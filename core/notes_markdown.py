"""Shared helpers for the markdown-native Note model.

A note is one markdown document (`Note.content`). Checklists live inside it as
GitHub-style task lines (`- [ ] todo` / `- [x] done`) and images as
`![](/api/upload/<id>)`. These helpers decide what counts as a task line, how
tasks are numbered, toggled and rewritten, and how the legacy `items` /
`image_url` columns fold into content.

The JS mirror is `frontend/src/features/notes/domain/markdownTasks.js`, and the
interactive task renderer in `static/js/markdown.js` numbers tasks the same way.
All three are pinned against `tests/fixtures/notes_task_vectors.json`, so
toggle-by-index means the same line everywhere.

Stdlib only: imported by the DB migration, the routes, the agent tool and the
CLI without import cycles.
"""

import json
import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Tuple

# A task line: optional indent, a bullet (-, *, +), a [ ]/[x] box, then text.
# Group 1 = leading whitespace, 2 = bullet, 3 = box char, 4 = the item text.
# The separator before the text is `[ \t]?`, not `\s?`: helpers run line by
# line, and keeping it tab/space-only keeps the JS regexes identical.
_TASK_RE = re.compile(r"^([ \t]*)([-*+])[ \t]+\[([ xX])\][ \t]?([^\n]*)$")

# A fenced-code delimiter: optional indent then 3+ backticks or tildes.
# Task-looking lines inside a fence are code samples, not checklist items.
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")

# Markdown image, first capture is the URL.
_IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

# A line that is only an untitled image, the way `fold_image_url` writes the
# legacy single image. Group 1 = the URL.
_BARE_IMAGE_LINE_RE = re.compile(r"^[ \t]*!\[\]\(([^)\s]+)\)[ \t]*$")

# Characters a markdown link destination cannot hold unescaped. A legacy
# `image_url` is written percent-encoded so the image parses back as the
# same URL and a repeated write is recognised instead of stacked.
_URL_ESCAPES = {" ": "%20", "\t": "%09", "(": "%28", ")": "%29", '"': "%22", "<": "%3C", ">": "%3E"}

# The note colour palette. `COLORS` in frontend/src/features/notes/ui/colors.js
# offers the same names (tests/test_note_color.py keeps them in step).
NOTE_COLORS = ("red", "orange", "yellow", "green", "blue", "purple")

# A picture background: `bg:` and an upload. Older clients wrote the upload
# with this site's origin in front, so an http(s) origin is accepted and
# dropped; what is stored is always the path.
_NOTE_BG_RE = re.compile(
    r"^bg:(?:https?://[A-Za-z0-9.\-]+(?::\d{1,5})?)?(/api/upload/[0-9a-fA-F]{32}(?:\.[A-Za-z0-9]{1,16})?)$"
)


def normalize_note_color(value) -> Optional[str]:
    """A note colour as stored: "" (none), a palette name, or `bg:/api/upload/<id>`.

    None means "not sent" and stays None. Anything else raises ValueError:
    the colour ends up in a class name and a style on every card that shows
    the note, including a collaborator's.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return ""
    if text.lower() in NOTE_COLORS:
        return text.lower()
    m = _NOTE_BG_RE.match(text)
    if m:
        return "bg:" + m.group(1)
    raise ValueError(
        "color must be one of " + ", ".join(NOTE_COLORS)
        + ", empty for none, or bg:/api/upload/<id> for a picture"
    )


# Two spaces per nesting level when task lines are emitted.
_INDENT_UNIT = 2
_MAX_INDENT = 3

# Note types that are stored modes rather than a description of the content.
# Goal notes keep their progress/Today views and a drawing keeps its type and
# gallery link, so neither is derived from content.
STORED_NOTE_TYPES = ("goal", "draw")


def _indent_level(ws: str) -> int:
    """Leading whitespace -> nesting level (a tab counts as one level)."""
    width = sum(_INDENT_UNIT if ch == "\t" else 1 for ch in ws)
    return width // _INDENT_UNIT


def _coerce_indent(value) -> int:
    """Best-effort nesting level for a legacy item's `indent` field.

    Legacy rows were free-form JSON, so `indent` can be missing, null, a
    float, or junk like "oops". A bad value must never abort a conversion, so
    anything non-coercible degrades to 0.
    """
    try:
        return min(_MAX_INDENT, max(0, int(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _item_text(item) -> str:
    """Single-line text for a legacy item; a newline would split the task."""
    if not isinstance(item, dict):
        return ""
    return " ".join(str(item.get("text") or "").split())


def _migration_item_text(item) -> str:
    """A legacy item's text as the migration writes it: newlines become
    spaces so the item stays one task line, and nothing else is changed."""
    if not isinstance(item, dict):
        return ""
    return re.sub(r"\r\n|[\r\n]", " ", str(item.get("text") or "")).strip()


def _item_done(item) -> bool:
    return bool(isinstance(item, dict) and (item.get("done") or item.get("checked")))


def _clean_items(items) -> List[Dict]:
    """Keep the legacy items that would produce a task line, in order."""
    if not isinstance(items, list):
        return []
    return [it for it in items if _item_text(it)]


def _split_lines(content: str) -> List[str]:
    """Split on newlines only; CRLF counts as one break, a final newline adds no line.

    Not `str.splitlines()`: that also splits on U+2028, form feeds and other
    separators the JS mirror would not, and task numbering has to agree.
    """
    lines = content.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _iter_lines_outside_fences(content: str):
    """Yield (lineno, raw) for lines that are not fenced code.

    A fence opens on the first backtick or tilde run (3+ chars) and closes on
    the next delimiter of the same character that is at least as long
    (CommonMark). A delimiter of the other character inside an open fence is
    code content. An unclosed fence runs to the end of the document. Fence
    delimiter lines themselves are never yielded.
    """
    fence_char: Optional[str] = None
    fence_len = 0
    for lineno, raw in enumerate(_split_lines(content)):
        m = _FENCE_RE.match(raw)
        if m:
            marker = m.group(1)
            if fence_char is None:
                fence_char, fence_len = marker[0], len(marker)
                continue
            if marker[0] == fence_char and len(marker) >= fence_len:
                fence_char, fence_len = None, 0
                continue
        if fence_char is not None:
            continue
        yield lineno, raw


def _iter_task_matches(content: str):
    for lineno, raw in _iter_lines_outside_fences(content):
        m = _TASK_RE.match(raw)
        if m:
            yield lineno, m


def _join(lines: List[str], original: str) -> str:
    """Join lines back, keeping the original trailing newline if it had one."""
    joined = "\n".join(lines)
    if original.endswith("\n") and lines:
        joined += "\n"
    return joined


def _task_line(text: str, done: bool, indent: int, bullet: str = "-", ws: Optional[str] = None) -> str:
    lead = ws if ws is not None else " " * (_INDENT_UNIT * indent)
    return f"{lead}{bullet} [{'x' if done else ' '}] {text}"


def parse_task_lines(content: Optional[str]) -> List[Dict]:
    """Return the task lines in `content`, in document order.

    Each entry is {index, text, done, indent, line}: `index` is the 0-based
    ordinal among task lines outside fences (what the API, the agent and the
    UI toggle by) and `line` is the 0-based source line number.
    """
    if not content:
        return []
    out: List[Dict] = []
    for lineno, m in _iter_task_matches(content):
        out.append({
            "index": len(out),
            "text": m.group(4).strip(),
            "done": m.group(3).lower() == "x",
            "indent": _indent_level(m.group(1)),
            "line": lineno,
        })
    return out


def has_tasks(content: Optional[str]) -> bool:
    """True if `content` has at least one task line outside fenced code."""
    if not content:
        return False
    return any(True for _ in _iter_task_matches(content))


def has_non_task_text(content: Optional[str]) -> bool:
    """True if `content` holds anything besides task lines and blank lines.

    Fenced code counts as text: a fence is something the user wrote.
    """
    if not content:
        return False
    task_lines = {lineno for lineno, _ in _iter_task_matches(content)}
    return any(
        raw.strip() and lineno not in task_lines
        for lineno, raw in enumerate(_split_lines(content))
    )


def strip_task_lines(content: Optional[str]) -> str:
    """`content` with its task lines removed (fenced task samples stay)."""
    if not content:
        return ""
    task_lines = {lineno for lineno, _ in _iter_task_matches(content)}
    kept = [raw for lineno, raw in enumerate(_split_lines(content)) if lineno not in task_lines]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def toggle_task(content: Optional[str], index: int) -> Tuple[str, Dict]:
    """Flip the done state of the `index`-th task line (0-based).

    Returns (new_content, toggled_item). Raises IndexError if there is no task
    line at that ordinal. Indent, bullet and text are left as they were.
    """
    text = content or ""
    if index < 0:
        raise IndexError(f"no task line at index {index}")
    lines = _split_lines(text)
    for seen, (lineno, m) in enumerate(_iter_task_matches(text)):
        if seen != index:
            continue
        now_done = m.group(3).lower() != "x"
        raw = lines[lineno]
        box_at = raw.index("[", len(m.group(1)) + 1)
        lines[lineno] = raw[:box_at] + ("[x]" if now_done else "[ ]") + raw[box_at + 3:]
        item = {
            "index": index,
            "text": m.group(4).strip(),
            "done": now_done,
            "indent": _indent_level(m.group(1)),
            "line": lineno,
        }
        return _join(lines, text), item
    raise IndexError(f"no task line at index {index}")


def items_to_markdown(items: Optional[Iterable[Dict]]) -> str:
    """Render legacy structured items ([{text, done, indent}]) as task lines."""
    return "\n".join(
        _task_line(_item_text(it), _item_done(it), _coerce_indent(it.get("indent")))
        for it in _clean_items(list(items or []))
    )


def _append_block(content: str, block: str) -> str:
    """Append a markdown block, separated from existing text by a blank line."""
    if not block:
        return content
    if not content.strip():
        return block
    return content.rstrip() + "\n\n" + block


def _insert_task_lines(content: str, new_lines: List[str]) -> str:
    """Insert task lines right after the last task line, or append them.

    When they are appended, a code fence left open at the end is closed
    first, so the new lines are tasks and not code.
    """
    if not new_lines:
        return content
    last = None
    for lineno, _ in _iter_task_matches(content):
        last = lineno
    if last is None:
        fence = _open_fence_at_end(content)
        if fence:
            content = content.rstrip() + "\n" + fence
        return _append_block(content, "\n".join(new_lines))
    lines = _split_lines(content)
    lines[last + 1:last + 1] = new_lines
    return _join(lines, content)


def _newline_of(content: str) -> str:
    """The line break `content` uses: CRLF when it has any, else LF."""
    return "\r\n" if "\r\n" in content else "\n"


def _open_fence_at_end(content: str) -> Optional[str]:
    """The delimiter that would close a fence still open at the end of `content`."""
    fence_char: Optional[str] = None
    fence_len = 0
    for raw in _split_lines(content):
        m = _FENCE_RE.match(raw)
        if not m:
            continue
        marker = m.group(1)
        if fence_char is None:
            fence_char, fence_len = marker[0], len(marker)
        elif marker[0] == fence_char and len(marker) >= fence_len:
            fence_char, fence_len = None, 0
    return fence_char * fence_len if fence_char else None


def _append_block_keeping_content(content: str, lines: List[str]) -> str:
    """Append `lines` as a block after `content` without rewriting it.

    The existing text keeps its bytes and line breaks: the block is joined
    with the content's own newline and separated by one blank line. A code
    fence left open at the end is closed first, so the block is not swallowed
    as code.
    """
    if not lines:
        return content
    if not content.strip():
        return "\n".join(lines)
    nl = _newline_of(content)
    fence = _open_fence_at_end(content)
    out = content
    if fence:
        out += ("" if out.endswith(("\n", "\r")) else nl) + fence
    if out.endswith(nl + nl):
        sep = ""
    elif out.endswith(nl):
        sep = nl
    else:
        sep = nl + nl
    return out + sep + nl.join(lines)


def _insert_after_last_task_keeping_content(content: str, lines: List[str]) -> str:
    """Insert `lines` right after the last task line without rewriting the rest."""
    if not lines:
        return content
    last = None
    for lineno, _ in _iter_task_matches(content):
        last = lineno
    if last is None:
        return _append_block_keeping_content(content, lines)
    parts = content.split("\n")
    nl = "\r\n" if parts[last].endswith("\r") else "\n"
    pos = sum(len(part) + 1 for part in parts[:last + 1])
    if pos > len(content):  # the last task line is the final, unterminated line
        return content + nl + nl.join(lines)
    return content[:pos] + "".join(line + nl for line in lines) + content[pos:]


def merge_items_into_content(content: Optional[str], items: Optional[List[Dict]]) -> str:
    """Fold legacy `items` into `content` for the one-time migration.

    An item is already present when an unused task line in `content` has the
    same text and the same done state; every other item is added as a task
    line, so two identical items and an item whose tick differs from the
    content's copy all survive. Content is never rewritten: its bytes and
    line breaks stay as they were and a legacy row can only gain lines. Task
    lines inside fenced code do not count as present (they are code), so a
    note whose only `- [ ]` lines are a fenced sample still gets its real
    items, and a fence left open at the end is closed before them.
    """
    base = content or ""
    present = Counter((t["text"], t["done"]) for t in parse_task_lines(base))
    missing = []
    for it in _clean_items(items):
        key = (_migration_item_text(it), _item_done(it))
        if present[key]:
            present[key] -= 1
            continue
        missing.append(it)
    return _insert_after_last_task_keeping_content(base, [
        _task_line(_migration_item_text(it), _item_done(it), _coerce_indent(it.get("indent")))
        for it in missing
    ])


def apply_items_to_content(content: Optional[str], items: Optional[List[Dict]]) -> str:
    """Write an `items` array into `content` (compat path for `items` writers).

    The n-th item rewrites the n-th task line (text, done, indent); a line
    whose values did not change is kept byte-for-byte. Extra items are
    inserted after the last task line; task lines past the end of the array
    are dropped. Text that is not a task line, and fenced code, are untouched.
    `items=None` means "not supplied" and returns `content` as-is.
    """
    base = content or ""
    if items is None:
        return base
    wanted = _clean_items(items)
    matches = list(_iter_task_matches(base))
    lines = _split_lines(base)
    drop = set()
    for n, (lineno, m) in enumerate(matches):
        if n >= len(wanted):
            drop.add(lineno)
            continue
        it = wanted[n]
        text, done, indent = _item_text(it), _item_done(it), _coerce_indent(it.get("indent"))
        same_indent = _indent_level(m.group(1)) == indent
        if text == m.group(4).strip() and done == (m.group(3).lower() == "x") and same_indent:
            continue
        ws = m.group(1) if same_indent else None
        lines[lineno] = _task_line(text, done, indent, bullet=m.group(2), ws=ws)
    if drop:
        kept = [raw for lineno, raw in enumerate(lines) if lineno not in drop]
        base = _join(kept, base)
    else:
        base = _join(lines, base)
    extra = wanted[len(matches):]
    return _insert_task_lines(base, [
        _task_line(_item_text(it), _item_done(it), _coerce_indent(it.get("indent")))
        for it in extra
    ])


def keep_task_lines(previous: Optional[str], content: Optional[str]) -> str:
    """`content` for a write that sent only text, with `previous`'s task lines kept.

    Agents and API clients read a note's prose and its tasks separately, and
    write the prose back on its own. When the new text has no task lines of
    its own, the old ones are put back (after the text) instead of being
    deleted; a writer that means to clear them sends an empty item list.
    """
    new = content or ""
    if has_tasks(new) or not has_tasks(previous):
        return new
    lines = _split_lines(previous or "")
    return _insert_task_lines(new, [lines[lineno] for lineno, _ in _iter_task_matches(previous)])


def append_task_line(content: Optional[str], text: str, indent=0) -> Tuple[str, Dict]:
    """Add one open task after the last task line. Returns (content, item)."""
    clean = " ".join(str(text or "").split())
    if not clean:
        raise ValueError("task text is empty")
    level = _coerce_indent(indent)
    updated = _insert_task_lines(content or "", [_task_line(clean, False, level)])
    tasks = parse_task_lines(updated)
    item = next(
        (t for t in reversed(tasks) if t["text"] == clean and not t["done"] and t["indent"] == level),
        tasks[-1],
    )
    return updated, item


def pending_task_summary(content: Optional[str], limit: int = 8) -> str:
    """"Pending (N):" plus up to `limit` open tasks, or "" when none are open.

    Task text is reminder text (`reminder_text`): a task that is only an image
    is not listed.
    """
    pending = [text for text in (reminder_text(t["text"]) for t in parse_task_lines(content) if not t["done"]) if text]
    if not pending:
        return ""
    shown = "\n".join(f"- {text}" for text in pending[:limit])
    extra = f"\n...and {len(pending) - limit} more" if len(pending) > limit else ""
    return f"Pending ({len(pending)}):\n{shown}{extra}"


def load_legacy_items(raw) -> Optional[List[Dict]]:
    """Decode a legacy `items` column value; None when absent or unreadable."""
    if not raw:
        return None
    if isinstance(raw, list):
        return raw
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return items if isinstance(items, list) else None


# The fields that are not columns: checklists (`items`, the tool's
# `checklist_items`) and the legacy `image_url` are lines inside `content`. A
# null on one has no meaning of its own: "clear" would wipe the note's body
# and "ignore" would answer success for a write that did nothing. Writers
# refuse it and say how to clear instead.
NULL_FOLDED_FIELD_HINTS = {
    "content": "send an empty string to clear the note",
    "items": "send [] to remove the checklist",
    "checklist_items": "send [] to remove the checklist",
    "image_url": "remove its image line from content",
}


def null_folded_field_error(values: Dict, sent: Optional[Iterable[str]] = None) -> Optional[str]:
    """The refusal for a write that sends null on a folded field, else None.

    `values` maps field names to what was sent; `sent` names the fields the
    request actually carried (a pydantic model's `model_fields_set`), for
    callers whose missing keys also read as None. Without it, a key's
    presence in `values` decides.
    """
    keys = values.keys() if sent is None else sent
    for field in NULL_FOLDED_FIELD_HINTS:
        if field in keys and values.get(field) is None:
            return f"{field} cannot be null; {NULL_FOLDED_FIELD_HINTS[field]}."
    return None


def effective_content(content: Optional[str], legacy_items=None, legacy_image_url=None) -> str:
    """Content with any not-yet-migrated legacy `items` and `image_url` folded in.

    The startup migration clears both columns, but a row it skipped (malformed
    JSON elsewhere in the row, a crash mid-boot, or a database that is not
    SQLite, where the migration does not run) keeps them. Readers use this so
    such a row still shows its checklist and its image the way the migration
    would have stored them, instead of silently losing them.
    """
    out = content or ""
    items = load_legacy_items(legacy_items)
    if items:
        out = merge_items_into_content(out, items)
    if legacy_image_url:
        out = fold_image_url(out, legacy_image_url)
    return out


def fold_legacy_columns(note) -> None:
    """Move a row the startup migration skipped into content before a write.

    `note` is any object with `content`, `items` and `image_url` attributes
    (the ORM row). The columns are cleared once their values are in content,
    exactly as the migration would leave the row. Unreadable `items` are left
    in place for the migration to retry, and so is `image_url` on such a row,
    so nothing the migration still needs is thrown away.
    """
    raw_items = getattr(note, "items", None)
    if raw_items and load_legacy_items(raw_items) is None:
        return
    image_url = (getattr(note, "image_url", None) or "").strip()
    if not raw_items and not image_url:
        return
    note.content = effective_content(note.content, raw_items, image_url)
    note.items = None
    note.image_url = None


def derive_note_type(stored_type: Optional[str], content: Optional[str]) -> str:
    """`checklist` for a pure task list, `note` otherwise; goal/draw are kept."""
    if stored_type in STORED_NOTE_TYPES:
        return stored_type
    if has_tasks(content) and not has_non_task_text(content):
        return "checklist"
    return "note"


def image_urls(content: Optional[str]) -> List[str]:
    """Markdown image URLs in `content`, outside fenced code, in order."""
    if not content:
        return []
    urls: List[str] = []
    for _, raw in _iter_lines_outside_fences(content):
        urls.extend(m.group(1) for m in _IMAGE_RE.finditer(raw))
    return urls


def markdown_image_url(url: Optional[str]) -> str:
    """`url` as a markdown image destination: trimmed, with the characters a
    destination cannot hold percent-encoded."""
    return "".join(_URL_ESCAPES.get(ch, ch) for ch in (url or "").strip())


def legacy_image_url(content: Optional[str]) -> Optional[str]:
    """The note's single legacy image: the last line that is only an untitled
    image, which is what `fold_image_url` writes. None when there is none."""
    found = None
    for _, raw in _iter_lines_outside_fences(content or ""):
        m = _BARE_IMAGE_LINE_RE.match(raw)
        if m:
            found = m.group(1)
    return found


def remove_image_line(content: Optional[str], url: Optional[str]) -> str:
    """Drop the last line that is only `![](url)`; other text is untouched."""
    base = content or ""
    target = markdown_image_url(url)
    if not target:
        return base
    parts = base.split("\n")
    hit = None
    for lineno, raw in _iter_lines_outside_fences(base):
        m = _BARE_IMAGE_LINE_RE.match(raw)
        if m and m.group(1) == target:
            hit = lineno
    if hit is None:
        return base
    # Take the blank separator line the fold wrote before it, too, when the
    # image line sat at the end or between two blank lines.
    start = hit
    blank = lambda i: 0 <= i < len(parts) and not parts[i].strip()
    if blank(hit - 1) and (hit + 1 >= len(parts) or blank(hit + 1)):
        start = hit - 1
    out = "\n".join(parts[:start] + parts[hit + 1:])
    return out if out.strip() else ""


def fold_image_url(content: Optional[str], url: Optional[str], replace: Optional[str] = None) -> str:
    """Put a legacy `image_url` into content as `![](url)`.

    No-op when `url` is empty or content already references it, so a repeated
    write never stacks a second copy (URLs with spaces or parentheses are
    written percent-encoded and still match). With `replace`, an existing
    `](replace)` reference is swapped for the new URL instead of appending a
    second image (a re-saved drawing, or a new `image_url` on a note).
    """
    base = content or ""
    url = markdown_image_url(url)
    if not url or url in image_urls(base):
        return base
    replace = markdown_image_url(replace)
    if replace and replace != url:
        needle = f"]({replace})"
        if needle in base:
            return base.replace(needle, f"]({url})", 1)
    return _append_block_keeping_content(base, [f"![]({url})"])


# Markup a reminder (email, ntfy, a desktop notification) cannot show:
# images are dropped, links keep their text, heading and quote markers go.
# Fenced code is content and stays as written.
_REMINDER_IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")
_REMINDER_LINK_RE = re.compile(r"(?<!!)\[([^\]\n]*)\]\([^)\n]*\)")
_REMINDER_LINE_MARK_RE = re.compile(r"^[ \t]{0,3}(?:#{1,6}[ \t]+|>[ \t]?)")
_REMINDER_FENCE_RE = re.compile(r"^[ \t]{0,3}(?:`{3,}|~{3,})")


def reminder_text(text: Optional[str]) -> str:
    """`text` as plain words for a reminder: no image markdown, links as their text."""
    lines = []
    in_fence = False
    for line in (text or "").split("\n"):
        if _REMINDER_FENCE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            line = _REMINDER_IMAGE_RE.sub("", line)
            line = _REMINDER_LINK_RE.sub(lambda m: m.group(1), line)
            line = _REMINDER_LINE_MARK_RE.sub("", line)
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def reminder_body(content: Optional[str], text_limit: int = 400, task_limit: int = 8) -> str:
    """Reminder text for a note: its prose (task lines removed) plus open tasks.

    Shared by the fire-reminder route and the background ping scanner so the
    two cannot disagree about what a checklist reminder says; the browser's
    local notification follows the same rules (`reminderBody` in the notes
    domain, checked against tests/fixtures/notes_reminder_vectors.json).
    """
    tasks = parse_task_lines(content)
    if not tasks:
        return reminder_text(content)[:text_limit].strip()
    prose = reminder_text(strip_task_lines(content))[:text_limit].strip()
    summary = pending_task_summary(content, limit=task_limit)
    if not summary:
        return prose or f"{len(tasks)} item{'s' if len(tasks) != 1 else ''}"
    return f"{prose}\n\n{summary}" if prose else summary