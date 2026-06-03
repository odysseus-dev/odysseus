"""
email_thread_parser.py

Server-side port of the JS thread parser in static/js/emailLibrary.js.
Walks an email body (HTML or plain text) and returns a tree of reply turns
that the client can render directly without re-parsing.

Mirrors the rules from talon (mailgun) and email-reply-parser:
  - Multilingual "On <date>, <name> wrote:" attribution lines (20+ locales)
  - Outlook-style "From: ... Sent: ... Subject:" header blocks
  - "----- Original Message -----" delimiters
  - <blockquote> nesting (HTML)
  - "> " prefix nesting (plain text)

Returns a list of dicts:
    [
      {"level": 0, "body_html": "...", "meta": null},
      {"level": 1, "body_html": "...", "meta": "Alice <a@x> · May 5"},
      {"level": 2, "body_html": "...", "meta": "Bob <b@y> · May 4"},
      ...
    ]
where level 0 is the current reply, increasing levels = deeper in the chain.
"""

from __future__ import annotations

import html as _html
import re
from typing import Any

# Bump whenever the parser's output shape or splitting rules change. The
# cache layer wraps turns as {"v": THREAD_PARSER_VERSION, "turns": [...]}
# and treats anything with a different version as stale.
THREAD_PARSER_VERSION = 6

# ── Locale tables (same as static/js/emailLibrary.js _TALON_*) ──

_WROTE = (
    r"(?:wrote|écrit|escribió|scrisse|schrieb|skrev|schreef|napisał|написал|"
    r"napsal|написа|έγραψε|katselivat|napisao|написав|napisała|napisali|"
    r"hat geschrieben|kirjoitti|написала|escreveu)"
)
_FROM = (
    r"(?:From|Från|Von|De|Da|От|Od|Van|差出人|发件人|寄件人|Lähettäjä|"
    r"Avsender|Pošiljatelj|Frá)"
)
_SENT = (
    r"(?:Sent|Skickat|Gesendet|Envoy[ée]|Inviato|Enviado|Verzonden|Отправлено|"
    r"Wysłane|Date|送信日時|发送时间|寄件日期|Sendt|Lähetetty|Tarih|Datum|Data)"
)
_SUBJ = (
    r"(?:Subject|Ämne|Betreff|Objet|Oggetto|Asunto|Onderwerp|Тема|Temat|"
    r"件名|主题|主旨|Emne|Aihe|Konu)"
)
_TO = r"(?:To|Till|An|À|A|Voor|Para|Naar|Кому|Do|宛先|收件人|Komu)"
_CCBCC = r"(?:Cc|Bcc|Kopie|Skrytá kopie|Копия)"
_HDR_KEYS = rf"(?:{_FROM}|{_SENT}|{_SUBJ}|{_TO}|{_CCBCC}|Importance|Priority)"

_ORIG_RE = re.compile(
    r"(?:^|\n)[\s>]*[-_=]{3,}\s*(?:Original\s+Message|Forwarded\s+message|"
    r"Ursprüngliche\s+Nachricht|"
    r"Mensaje\s+original|Messaggio\s+originale|Message\s+d['’]origine|"
    r"Oorspronkelijk\s+bericht|Original\s+meddelande|原文|原始邮件|転送)"
    r"\s*[-_=]{3,}",
    re.IGNORECASE,
)
_WROTE_LINE_RE = re.compile(rf"^\s*On\s.+?\s{_WROTE}\s*:\s*$", re.IGNORECASE | re.MULTILINE)
# CJK-style attribution lines — Japanese Gmail / Yahoo Mail JP / etc.
# Examples (all valid):
#   2026年5月11日(月) 21:28 <alice@example.com>:
#   2026年5月11日 21:28 alice@example.com:
#   2026/05/11 21:28 <alice@example.com> のメッセージ:
#   2026年5月11日(月) 21:28に Alice Smith <alice@example.com> のメッセージ:
#   2026年5月11日 21:28、alice@example.com さんは書きました:
#   Alice さんは 2026/05/11 21:28 に書きました:
_CJK_ATTRIB_LINE_RE = re.compile(
    r"^\s*(?:"
        # date(weekday) time <email>:    (Gmail JP default)
        r"\d{4}[年/.-]\d{1,2}[月/.-]\d{1,2}日?(?:\s*[\(\(].+?[\)\)])?"
        r"\s+\d{1,2}:\d{2}(?:\s*[ＡＰAP][ＭM])?"
        r"(?:に|、|,)?\s*(?:.+?\s+)?[<＜]?[\w.+\-]+@[\w.\-]+\.[A-Za-z]{2,}[>＞]?"
        r"\s*(?:のメッセージ|さんは(?:書|お?書き)きました|wrote)?\s*[:：]\s*$"
        r"|"
        # 何々さんは 2026/05/11 21:28 に書きました:
        r".+?(?:さん|様)\s*(?:は|が)\s+\d{4}[年/.-]\d{1,2}[月/.-]\d{1,2}日?"
        r"(?:\s*[\(\(].+?[\)\)])?\s+\d{1,2}:\d{2}\s*(?:に)?\s*(?:書|お?書き)きました\s*[:：]\s*$"
        r"|"
        # Chinese "XXX 写道:" preceded by a date or address
        r".+?\s*写道\s*[:：]\s*$"
        r"|"
        # Korean "님이 작성:"
        r".+?\s*님이\s*작성(?:한\s*내용)?\s*[:：]\s*$"
    r")",
    re.MULTILINE,
)
_OUTLOOK_HEADER_RE = re.compile(
    rf"{_FROM}\s*:\s*[^\n]+\s*\n\s*(?:.+\n)?{_SENT}\s*:\s*[^\n]+\s*\n",
    re.IGNORECASE,
)
# Stop the From/Date captures at the next header key so they don't swallow
# the whole header block when whitespace has been normalised.
_FROM_STOP = rf"\s+(?:{_FROM}|{_SENT}|{_SUBJ}|{_TO}|{_CCBCC}|Importance|Priority)\s*:"
_DATE_STOP = rf"\s+(?:{_FROM}|{_SUBJ}|{_TO}|{_CCBCC}|Importance|Priority)\s*:"
_QUOTE_META_FROM = re.compile(
    rf"{_FROM}\s*:\s*(.+?)(?:(?={_FROM_STOP})|$)",
    re.IGNORECASE | re.DOTALL,
)
_QUOTE_META_DATE = re.compile(
    rf"{_SENT}\s*:\s*(.+?)(?:(?={_DATE_STOP})|$)",
    re.IGNORECASE | re.DOTALL,
)
# Greedy date capture so multi-comma dates ("Thu, May 7, 2026, 11:33 AM,")
# don't collapse to just the day. We let the comma + lazy author match
# back off to the LAST comma before "wrote:".
_GMAIL_ATTRIB = re.compile(
    rf"On\s+(.+),\s+([^,]+?)\s+{_WROTE}\s*:",
    re.IGNORECASE | re.DOTALL,
)


def _extract_quote_meta(text_or_html: str) -> str | None:
    """Pull a '<sender> · <date>' chip from a quoted block. Preserves
    angle-bracketed email addresses (`<foo@bar.com>`) so callers can
    identify the sender for chat-bubble alignment."""
    if not text_or_html:
        return None
    plain = re.sub(r"<style[\s\S]*?</style>", " ", text_or_html, flags=re.IGNORECASE)
    # Strip HTML tags, but keep <foo@bar> patterns since they carry the
    # sender's address that downstream consumers (bubble renderer) need.
    plain = re.sub(r"<(?![^@>\s]+@[^@>\s]+>)[^>]+>", " ", plain)
    plain = re.sub(r"&nbsp;", " ", plain, flags=re.IGNORECASE)
    plain = plain.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    plain = re.sub(r"\s+", " ", plain).strip()[:1500]

    f = _QUOTE_META_FROM.search(plain)
    d = _QUOTE_META_DATE.search(plain)
    if f and d:
        return f"{f.group(1).strip()} · {d.group(1).strip()[:80]}"
    g = _GMAIL_ATTRIB.search(plain)
    if g:
        date, who = g.group(1).strip(), g.group(2).strip()
        return f"{who} · {date}"
    # CJK attribution: "YYYY年MM月DD日(曜) HH:MM <email>:"
    cjk = re.search(
        r"(\d{4}[年/.-]\d{1,2}[月/.-]\d{1,2}日?(?:\s*[\(\(][^\)\)]+?[\)\)])?\s+\d{1,2}:\d{2}(?:\s*[ＡＰAP][ＭM])?)"
        r"\s*(?:に|、|,)?\s*"
        r"(?:(.+?)\s+)?"           # optional display name
        r"[<＜]?([\w.+\-]+@[\w.\-]+\.[A-Za-z]{2,})[>＞]?",
        plain,
    )
    if cjk:
        date = cjk.group(1).strip()
        who = (cjk.group(2) or cjk.group(3) or '').strip()
        return f"{who} · {date}" if who else date
    if f:
        return f.group(1).strip()
    if d:
        return d.group(1).strip()
    return None


# ── Plaintext path ──

# Outlook sometimes renders a one-line "conversation summary header" at
# the very top of a reply when the recipient's mail client copies it from
# the reading pane (whitespace gets squashed). Looks like:
#   "alice@example.comThursday, May 7, 2026 3:06 PM To: housekeeping <...> Subject: ..."
# or just:
#   "alice@example.comThursday, May 7, 2026 3:06 PM"
# Same info already lives in the envelope, so strip it.
_MASHED_HDR_RE = re.compile(
    r"^\s*[\w.+\-]+@[\w.\-]+\.[A-Za-z]{2,}"          # email address
    r"\s*"
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+"    # day name
    r"\S+\s+\d+,?\s*\d{4}\s+\d{1,2}:\d{2}"           # date + time
    r"(?:\s*[AP]M)?"                                  # optional AM/PM
    rf"(?:\s+{_TO}\s*:\s*[^\n]+(?:\s+{_SUBJ}\s*:\s*[^\n]*)?)?"  # optional To:/Subject:
    r"\s*(?:\n|$)",                                   # end of line
    re.IGNORECASE,
)


def _strip_mashed_header(text: str) -> str:
    if not text:
        return text
    m = _MASHED_HDR_RE.match(text)
    if not m:
        return text
    rest = text[m.end():]
    # Skip any blank lines that immediately follow the strip.
    rest = re.sub(r"^\s*\n+", "", rest)
    return rest


def _normalize_body(text: str) -> str:
    """Strip noise that mail clients (mostly Outlook) inject into the
    plaintext body but that adds no signal — duplicate <mailto:> link
    decorations, bracketed-URL annotations, repeated blank lines, and
    the mashed conversation-header at the top."""
    if not text:
        return text
    text = _strip_mashed_header(text)
    # Outlook appends `<mailto:foo@bar>` after every email address it
    # finds, and `<https://...>` after every URL. Both are duplicate
    # noise — they show the same target as the visible text. Drop them.
    text = re.sub(r"<mailto:[^<>\s]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<https?://[^<>\s]*>", "", text, flags=re.IGNORECASE)
    # Trim trailing whitespace (incl. NBSP / form-feed / tab) so blank
    # lines that mail clients fill with non-breaking spaces still count
    # as blank for the collapse step below.
    text = re.sub(r"[^\S\n]+(\n|$)", r"\1", text)
    # Collapse 3+ consecutive newlines (vertical-space soup) into 2.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _outlook_header_block_end(stripped: list[str], levels: list[int], start: int) -> int:
    """If lines[start..N] form an Outlook From/Sent/To/Subject header block
    at the same base level, return N (exclusive end). Otherwise return start.
    Requires a From: line followed within 5 lines by a Sent:/Date: line."""
    if start >= len(stripped):
        return start
    base = levels[start]
    first = stripped[start].strip()
    if not re.match(rf"^{_FROM}\s*:\s*\S", first, re.IGNORECASE):
        return start
    # Look ahead for the matching Sent:/Date: line at the same base level.
    found_sent = False
    j = start + 1
    while j < len(stripped) and j < start + 6 and levels[j] == base:
        nl = stripped[j].strip()
        if not nl:
            j += 1
            continue
        if re.match(rf"^{_SENT}\s*:", nl, re.IGNORECASE):
            found_sent = True
            break
        if not re.match(rf"^{_HDR_KEYS}\s*:", nl, re.IGNORECASE):
            return start  # something other than a header key — abort
        j += 1
    if not found_sent:
        return start
    # Consume header-key lines until we hit a non-header line OR a blank line.
    j = start + 1
    while j < len(stripped) and levels[j] == base:
        nl = stripped[j].strip()
        if not nl:
            j += 1
            break
        if re.match(rf"^{_HDR_KEYS}\s*:", nl, re.IGNORECASE):
            j += 1
            continue
        break
    return j


def _parse_plaintext(text: str) -> list[dict[str, Any]] | None:
    """Walk `>` quote prefix levels + inline attribution markers at any
    level. Each attribution event AND each `>`-level increment counts as
    one conversation step, with one important exception: an attribution
    marker IMMEDIATELY followed by a deeper `>` block is the same event
    as that `>` increase (the classic Gmail "On X wrote:\\n> quoted"
    pattern) and contributes only one step.

    Returns a flat list of {level, body_html, meta} or None when nothing
    quoted is detected."""
    if not text or len(text) > 200_000:
        return None
    text = _normalize_body(text)
    lines = text.splitlines()

    base_levels: list[int] = []
    stripped_lines: list[str] = []
    for line in lines:
        m = re.match(r"^((?:>\s?)+)", line)
        n = line[: m.end()].count(">") if m else 0
        base_levels.append(n)
        stripped_lines.append(re.sub(r"^(?:>\s?)+", "", line) if n > 0 else line)

    has_quotes = any(l > 0 for l in base_levels)
    has_attrib = bool(
        _WROTE_LINE_RE.search(text) or _ORIG_RE.search(text)
        or _OUTLOOK_HEADER_RE.search(text) or _CJK_ATTRIB_LINE_RE.search(text)
    )
    if not has_quotes and not has_attrib:
        return None

    turns: list[dict[str, Any]] = []
    buf: list[str] = []
    cur_level = 0
    pending_meta: str | None = None
    # depth_at_base[B] = the effective conversation depth recorded the
    # last time we were at `>` base level B. Used to restore depth when
    # the > nesting decreases (we hop back to a shallower base).
    depth_at_base: dict[int, int] = {0: 0}
    depth = 0
    prev_base = 0

    def lookahead_content_base(start_idx: int) -> int | None:
        j = start_idx
        while j < len(lines) and not stripped_lines[j].strip():
            j += 1
        return base_levels[j] if j < len(lines) else None

    def flush() -> None:
        # `buf` is only mutated via .clear() / .append() in the enclosing
        # scope, never re-assigned, so it doesn't need `nonlocal`.
        nonlocal pending_meta
        if not buf:
            return
        body = "\n".join(buf).rstrip()
        if body or cur_level > 0:
            turns.append({
                "level": cur_level,
                "body_html": _escape_to_html(body),
                "meta": pending_meta,
            })
        buf.clear()
        pending_meta = None

    i = 0
    while i < len(lines):
        base = base_levels[i]
        stripped = stripped_lines[i]

        # `>` base level change → flush current turn, then step depth.
        if base > prev_base:
            flush()
            for b in range(prev_base + 1, base + 1):
                depth += 1
                depth_at_base[b] = depth
            cur_level = depth
        elif base < prev_base:
            flush()
            depth = depth_at_base.get(base, base)
            for b in list(depth_at_base.keys()):
                if b > base:
                    del depth_at_base[b]
            cur_level = depth
        prev_base = base

        is_gmail = bool(re.match(rf"^\s*On\s.+?\s{_WROTE}\s*:\s*$", stripped, re.IGNORECASE))
        is_cjk = bool(_CJK_ATTRIB_LINE_RE.match(stripped))
        is_orig = bool(_ORIG_RE.search("\n" + stripped))
        outlook_end = _outlook_header_block_end(stripped_lines, base_levels, i)
        is_outlook = outlook_end > i

        if is_gmail or is_cjk or is_orig or is_outlook:
            # Collect the full attribution text for meta extraction.
            attrib_end = outlook_end if is_outlook else (i + 1)
            meta_text = "\n".join(stripped_lines[i:attrib_end])

            # "-----Original Message-----" is almost always immediately
            # followed by an Outlook From:/Sent: header — fold that into
            # the SAME attribution event so we don't double-bump.
            if is_orig:
                j = attrib_end
                while j < len(lines) and base_levels[j] == base and not stripped_lines[j].strip():
                    j += 1
                if j < len(lines) and base_levels[j] == base:
                    oe2 = _outlook_header_block_end(stripped_lines, base_levels, j)
                    if oe2 > j:
                        meta_text = meta_text + "\n" + "\n".join(stripped_lines[j:oe2])
                        attrib_end = oe2

            # If the next content line lives at a deeper > base, the
            # upcoming `>` increase will be the depth step — suppress
            # our own bump so we don't double up. Otherwise, this
            # attribution IS the step.
            next_base = lookahead_content_base(attrib_end)
            flush()
            if next_base is not None and next_base > base:
                pending_meta = _extract_quote_meta(meta_text) or meta_text.strip().splitlines()[0]
            else:
                depth += 1
                depth_at_base[base] = depth
                cur_level = depth
                pending_meta = _extract_quote_meta(meta_text) or meta_text.strip().splitlines()[0]
            i = attrib_end
            continue

        buf.append(stripped)
        i += 1

    flush()

    if not turns or (len(turns) == 1 and turns[0]["level"] == 0):
        return None
    return turns


def _escape_to_html(text: str) -> str:
    """Conservative plaintext → HTML: escape, then linkify URLs and convert
    newlines to <br>."""
    if not text:
        return ""
    out = _html.escape(text)
    out = re.sub(
        r"(https?://[^\s<>\"]+)",
        lambda m: f'<a href="{m.group(1)}" target="_blank" rel="noopener">{m.group(1)}</a>',
        out,
    )
    return out.replace("\n", "<br>")


# ── HTML path (BeautifulSoup) ──

def _is_quote_container(tag) -> bool:
    """Return True if a BeautifulSoup tag is a known quote-container element.
    Covers Gmail (`gmail_quote`), Apple Mail (`type="cite"`), Yahoo
    (`yahoo_quoted`), Outlook (`divRplyFwdMsg`, `OutlookMessageHeader`,
    `gmail_attr` precedes a quote in some forwards), and the standard
    `<blockquote>`."""
    if tag is None:
        return False
    name = (getattr(tag, "name", None) or "").lower()
    if name == "blockquote":
        return True
    cls = " ".join(tag.get("class") or []).lower() if hasattr(tag, "get") else ""
    if "gmail_quote" in cls or "yahoo_quoted" in cls or "moz-cite-prefix" in cls:
        return True
    if "outlookmessageheader" in cls or "wordsection1" in cls:
        return True
    if (tag.get("id") if hasattr(tag, "get") else "") == "divRplyFwdMsg":
        return True
    typ = (tag.get("type") if hasattr(tag, "get") else "") or ""
    if name == "div" and typ.lower() == "cite":
        return True
    return False


def _parse_html(html: str) -> list[dict[str, Any]] | None:
    """Walk top-level quote-container elements and recurse into nested ones.
    Returns None if no quote markers are present. Recognises <blockquote>
    plus the Gmail / Apple Mail / Yahoo / Outlook / Thunderbird wrappers
    that don't use <blockquote>."""
    if not html or len(html) > 200_000:
        return None
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return None  # bs4 not installed → caller falls back to plaintext / client parse

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None

    # Find all quote containers, then keep only the top-level ones (those
    # whose nearest ancestor that's also a quote container is None).
    all_quotes = [t for t in soup.find_all(True) if _is_quote_container(t)]
    if not all_quotes:
        return None

    def has_quote_ancestor(t) -> bool:
        p = t.parent
        while p is not None:
            if _is_quote_container(p):
                return True
            p = p.parent
        return False

    tops = [t for t in all_quotes if not has_quote_ancestor(t)]
    if not tops:
        return None

    turns: list[dict[str, Any]] = []

    # Collect the new-reply content from OUTSIDE the quote containers.
    # Most replies are top-posted (head), but Japanese / formal emails are
    # frequently bottom-posted (tail). Some users do both. We combine head
    # and tail into a single level-0 turn so the new content always shows
    # first, regardless of source-order position.
    parent_children = list(tops[0].parent.children if tops[0].parent else [])

    head_nodes = []
    for sib in parent_children:
        if sib is tops[0]:
            break
        head_nodes.append(sib)

    # Tail = everything after the LAST top-level quote at this parent level
    last_top = tops[-1]
    tail_nodes = []
    after_last = False
    for sib in parent_children:
        if sib is last_top:
            after_last = True
            continue
        # Skip any other top-level quotes between (they get walked below)
        if after_last and sib in tops:
            continue
        if after_last:
            tail_nodes.append(sib)

    def _strip_trailing_attribution(html_chunk: str) -> str:
        text = re.sub(r"<[^>]+>", " ", html_chunk)
        if not (_WROTE_LINE_RE.search(text) or _ORIG_RE.search(text) or _CJK_ATTRIB_LINE_RE.search(text)):
            return html_chunk
        html_chunk = re.sub(
            rf"(?:<br\s*/?>|</p>|</div>|\n)?\s*On\s.+?\s{_WROTE}\s*:\s*(?:</[^>]+>)*\s*$",
            "",
            html_chunk,
            flags=re.IGNORECASE | re.DOTALL,
        )
        html_chunk = re.sub(
            r"(?:<br\s*/?>|</p>|</div>|\n)?\s*"
            r"(?:\d{4}[年/.-]\d{1,2}[月/.-]\d{1,2}日?(?:\s*[\(\(][^\)\)]+?[\)\)])?"
            r"\s+\d{1,2}:\d{2}(?:\s*[ＡＰAP][ＭM])?(?:に|、|,)?"
            r"\s*(?:.+?\s+)?[<＜]?[\w.+\-]+@[\w.\-]+\.[A-Za-z]{2,}[>＞]?"
            r"\s*(?:のメッセージ|さんは(?:書|お?書き)きました|wrote)?\s*[:：]"
            r"\s*(?:</[^>]+>)*\s*$",
            "",
            html_chunk,
            flags=re.DOTALL,
        )
        return html_chunk

    head_html = _strip_trailing_attribution("".join(str(n) for n in head_nodes))
    tail_html = "".join(str(n) for n in tail_nodes)

    # Stitch head + tail. Tail (bottom-posted reply) goes first because
    # that's the most-recent / most-relevant content; head (which may just
    # be empty or a forwarded preamble) follows.
    parts = []
    if tail_html.strip(): parts.append(tail_html.strip())
    if head_html.strip(): parts.append(head_html.strip())
    if parts:
        turns.append({
            "level": 0,
            "body_html": "<br><br>".join(parts) if len(parts) > 1 else parts[0],
            "meta": None,
        })

    def _walk(node, level: int):
        meta_from_node = _extract_quote_meta(str(node))
        # Recurse into nested quote containers inside this one, then strip
        # them so the body of THIS turn doesn't include them.
        nested = [t for t in node.find_all(True, recursive=True) if _is_quote_container(t)]
        # Keep only direct-quote descendants (no other quote container between)
        def has_quote_between(child, ancestor) -> bool:
            p = child.parent
            while p is not None and p is not ancestor:
                if _is_quote_container(p):
                    return True
                p = p.parent
            return False
        direct_nested = [n for n in nested if not has_quote_between(n, node)]
        for n in list(direct_nested):
            n.extract()
        body_html = node.decode_contents()

        # Collapse "wrapper-only" quote containers: if the only remaining
        # content of this node (after pulling out nested quotes) is an
        # attribution line, don't emit a separate turn for it. Instead,
        # pass the attribution down as meta for the directly-nested child.
        # Without this collapse, gmail_quote_container produces a phantom
        # bubble that contains just the JP/EN attribution line.
        body_text = re.sub(r"<[^>]+>", " ", body_html).strip()
        body_text = _html.unescape(body_text)
        body_text_collapsed = re.sub(r"\s+", " ", body_text).strip()
        is_attrib_only = bool(body_text_collapsed) and (
            _CJK_ATTRIB_LINE_RE.match(body_text_collapsed)
            or re.match(rf"^\s*On\s.+?\s{_WROTE}\s*:\s*$", body_text_collapsed, re.IGNORECASE)
            or _OUTLOOK_HEADER_RE.match(body_text_collapsed)
        )
        if is_attrib_only and len(direct_nested) == 1:
            # Skip emitting this wrapper. Pass attribution as meta for child.
            child_meta = meta_from_node or body_text_collapsed
            # Recurse into child as the SAME level (replacing this wrapper)
            _walk_with_meta(direct_nested[0], level, child_meta)
            return

        turns.append({"level": level, "body_html": body_html, "meta": meta_from_node})
        for n in direct_nested:
            _walk(n, level + 1)

    def _walk_with_meta(node, level: int, forced_meta: str):
        """Variant that uses a passed-in meta when the node's own meta is empty."""
        meta_from_node = _extract_quote_meta(str(node)) or forced_meta
        nested = [t for t in node.find_all(True, recursive=True) if _is_quote_container(t)]
        def has_quote_between(child, ancestor) -> bool:
            p = child.parent
            while p is not None and p is not ancestor:
                if _is_quote_container(p):
                    return True
                p = p.parent
            return False
        direct_nested = [n for n in nested if not has_quote_between(n, node)]
        for n in list(direct_nested):
            n.extract()
        body_html = node.decode_contents()
        turns.append({"level": level, "body_html": body_html, "meta": meta_from_node})
        for n in direct_nested:
            _walk(n, level + 1)

    for bq in tops:
        _walk(bq, 1)

    if not turns:
        return None
    return turns


def parse_thread(body_html: str | None, body_text: str | None) -> list[dict[str, Any]] | None:
    """Public entry point. Prefer HTML when available, else plaintext.
    Returns None if no quoted material found (caller renders flat)."""
    if isinstance(body_html, str) and body_html:
        out = _parse_html(body_html)
        if out:
            return out
    if isinstance(body_text, str) and body_text:
        return _parse_plaintext(body_text)
    return None
