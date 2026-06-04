// src/email_thread_parser.rs  <- src/email_thread_parser.py
//! Server-side email thread parser.
//!
//! Walks an email body (HTML or plain text) and returns a tree of reply turns
//! that the client can render directly without re-parsing. Mirrors the rules
//! from talon (mailgun) and email-reply-parser:
//!   - Multilingual "On <date>, <name> wrote:" attribution lines (20+ locales)
//!   - Outlook-style "From: ... Sent: ... Subject:" header blocks
//!   - "----- Original Message -----" delimiters
//!   - `<blockquote>` nesting (HTML)
//!   - `"> "` prefix nesting (plain text)
//!
//! Returns a list of `{level, body_html, meta}` dicts (modelled as
//! `serde_json::Value` objects) where level 0 is the current reply and
//! increasing levels = deeper in the chain.
//!
//! PORTING NOTES / DOCUMENTED DRIFT:
//! - The lookahead-bearing `_QUOTE_META_FROM` / `_QUOTE_META_DATE` patterns use
//!   the `fancy-regex` crate (the `regex` crate has no `(?=...)`); every other
//!   pattern uses the `regex` crate.
//! - The HTML path uses the `scraper` crate (html5ever). bs4's mutating
//!   `.extract()` is reproduced by tracking *detached* `NodeId`s and a custom
//!   inner-HTML serializer that skips them (html5ever's `ego_tree` is immutable).
//!   DRIFT: html5ever follows the HTML5 parsing spec while Python's
//!   `html.parser` is more lenient, so malformed mail can yield slightly
//!   different DOM shapes.
//! - Python `str.splitlines()` splits on a wider set of unicode line boundaries
//!   than Rust `.lines()`. [`splitlines`] reproduces the ASCII + common-unicode
//!   boundary set (`\n \r \r\n \v \f \x1c \x1d \x1e \u{85} \u{2028} \u{2029}`).
//! - `html.escape` -> `html_escape::encode_quoted_attribute` (the same five-entity
//!   table); `html.unescape` -> `html_escape::decode_html_entities`.

use fancy_regex::Regex as FancyRegex;
use once_cell::sync::Lazy;
use regex::Regex;
use scraper::{ElementRef, Html, Node};
use serde_json::{json, Value};

/// Bump whenever the parser's output shape or splitting rules change.
pub const THREAD_PARSER_VERSION: i64 = 6;

// ── Locale fragment consts (same as static/js/emailLibrary.js _TALON_*) ──

const WROTE: &str = "(?:wrote|écrit|escribió|scrisse|schrieb|skrev|schreef|napisał|написал|\
napsal|написа|έγραψε|katselivat|napisao|написав|napisała|napisali|\
hat geschrieben|kirjoitti|написала|escreveu)";
const FROM: &str = "(?:From|Från|Von|De|Da|От|Od|Van|差出人|发件人|寄件人|Lähettäjä|\
Avsender|Pošiljatelj|Frá)";
const SENT: &str = "(?:Sent|Skickat|Gesendet|Envoy[ée]|Inviato|Enviado|Verzonden|Отправлено|\
Wysłane|Date|送信日時|发送时间|寄件日期|Sendt|Lähetetty|Tarih|Datum|Data)";
const SUBJ: &str = "(?:Subject|Ämne|Betreff|Objet|Oggetto|Asunto|Onderwerp|Тема|Temat|\
件名|主题|主旨|Emne|Aihe|Konu)";
const TO: &str = "(?:To|Till|An|À|A|Voor|Para|Naar|Кому|Do|宛先|收件人|Komu)";
const CCBCC: &str = "(?:Cc|Bcc|Kopie|Skrytá kopie|Копия)";

/// `_HDR_KEYS` = the union of all header-key fragments.
fn hdr_keys() -> String {
    format!("(?:{FROM}|{SENT}|{SUBJ}|{TO}|{CCBCC}|Importance|Priority)")
}

// ── Compiled patterns ──

/// `_ORIG_RE` — "-----Original Message-----" delimiters (IGNORECASE).
///
/// Single-line raw string: a backslash-newline inside a Rust `r"..."` raw
/// string is NOT a line continuation (raw strings keep both chars literally),
/// which would corrupt the pattern — so the locale alternation is kept on one
/// line.
static ORIG_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)(?:^|\n)[\s>]*[-_=]{3,}\s*(?:Original\s+Message|Forwarded\s+message|Ursprüngliche\s+Nachricht|Mensaje\s+original|Messaggio\s+originale|Message\s+d[‘’]origine|Oorspronkelijk\s+bericht|Original\s+meddelande|原文|原始邮件|転送)\s*[-_=]{3,}",
    )
    .expect("ORIG_RE")
});

/// `_WROTE_LINE_RE` — Gmail "On … wrote:" attribution (IGNORECASE | MULTILINE).
static WROTE_LINE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(&format!(r"(?im)^\s*On\s.+?\s{WROTE}\s*:\s*$")).expect("WROTE_LINE_RE"));

/// The CJK attribution body shared between line-anchored and search variants.
///
/// Single-line raw string (see [`ORIG_RE`] for why raw strings can't use
/// backslash-newline continuations). Reproduced character-for-character from the
/// Python `_CJK_ATTRIB_LINE_RE` alternation.
const CJK_ATTRIB_BODY: &str = r"(?:\d{4}[年/.-]\d{1,2}[月/.-]\d{1,2}日?(?:\s*[\(\(].+?[\)\)])?\s+\d{1,2}:\d{2}(?:\s*[ＡＰAP][ＭM])?(?:に|、|,)?\s*(?:.+?\s+)?[<＜]?[\w.+\-]+@[\w.\-]+\.[A-Za-z]{2,}[>＞]?\s*(?:のメッセージ|さんは(?:書|お?書き)きました|wrote)?\s*[:：]\s*$|.+?(?:さん|様)\s*(?:は|が)\s+\d{4}[年/.-]\d{1,2}[月/.-]\d{1,2}日?(?:\s*[\(\(].+?[\)\)])?\s+\d{1,2}:\d{2}\s*(?:に)?\s*(?:書|お?書き)きました\s*[:：]\s*$|.+?\s*写道\s*[:：]\s*$|.+?\s*님이\s*작성(?:한\s*내용)?\s*[:：]\s*$)";

/// `_CJK_ATTRIB_LINE_RE` — CJK-style attribution lines (MULTILINE).
///
/// NOTE: the body above is reproduced character-for-character from the Python
/// source, except `内容` (the CJK glyph in the Korean "작성한 내용" branch) which
/// is the same Unicode codepoint as in `_CJK_ATTRIB_LINE_RE` of the source.
static CJK_ATTRIB_LINE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(&format!(r"(?m)^\s*{CJK_ATTRIB_BODY}")).expect("CJK_ATTRIB_LINE_RE")
});

/// `_OUTLOOK_HEADER_RE` — Outlook From:/Sent: header block (IGNORECASE).
static OUTLOOK_HEADER_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(&format!(
        r"(?i){FROM}\s*:\s*[^\n]+\s*\n\s*(?:.+\n)?{SENT}\s*:\s*[^\n]+\s*\n"
    ))
    .expect("OUTLOOK_HEADER_RE")
});

/// `_FROM_STOP` / `_DATE_STOP` — the stop lookaheads for the meta captures.
fn from_stop() -> String {
    format!(r"\s+(?:{FROM}|{SENT}|{SUBJ}|{TO}|{CCBCC}|Importance|Priority)\s*:")
}
fn date_stop() -> String {
    format!(r"\s+(?:{FROM}|{SUBJ}|{TO}|{CCBCC}|Importance|Priority)\s*:")
}

/// `_QUOTE_META_FROM` — From: capture, stopping at the next header key
/// (IGNORECASE | DOTALL, with a `(?=...)` lookahead -> fancy-regex).
static QUOTE_META_FROM: Lazy<FancyRegex> = Lazy::new(|| {
    FancyRegex::new(&format!(
        r"(?is){FROM}\s*:\s*(.+?)(?:(?={})|$)",
        from_stop()
    ))
    .expect("QUOTE_META_FROM")
});

/// `_QUOTE_META_DATE` — Sent:/Date: capture (IGNORECASE | DOTALL, lookahead).
static QUOTE_META_DATE: Lazy<FancyRegex> = Lazy::new(|| {
    FancyRegex::new(&format!(
        r"(?is){SENT}\s*:\s*(.+?)(?:(?={})|$)",
        date_stop()
    ))
    .expect("QUOTE_META_DATE")
});

/// `_GMAIL_ATTRIB` — greedy date + lazy author before "wrote:" (IGNORECASE | DOTALL).
static GMAIL_ATTRIB: Lazy<Regex> = Lazy::new(|| {
    Regex::new(&format!(r"(?is)On\s+(.+),\s+([^,]+?)\s+{WROTE}\s*:")).expect("GMAIL_ATTRIB")
});

/// The CJK attribution *search* pattern used inside `_extract_quote_meta`
/// (captures date / optional display name / email address).
static CJK_META_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(\d{4}[年/.-]\d{1,2}[月/.-]\d{1,2}日?(?:\s*[\(\(][^\)\)]+?[\)\)])?\s+\d{1,2}:\d{2}(?:\s*[ＡＰAP][ＭM])?)\s*(?:に|、|,)?\s*(?:(.+?)\s+)?[<＜]?([\w.+\-]+@[\w.\-]+\.[A-Za-z]{2,})[>＞]?",
    )
    .expect("CJK_META_RE")
});

/// `_MASHED_HDR_RE` — Outlook one-line conversation summary header (IGNORECASE).
static MASHED_HDR_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(&format!(
        r"(?i)^\s*[\w.+\-]+@[\w.\-]+\.[A-Za-z]{{2,}}\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+\S+\s+\d+,?\s*\d{{4}}\s+\d{{1,2}}:\d{{2}}(?:\s*[AP]M)?(?:\s+{TO}\s*:\s*[^\n]+(?:\s+{SUBJ}\s*:\s*[^\n]*)?)?\s*(?:\n|$)"
    ))
    .expect("MASHED_HDR_RE")
});

// ── Misc small patterns (compiled once) ──

/// `re.sub(r"<style[\s\S]*?</style>", " ", t, IGNORECASE)`.
static STYLE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?is)<style.*?</style>").expect("STYLE_RE"));
/// `re.sub(r"<(?![^@>\s]+@[^@>\s]+>)[^>]+>", " ", plain)` — tag strip that keeps
/// `<addr@host>` patterns. The negative lookahead -> fancy-regex.
static TAG_STRIP_KEEP_EMAIL: Lazy<FancyRegex> =
    Lazy::new(|| FancyRegex::new(r"<(?![^@>\s]+@[^@>\s]+>)[^>]+>").expect("TAG_STRIP_KEEP_EMAIL"));
/// `re.sub(r"&nbsp;", " ", plain, IGNORECASE)`.
static NBSP_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)&nbsp;").expect("NBSP_RE"));
/// `re.sub(r"\s+", " ", plain)`.
static WS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\s+").expect("WS_RE"));
/// `re.match(r"^((?:>\s?)+)", line)` quote-prefix detector.
static QUOTE_PREFIX_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^((?:>\s?)+)").expect("QUOTE_PREFIX_RE"));
/// `re.sub(r"^(?:>\s?)+", "", line)` quote-prefix stripper.
static QUOTE_PREFIX_STRIP_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^(?:>\s?)+").expect("QUOTE_PREFIX_STRIP_RE"));
/// `re.sub(r"^\s*\n+", "", rest)` blank-lead stripper for `_strip_mashed_header`.
static BLANK_LEAD_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\s*\n+").expect("BLANK_LEAD_RE"));
/// `re.sub(r"<mailto:[^<>\s]*>", "", text, IGNORECASE)`.
static MAILTO_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)<mailto:[^<>\s]*>").expect("MAILTO_RE"));
/// `re.sub(r"<https?://[^<>\s]*>", "", text, IGNORECASE)`.
static URL_BRACKET_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)<https?://[^<>\s]*>").expect("URL_BRACKET_RE"));
/// `re.sub(r"[^\S\n]+(\n|$)", r"\1", text)` — trailing-whitespace trim per line.
static TRAIL_WS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^\S\n]+(\n|$)").expect("TRAIL_WS_RE"));
/// `re.sub(r"\n{3,}", "\n\n", text)` newline collapse.
static NL_COLLAPSE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\n{3,}").expect("NL_COLLAPSE_RE"));
/// `re.sub(r"<[^>]+>", " ", html_chunk)` plain tag strip.
static TAG_STRIP_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"<[^>]+>").expect("TAG_STRIP_RE"));
/// `re.sub(r"(https?://[^\s<>\"]+)", ...)` URL linkifier.
static LINKIFY_RE: Lazy<Regex> = Lazy::new(|| Regex::new("(https?://[^\\s<>\"]+)").expect("LINKIFY_RE"));
/// `re.match(rf"^{_FROM}\s*:\s*\S", first, IGNORECASE)`.
static FROM_LINE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(&format!(r"(?i)^{FROM}\s*:\s*\S")).expect("FROM_LINE_RE"));
/// `re.match(rf"^{_SENT}\s*:", nl, IGNORECASE)`.
static SENT_LINE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(&format!(r"(?i)^{SENT}\s*:")).expect("SENT_LINE_RE"));
/// `re.match(rf"^{_HDR_KEYS}\s*:", nl, IGNORECASE)`.
static HDR_KEY_LINE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(&format!(r"(?i)^{}\s*:", hdr_keys())).expect("HDR_KEY_LINE_RE"));
/// `re.match(rf"^\s*On\s.+?\s{_WROTE}\s*:\s*$", stripped, IGNORECASE)` — single-line variant.
static GMAIL_LINE_MATCH_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(&format!(r"(?i)^\s*On\s.+?\s{WROTE}\s*:\s*$")).expect("GMAIL_LINE_MATCH_RE"));

// ── meta extraction ──

/// `_extract_quote_meta` — pull a `"<sender> · <date>"` chip from a quoted block.
fn extract_quote_meta(text_or_html: &str) -> Option<String> {
    if text_or_html.is_empty() {
        return None;
    }
    // Strip <style> blocks, then tags (keeping `<addr@host>`), &nbsp;, decode a
    // few entities, collapse whitespace, trim, slice to 1500 code points.
    let plain = STYLE_RE.replace_all(text_or_html, " ");
    let plain = TAG_STRIP_KEEP_EMAIL.replace_all(&plain, " ").into_owned();
    let plain = NBSP_RE.replace_all(&plain, " ").into_owned();
    let plain = plain
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"");
    let plain = WS_RE.replace_all(&plain, " ");
    let plain: String = plain.trim().chars().take(1500).collect();

    let f = QUOTE_META_FROM.captures(&plain).ok().flatten();
    let d = QUOTE_META_DATE.captures(&plain).ok().flatten();
    if let (Some(fc), Some(dc)) = (&f, &d) {
        let from = fc.get(1).map(|m| m.as_str().trim()).unwrap_or("");
        let date: String = dc
            .get(1)
            .map(|m| m.as_str().trim())
            .unwrap_or("")
            .chars()
            .take(80)
            .collect();
        return Some(format!("{from} · {date}"));
    }
    if let Some(g) = GMAIL_ATTRIB.captures(&plain) {
        let date = g.get(1).map(|m| m.as_str().trim()).unwrap_or("");
        let who = g.get(2).map(|m| m.as_str().trim()).unwrap_or("");
        return Some(format!("{who} · {date}"));
    }
    if let Some(cjk) = CJK_META_RE.captures(&plain) {
        let date = cjk.get(1).map(|m| m.as_str().trim()).unwrap_or("").to_string();
        // who = group(2) or group(3) or ''
        let g2 = cjk.get(2).map(|m| m.as_str()).unwrap_or("");
        let who = if !g2.is_empty() {
            g2.trim().to_string()
        } else {
            cjk.get(3).map(|m| m.as_str().trim()).unwrap_or("").to_string()
        };
        return Some(if !who.is_empty() {
            format!("{who} · {date}")
        } else {
            date
        });
    }
    if let Some(fc) = &f {
        return Some(fc.get(1).map(|m| m.as_str().trim()).unwrap_or("").to_string());
    }
    if let Some(dc) = &d {
        return Some(dc.get(1).map(|m| m.as_str().trim()).unwrap_or("").to_string());
    }
    None
}

// ── Plaintext path ──

/// `_strip_mashed_header` — strip a one-line Outlook conversation summary header.
fn strip_mashed_header(text: &str) -> String {
    if text.is_empty() {
        return text.to_string();
    }
    // `_MASHED_HDR_RE.match(text)` — anchored at start.
    match MASHED_HDR_RE.find(text) {
        Some(m) if m.start() == 0 => {
            let rest = &text[m.end()..];
            BLANK_LEAD_RE.replace(rest, "").into_owned()
        }
        _ => text.to_string(),
    }
}

/// `_normalize_body` — strip mail-client noise from the plaintext body.
fn normalize_body(text: &str) -> String {
    if text.is_empty() {
        return text.to_string();
    }
    let text = strip_mashed_header(text);
    let text = MAILTO_RE.replace_all(&text, "").into_owned();
    let text = URL_BRACKET_RE.replace_all(&text, "").into_owned();
    let text = TRAIL_WS_RE.replace_all(&text, "$1").into_owned();
    NL_COLLAPSE_RE.replace_all(&text, "\n\n").into_owned()
}

/// `_outlook_header_block_end` — return exclusive end of an Outlook header block
/// starting at `start`, else `start` if the lines don't form one.
fn outlook_header_block_end(stripped: &[String], levels: &[i64], start: usize) -> usize {
    if start >= stripped.len() {
        return start;
    }
    let base = levels[start];
    let first = stripped[start].trim();
    if FROM_LINE_RE.find(first).is_none() {
        return start;
    }
    // Look ahead for the matching Sent:/Date: line at the same base level.
    let mut found_sent = false;
    let mut j = start + 1;
    while j < stripped.len() && j < start + 6 && levels[j] == base {
        let nl = stripped[j].trim();
        if nl.is_empty() {
            j += 1;
            continue;
        }
        if SENT_LINE_RE.find(nl).is_some() {
            found_sent = true;
            break;
        }
        if HDR_KEY_LINE_RE.find(nl).is_none() {
            return start; // something other than a header key — abort
        }
        j += 1;
    }
    if !found_sent {
        return start;
    }
    // Consume header-key lines until a non-header line OR a blank line.
    let mut j = start + 1;
    while j < stripped.len() && levels[j] == base {
        let nl = stripped[j].trim();
        if nl.is_empty() {
            j += 1;
            break;
        }
        if HDR_KEY_LINE_RE.find(nl).is_some() {
            j += 1;
            continue;
        }
        break;
    }
    j
}

/// Python `str.splitlines()` — splits on the wider unicode boundary set than
/// Rust's `.lines()`. Reproduces the ASCII + common-unicode set; no trailing
/// empty element when the text ends with a separator (matches Python).
fn splitlines(text: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut cur = String::new();
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '\r' => {
                // \r\n is a single boundary.
                if chars.peek() == Some(&'\n') {
                    chars.next();
                }
                out.push(std::mem::take(&mut cur));
            }
            '\n' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}'
            | '\u{2028}' | '\u{2029}' => {
                out.push(std::mem::take(&mut cur));
            }
            other => cur.push(other),
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

/// `_parse_plaintext` — walk `>` quote levels + inline attribution markers.
fn parse_plaintext(text: &str) -> Option<Vec<Value>> {
    if text.is_empty() || text.chars().count() > 200_000 {
        return None;
    }
    let text = normalize_body(text);
    let lines = splitlines(&text);

    let mut base_levels: Vec<i64> = Vec::with_capacity(lines.len());
    let mut stripped_lines: Vec<String> = Vec::with_capacity(lines.len());
    for line in &lines {
        let m = QUOTE_PREFIX_RE.find(line);
        let n = match m {
            // `line[:m.end()].count(">")`
            Some(mm) => line[..mm.end()].matches('>').count() as i64,
            None => 0,
        };
        base_levels.push(n);
        if n > 0 {
            stripped_lines.push(QUOTE_PREFIX_STRIP_RE.replace(line, "").into_owned());
        } else {
            stripped_lines.push(line.clone());
        }
    }

    let has_quotes = base_levels.iter().any(|&l| l > 0);
    let has_attrib = WROTE_LINE_RE.is_match(&text)
        || ORIG_RE.is_match(&text)
        || OUTLOOK_HEADER_RE.is_match(&text)
        || CJK_ATTRIB_LINE_RE.is_match(&text);
    if !has_quotes && !has_attrib {
        return None;
    }

    let mut turns: Vec<Value> = Vec::new();
    let mut buf: Vec<String> = Vec::new();
    let mut cur_level: i64 = 0;
    let mut pending_meta: Option<String> = None;
    let mut depth_at_base: std::collections::HashMap<i64, i64> = std::collections::HashMap::new();
    depth_at_base.insert(0, 0);
    let mut depth: i64 = 0;
    let mut prev_base: i64 = 0;

    // `lookahead_content_base(start_idx)` — base level of next non-blank line.
    let lookahead_content_base = |start_idx: usize| -> Option<i64> {
        let mut j = start_idx;
        while j < lines.len() && stripped_lines[j].trim().is_empty() {
            j += 1;
        }
        if j < lines.len() {
            Some(base_levels[j])
        } else {
            None
        }
    };

    // `flush()` — emit the buffered turn.
    fn flush(
        turns: &mut Vec<Value>,
        buf: &mut Vec<String>,
        cur_level: i64,
        pending_meta: &mut Option<String>,
    ) {
        if buf.is_empty() {
            return;
        }
        let body = buf.join("\n");
        let body = body.trim_end().to_string();
        if !body.is_empty() || cur_level > 0 {
            turns.push(json!({
                "level": cur_level,
                "body_html": escape_to_html(&body),
                "meta": match pending_meta {
                    Some(s) => Value::String(s.clone()),
                    None => Value::Null,
                },
            }));
        }
        buf.clear();
        *pending_meta = None;
    }

    let mut i = 0usize;
    while i < lines.len() {
        let base = base_levels[i];
        let stripped = stripped_lines[i].clone();

        // `>` base level change → flush current turn, then step depth.
        if base > prev_base {
            flush(&mut turns, &mut buf, cur_level, &mut pending_meta);
            for b in (prev_base + 1)..=base {
                depth += 1;
                depth_at_base.insert(b, depth);
            }
            cur_level = depth;
        } else if base < prev_base {
            flush(&mut turns, &mut buf, cur_level, &mut pending_meta);
            depth = *depth_at_base.get(&base).unwrap_or(&base);
            let to_remove: Vec<i64> = depth_at_base
                .keys()
                .copied()
                .filter(|&b| b > base)
                .collect();
            for b in to_remove {
                depth_at_base.remove(&b);
            }
            cur_level = depth;
        }
        prev_base = base;

        let is_gmail = GMAIL_LINE_MATCH_RE.is_match(&stripped);
        // `_CJK_ATTRIB_LINE_RE.match(stripped)` — anchored at start.
        let is_cjk = cjk_attrib_line_match(&stripped);
        // `_ORIG_RE.search("\n" + stripped)`
        let is_orig = ORIG_RE.is_match(&format!("\n{stripped}"));
        let outlook_end = outlook_header_block_end(&stripped_lines, &base_levels, i);
        let is_outlook = outlook_end > i;

        if is_gmail || is_cjk || is_orig || is_outlook {
            let mut attrib_end = if is_outlook { outlook_end } else { i + 1 };
            let mut meta_text = stripped_lines[i..attrib_end].join("\n");

            // Fold a following Outlook header block into the same Original event.
            if is_orig {
                let mut j = attrib_end;
                while j < lines.len()
                    && base_levels[j] == base
                    && stripped_lines[j].trim().is_empty()
                {
                    j += 1;
                }
                if j < lines.len() && base_levels[j] == base {
                    let oe2 = outlook_header_block_end(&stripped_lines, &base_levels, j);
                    if oe2 > j {
                        meta_text = format!("{}\n{}", meta_text, stripped_lines[j..oe2].join("\n"));
                        attrib_end = oe2;
                    }
                }
            }

            let next_base = lookahead_content_base(attrib_end);
            flush(&mut turns, &mut buf, cur_level, &mut pending_meta);
            // pending_meta = _extract_quote_meta(meta_text) or first stripped line
            let meta = extract_quote_meta(&meta_text)
                .unwrap_or_else(|| first_stripped_line(&meta_text));
            if next_base.map(|nb| nb > base).unwrap_or(false) {
                pending_meta = Some(meta);
            } else {
                depth += 1;
                depth_at_base.insert(base, depth);
                cur_level = depth;
                pending_meta = Some(meta);
            }
            i = attrib_end;
            continue;
        }

        buf.push(stripped);
        i += 1;
    }

    flush(&mut turns, &mut buf, cur_level, &mut pending_meta);

    // `if not turns or (len==1 and turns[0]["level"]==0): return None`
    if turns.is_empty()
        || (turns.len() == 1 && turns[0].get("level") == Some(&json!(0)))
    {
        return None;
    }
    Some(turns)
}

/// `meta_text.strip().splitlines()[0]` — first line of the trimmed meta text.
///
/// Python would `IndexError` if the trimmed text were empty, but the callers
/// only reach this fallback after a successful attribution match (the text is
/// always non-empty); we return `""` rather than panic to stay total.
fn first_stripped_line(meta_text: &str) -> String {
    let trimmed = meta_text.trim();
    splitlines(trimmed).into_iter().next().unwrap_or_default()
}

/// Anchored `_CJK_ATTRIB_LINE_RE.match(s)` — match must start at position 0.
fn cjk_attrib_line_match(s: &str) -> bool {
    match CJK_ATTRIB_LINE_RE.find(s) {
        Some(m) => m.start() == 0,
        None => false,
    }
}

/// `_escape_to_html` — conservative plaintext → HTML.
fn escape_to_html(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    // html.escape(text, quote=True)
    let out = html_escape::encode_quoted_attribute(text).into_owned();
    // Linkify URLs.
    let out = LINKIFY_RE.replace_all(&out, |caps: &regex::Captures| {
        let u = &caps[1];
        format!("<a href=\"{u}\" target=\"_blank\" rel=\"noopener\">{u}</a>")
    });
    out.replace('\n', "<br>")
}

// ── HTML path (scraper / html5ever) ──

/// `_is_quote_container(tag)` — known quote-container element predicate.
fn is_quote_container(el: ElementRef) -> bool {
    let name = el.value().name().to_lowercase();
    if name == "blockquote" {
        return true;
    }
    // `cls = " ".join(tag.get("class") or []).lower()` — class attr lowercased.
    let cls = el
        .value()
        .attr("class")
        .map(|c| c.to_lowercase())
        .unwrap_or_default();
    if cls.contains("gmail_quote") || cls.contains("yahoo_quoted") || cls.contains("moz-cite-prefix")
    {
        return true;
    }
    if cls.contains("outlookmessageheader") || cls.contains("wordsection1") {
        return true;
    }
    if el.value().attr("id") == Some("divRplyFwdMsg") {
        return true;
    }
    let typ = el.value().attr("type").unwrap_or("").to_lowercase();
    if name == "div" && typ == "cite" {
        return true;
    }
    false
}

/// True if `el` has a quote-container ancestor (anywhere above it).
fn has_quote_ancestor(el: ElementRef) -> bool {
    let mut p = el.parent();
    while let Some(node) = p {
        if let Some(pe) = ElementRef::wrap(node) {
            if is_quote_container(pe) {
                return true;
            }
        }
        p = node.parent();
    }
    false
}

/// True if there is a quote container strictly between `child` and `ancestor`.
fn has_quote_between(child: ElementRef, ancestor: ElementRef) -> bool {
    let anc_id = ancestor.id();
    let mut p = child.parent();
    while let Some(node) = p {
        if node.id() == anc_id {
            return false;
        }
        if let Some(pe) = ElementRef::wrap(node) {
            if is_quote_container(pe) {
                return true;
            }
        }
        p = node.parent();
    }
    false
}

/// Serialize a node's OUTER HTML, skipping any node whose id is in `detached`.
/// Mirrors bs4 `str(node)` after `.extract()` calls removed children.
fn serialize_outer(node: ego_tree::NodeRef<Node>, detached: &std::collections::HashSet<ego_tree::NodeId>, out: &mut String) {
    if detached.contains(&node.id()) {
        return;
    }
    match node.value() {
        Node::Text(t) => {
            push_escaped_text(t, out);
        }
        Node::Element(e) => {
            out.push('<');
            out.push_str(e.name());
            for (name, value) in e.attrs() {
                out.push(' ');
                out.push_str(name);
                out.push_str("=\"");
                push_escaped_attr(value, out);
                out.push('"');
            }
            // bs4's `html.parser` serializes void elements self-closed (`<br/>`),
            // matching the Python `str(node)` the parser compares against.
            if is_void_element(e.name()) {
                out.push_str("/>");
                return;
            }
            out.push('>');
            for child in node.children() {
                serialize_outer(child, detached, out);
            }
            out.push_str("</");
            out.push_str(e.name());
            out.push('>');
        }
        Node::Comment(c) => {
            out.push_str("<!--");
            out.push_str(c);
            out.push_str("-->");
        }
        Node::ProcessingInstruction(pi) => {
            out.push_str("<?");
            out.push_str(&pi.data);
            out.push('>');
        }
        Node::Doctype(d) => {
            out.push_str("<!DOCTYPE ");
            out.push_str(d.name());
            out.push('>');
        }
        _ => {}
    }
}

/// Serialize a node's INNER HTML (its children), skipping `detached` ids.
/// Mirrors bs4 `node.decode_contents()`.
fn serialize_inner(node: ElementRef, detached: &std::collections::HashSet<ego_tree::NodeId>) -> String {
    let mut out = String::new();
    for child in node.children() {
        serialize_outer(child, detached, &mut out);
    }
    out
}

/// HTML5 void elements (no closing tag / no children).
fn is_void_element(name: &str) -> bool {
    matches!(
        name,
        "area"
            | "base"
            | "br"
            | "col"
            | "embed"
            | "hr"
            | "img"
            | "input"
            | "link"
            | "meta"
            | "param"
            | "source"
            | "track"
            | "wbr"
    )
}

/// bs4 `html.parser` text-node serialization escaping (minimal formatter:
/// `&`, `<`, `>` only — U+00A0 is emitted as the literal character).
fn push_escaped_text(t: &str, out: &mut String) {
    for c in t.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            other => out.push(other),
        }
    }
}

/// bs4 `html.parser` attribute-value serialization escaping (minimal formatter:
/// `&`, `"` only — U+00A0 is emitted as the literal character).
fn push_escaped_attr(v: &str, out: &mut String) {
    for c in v.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '"' => out.push_str("&quot;"),
            other => out.push(other),
        }
    }
}

/// `_strip_trailing_attribution(html_chunk)` — drop a trailing Gmail/CJK
/// attribution line from a head chunk (HTML path).
///
/// Returns `None` to mirror Python's *runtime* `re.error`: the CJK pattern below
/// is malformed (an unbalanced `(?:` group — see [`STRIP_CJK_TAIL`]) so Python's
/// `re.sub` at `email_thread_parser.py:505` raises `re.error` whenever this
/// function gets past the attribution guard, and that exception propagates out of
/// `_parse_html` AND out of `parse_thread` (neither wraps it). The real callers
/// (`routes/email_routes.py`, `src/builtin_actions.py`) wrap `parse_thread` in
/// `try/except` and set the result to `None` — so the thread renders flat and the
/// `if body_text: return _parse_plaintext(...)` fallback is **never** reached.
/// We reproduce that exactly: this `None` becomes [`ThreadAborted`] in `parse_html`,
/// which `parse_thread` maps to a bare `None` WITHOUT trying plaintext. When the
/// guard does not fire, the chunk is returned unchanged and no error path is taken
/// — matching the `return html_chunk` early-out in Python.
fn strip_trailing_attribution(html_chunk: &str) -> Option<String> {
    // `text = re.sub(r"<[^>]+>", " ", html_chunk)`
    let text = TAG_STRIP_RE.replace_all(html_chunk, " ").into_owned();
    if !(WROTE_LINE_RE.is_match(&text)
        || ORIG_RE.is_match(&text)
        || CJK_ATTRIB_LINE_RE.is_match(&text))
    {
        return Some(html_chunk.to_string());
    }
    // First sub: trailing English "On … wrote:" (IGNORECASE | DOTALL).
    static STRIP_GMAIL_TAIL: Lazy<Regex> = Lazy::new(|| {
        Regex::new(&format!(
            r"(?is)(?:<br\s*/?>|</p>|</div>|\n)?\s*On\s.+?\s{WROTE}\s*:\s*(?:</[^>]+>)*\s*$"
        ))
        .expect("STRIP_GMAIL_TAIL")
    });
    // Second sub: trailing CJK attribution (DOTALL). This mirrors the Python
    // pattern verbatim (email_thread_parser.py:505-515), INCLUDING its bug: the
    // leading `(?:\d{4}...` group is never closed, so the `regex` crate (like
    // Python's `re`) rejects it. We build the pattern from an owned `String` so
    // it is compiled fallibly at use-time rather than const-rejected by clippy,
    // then `.ok()` keeps it as `None` — the exact analogue of Python raising
    // `re.error` at the `re.sub` call site.
    static STRIP_CJK_TAIL: Lazy<Option<Regex>> = Lazy::new(|| {
        let pattern: String = String::from(
            r"(?s)(?:<br\s*/?>|</p>|</div>|\n)?\s*(?:\d{4}[年/.-]\d{1,2}[月/.-]\d{1,2}日?(?:\s*[\(\(][^\)\)]+?[\)\)])?\s+\d{1,2}:\d{2}(?:\s*[ＡＰAP][ＭM])?(?:に|、|,)?\s*(?:.+?\s+)?[<＜]?[\w.+\-]+@[\w.\-]+\.[A-Za-z]{2,}[>＞]?\s*(?:のメッセージ|さんは(?:書|お?書き)きました|wrote)?\s*[:：]\s*(?:</[^>]+>)*\s*$",
        );
        Regex::new(&pattern).ok()
    });
    // English sub runs successfully (as in Python) before the CJK sub is reached.
    let html_chunk = STRIP_GMAIL_TAIL.replace(html_chunk, "").into_owned();
    // CJK sub: if the pattern compiled it strips; if it could not (it cannot, by
    // construction), Python would have raised here, so we propagate `None`.
    STRIP_CJK_TAIL
        .as_ref()
        .map(|re| re.replace(&html_chunk, "").into_owned())
}

/// Signals that Python's `_parse_html` *raised* `re.error` (the malformed CJK
/// strip pattern at `email_thread_parser.py:505`). Distinct from a normal `None`:
/// the Python exception unwinds the **whole** `parse_thread`, so the plaintext
/// fallback is NOT reached. A plain `None` would let `parse_thread` fall through
/// to `_parse_plaintext`, diverging from Python — hence a dedicated error type.
struct ThreadAborted;

/// `_parse_html` — walk top-level quote containers and recurse into nested ones.
///
/// `Ok(None)` is Python's normal "no quoted material" return (caller may try
/// plaintext); `Err(ThreadAborted)` is Python's `re.error` raise (caller must
/// NOT try plaintext) — see [`strip_trailing_attribution`].
fn parse_html(html: &str) -> Result<Option<Vec<Value>>, ThreadAborted> {
    if html.is_empty() || html.chars().count() > 200_000 {
        return Ok(None);
    }
    let doc = Html::parse_fragment(html);

    // `all_quotes = [t for t in soup.find_all(True) if _is_quote_container(t)]`
    let all_quotes: Vec<ElementRef> = doc
        .tree
        .nodes()
        .filter_map(ElementRef::wrap)
        .filter(|e| is_quote_container(*e))
        .collect();
    if all_quotes.is_empty() {
        return Ok(None);
    }

    // Keep only top-level quotes (no quote-container ancestor).
    let tops: Vec<ElementRef> = all_quotes
        .iter()
        .copied()
        .filter(|t| !has_quote_ancestor(*t))
        .collect();
    if tops.is_empty() {
        return Ok(None);
    }

    let mut turns: Vec<Value> = Vec::new();
    let detached: std::collections::HashSet<ego_tree::NodeId> = std::collections::HashSet::new();

    // `parent_children = list(tops[0].parent.children if tops[0].parent else [])`
    let first_top = tops[0];
    let parent_children: Vec<ego_tree::NodeRef<Node>> = match first_top.parent() {
        Some(p) => p.children().collect(),
        None => Vec::new(),
    };

    // head_nodes = siblings before tops[0]
    let first_top_id = first_top.id();
    let mut head_nodes: Vec<ego_tree::NodeRef<Node>> = Vec::new();
    for sib in &parent_children {
        if sib.id() == first_top_id {
            break;
        }
        head_nodes.push(*sib);
    }

    // tail_nodes = everything after the LAST top-level quote (skipping other tops)
    let last_top_id = tops[tops.len() - 1].id();
    let top_ids: std::collections::HashSet<ego_tree::NodeId> =
        tops.iter().map(|t| t.id()).collect();
    let mut tail_nodes: Vec<ego_tree::NodeRef<Node>> = Vec::new();
    let mut after_last = false;
    for sib in &parent_children {
        if sib.id() == last_top_id {
            after_last = true;
            continue;
        }
        if after_last && top_ids.contains(&sib.id()) {
            continue;
        }
        if after_last {
            tail_nodes.push(*sib);
        }
    }

    // head_html / tail_html via `"".join(str(n) for n in nodes)`.
    let mut head_raw = String::new();
    for n in &head_nodes {
        serialize_outer(*n, &detached, &mut head_raw);
    }
    // In Python a malformed CJK regex raises `re.error` here and unwinds the whole
    // `parse_thread` (caller -> flat render, NO plaintext fallback). We surface that
    // as `Err(ThreadAborted)` so `parse_thread` returns `None` without falling
    // through to `parse_plaintext`; a normal `None` (no quotes) would instead.
    let head_html = match strip_trailing_attribution(&head_raw) {
        Some(s) => s,
        None => return Err(ThreadAborted),
    };
    let mut tail_html = String::new();
    for n in &tail_nodes {
        serialize_outer(*n, &detached, &mut tail_html);
    }

    // Stitch tail + head (tail first).
    let mut parts: Vec<String> = Vec::new();
    if !tail_html.trim().is_empty() {
        parts.push(tail_html.trim().to_string());
    }
    if !head_html.trim().is_empty() {
        parts.push(head_html.trim().to_string());
    }
    if !parts.is_empty() {
        let body_html = if parts.len() > 1 {
            parts.join("<br><br>")
        } else {
            parts[0].clone()
        };
        turns.push(json!({"level": 0, "body_html": body_html, "meta": Value::Null}));
    }

    for bq in &tops {
        walk(*bq, 1, None, &mut turns);
    }

    if turns.is_empty() {
        return Ok(None);
    }
    Ok(Some(turns))
}

/// `_walk` / `_walk_with_meta` merged: when `forced_meta` is `Some`, the node's
/// own meta falls back to it and the wrapper-collapse step is skipped (matching
/// `_walk_with_meta`, which never collapses again).
fn walk(node: ElementRef, level: i64, forced_meta: Option<&str>, turns: &mut Vec<Value>) {
    // meta_from_node = _extract_quote_meta(str(node)) [or forced_meta]
    let node_outer = {
        let empty = std::collections::HashSet::new();
        let mut s = String::new();
        serialize_outer(*node, &empty, &mut s);
        s
    };
    let meta_from_node = match forced_meta {
        Some(fm) => extract_quote_meta(&node_outer).unwrap_or_else(|| fm.to_string()),
        None => extract_quote_meta(&node_outer).unwrap_or_default(),
    };
    let meta_is_some = forced_meta.is_some() || extract_quote_meta(&node_outer).is_some();

    // direct_nested = nested quote containers with no quote container between.
    // `node.find_all(True, recursive=True)` in bs4 yields DESCENDANTS only (not
    // the node itself), but `ego_tree::descendants()` includes the start node —
    // so we skip any element equal to `node`.
    let node_id = node.id();
    let nested: Vec<ElementRef> = node
        .descendants()
        .filter_map(ElementRef::wrap)
        .filter(|e| e.id() != node_id && is_quote_container(*e))
        .collect();
    let direct_nested: Vec<ElementRef> = nested
        .into_iter()
        .filter(|n| !has_quote_between(*n, node))
        .collect();

    // `n.extract()` for each direct nested -> mark its subtree detached.
    let mut detached: std::collections::HashSet<ego_tree::NodeId> = std::collections::HashSet::new();
    for n in &direct_nested {
        detached.insert(n.id());
    }

    // body_html = node.decode_contents() with detached nested quotes skipped.
    let body_html = serialize_inner(node, &detached);

    if forced_meta.is_none() {
        // Wrapper-collapse check (only in `_walk`, not `_walk_with_meta`).
        let body_text = TAG_STRIP_RE.replace_all(&body_html, " ").into_owned();
        let body_text = body_text.trim();
        let body_text = html_escape::decode_html_entities(body_text).into_owned();
        let body_text_collapsed = WS_RE.replace_all(body_text.trim(), " ").into_owned();
        let body_text_collapsed = body_text_collapsed.trim().to_string();
        let is_attrib_only = !body_text_collapsed.is_empty()
            && (cjk_attrib_line_match(&body_text_collapsed)
                || GMAIL_LINE_MATCH_RE.is_match(&body_text_collapsed)
                || outlook_header_anchored(&body_text_collapsed));
        if is_attrib_only && direct_nested.len() == 1 {
            // Skip emitting this wrapper; pass attribution as meta for child.
            let child_meta = if meta_is_some && !meta_from_node.is_empty() {
                meta_from_node
            } else {
                body_text_collapsed
            };
            walk(direct_nested[0], level, Some(&child_meta), turns);
            return;
        }
    }

    let meta_value = if meta_is_some {
        Value::String(meta_from_node)
    } else {
        Value::Null
    };
    turns.push(json!({"level": level, "body_html": body_html, "meta": meta_value}));
    for n in &direct_nested {
        walk(*n, level + 1, None, turns);
    }
}

/// Anchored `_OUTLOOK_HEADER_RE.match(s)` — match must start at position 0.
fn outlook_header_anchored(s: &str) -> bool {
    match OUTLOOK_HEADER_RE.find(s) {
        Some(m) => m.start() == 0,
        None => false,
    }
}

/// Public entry point. Prefer HTML when available, else plaintext.
///
/// Returns `None` if no quoted material is found (caller renders flat).
pub fn parse_thread(body_html: Option<&str>, body_text: Option<&str>) -> Option<Vec<Value>> {
    if let Some(h) = body_html {
        if !h.is_empty() {
            // `out = _parse_html(body_html); if out: return out` — Python truthiness:
            // a non-empty list returns; `None`/empty falls through to plaintext; but a
            // `re.error` raise unwinds the WHOLE function (no plaintext fallback), which
            // we model as `Err(ThreadAborted) -> return None`.
            match parse_html(h) {
                Ok(Some(out)) if !out.is_empty() => return Some(out),
                Ok(_) => {}
                Err(ThreadAborted) => return None,
            }
        }
    }
    if let Some(t) = body_text {
        if !t.is_empty() {
            return parse_plaintext(t);
        }
    }
    None
}

#[cfg(test)]
mod tests {
    // Parity checks against the live Python (`src.email_thread_parser`).
    use super::*;

    fn levels(turns: &[Value]) -> Vec<i64> {
        turns.iter().map(|t| t["level"].as_i64().unwrap()).collect()
    }

    #[test]
    fn no_quotes_returns_none() {
        assert!(parse_thread(None, Some("Just a flat reply.\nNo quoting here.")).is_none());
        assert!(parse_thread(Some("<div>Plain reply</div>"), None).is_none());
    }

    #[test]
    fn plaintext_gmail_attrib_then_quote() {
        // Gmail "On … wrote:" immediately followed by a deeper > block is a
        // single conversation step.
        let body = "Thanks!\n\nOn Mon, May 5, 2026 at 10:00 AM, Alice <a@x.com> wrote:\n> Hello there\n> second line";
        let turns = parse_thread(None, Some(body)).expect("turns");
        // level 0 (reply) then level 1 (the quoted block), not two bumps.
        assert_eq!(levels(&turns), vec![0, 1]);
        assert!(turns[1]["meta"].as_str().unwrap().contains("Alice"));
        assert!(turns[1]["body_html"].as_str().unwrap().contains("Hello there"));
    }

    #[test]
    fn plaintext_plain_quote_levels() {
        let body = "Top reply\n> quoted level 1\n>> quoted level 2";
        let turns = parse_thread(None, Some(body)).expect("turns");
        assert_eq!(levels(&turns), vec![0, 1, 2]);
    }

    #[test]
    fn plaintext_cjk_attribution() {
        // Japanese Gmail default attribution line.
        let body = "返信します。\n\n2026年5月11日(月) 21:28 <alice@example.com>:\n> 元のメッセージ";
        let turns = parse_thread(None, Some(body)).expect("turns");
        assert_eq!(levels(&turns), vec![0, 1]);
        let meta = turns[1]["meta"].as_str().unwrap();
        assert!(meta.contains("alice@example.com"));
    }

    #[test]
    fn plaintext_mashed_header_stripped() {
        // The mashed conversation header at the very top is stripped before
        // parse, leaving only the quoted body. Verified against Python: the
        // result is a single level-1 turn (the empty level-0 reply, which would
        // be all-blank after the strip, is not emitted).
        let body = "alice@example.com Thursday, May 7, 2026 3:06 PM\n> quoted body";
        let turns = parse_thread(None, Some(body)).expect("turns");
        assert_eq!(levels(&turns), vec![1]);
        assert!(turns[0]["body_html"].as_str().unwrap().contains("quoted body"));
    }

    #[test]
    fn plaintext_outlook_header_block() {
        let body = "My reply text.\n\nFrom: Bob <b@y.com>\nSent: Monday, May 5, 2026\nSubject: Hi\n\nOriginal body line";
        let turns = parse_thread(None, Some(body)).expect("turns");
        // The From/Sent/Subject block is one attribution step -> level 1.
        assert_eq!(turns.last().unwrap()["level"], json!(1));
        assert!(turns.last().unwrap()["meta"].as_str().unwrap().contains("Bob"));
    }

    #[test]
    fn plaintext_forwarded_message_delimiter() {
        // "-----Forwarded message-----" must be recognised as a split point, just
        // like "-----Original Message-----". Added when ORIG_RE gained the
        // `Forwarded\s+message` alternative to match the Python source.
        let body = "See below.\n\n----- Forwarded message -----\nFrom: Eve <e@v.com>\nSent: Wed, May 7\n\nForwarded body";
        let turns = parse_thread(None, Some(body)).expect("turns");
        assert_eq!(levels(&turns), vec![0, 1]);
        assert!(turns[0]["body_html"].as_str().unwrap().contains("See below."));
        let meta = turns[1]["meta"].as_str().unwrap_or("");
        assert!(meta.contains("Eve"), "meta should contain sender, got: {meta}");
        assert!(turns[1]["body_html"].as_str().unwrap().contains("Forwarded body"));
    }

    #[test]
    fn orig_re_matches_forwarded_variants() {
        // Direct regex-level check: all case variants that the Python source
        // recognises via ORIG_RE should also match in Rust.
        let cases = [
            "\n----- Forwarded message -----",
            "\n--- Forwarded message ---",
            "\n=== Forwarded message ===",
            "\n----- forwarded message -----",  // IGNORECASE
            "\n----- FORWARDED MESSAGE -----",  // IGNORECASE
        ];
        for input in &cases {
            assert!(
                ORIG_RE.is_match(input),
                "ORIG_RE should match forwarded delimiter: {input:?}"
            );
        }
        // Sanity: original-message variants still match.
        assert!(ORIG_RE.is_match("\n----- Original Message -----"));
    }

    #[test]
    fn html_blockquote_nesting() {
        let html = r#"<div>Reply body<blockquote class="gmail_quote">quoted lvl1<blockquote>deep lvl2</blockquote></blockquote></div>"#;
        let turns = parse_thread(Some(html), None).expect("turns");
        // level 0 head + level 1 + level 2.
        assert_eq!(levels(&turns), vec![0, 1, 2]);
        // The level-1 body excludes the nested level-2 quote.
        let lvl1 = turns[1]["body_html"].as_str().unwrap();
        assert!(lvl1.contains("quoted lvl1"));
        assert!(!lvl1.contains("deep lvl2"));
        assert!(turns[2]["body_html"].as_str().unwrap().contains("deep lvl2"));
    }

    #[test]
    fn html_no_quote_container_none() {
        let html = "<div>Just a reply with <b>bold</b> text</div>";
        // Normal "no quoted material" -> Ok(None) (NOT the ThreadAborted raise path).
        assert!(matches!(parse_html(html), Ok(None)));
    }

    #[test]
    fn attribution_in_head_aborts_without_plaintext_fallback() {
        // A head chunk whose stripped text trips the attribution guard makes
        // Python reach the malformed CJK `re.sub`, which raises `re.error` and
        // unwinds the WHOLE `parse_thread` (caller catches -> None). The plaintext
        // fallback must NOT be reached even though `body_text` parses on its own.
        let body_html = "<div>On Mon, May 11 2026 Alice alice@example.com wrote:\
                         <blockquote>quoted reply here</blockquote></div>";
        let body_text = "On Mon, May 11 2026 Bob bob@example.com wrote:\n> hello there";
        // Sanity: that body_text DOES parse to a thread on its own...
        assert!(parse_thread(None, Some(body_text)).is_some());
        // ...and the head chunk really does trip the guard (so Python would raise).
        assert!(strip_trailing_attribution("On Mon, May 11 2026 Alice alice@example.com wrote:").is_none());
        // ...but with the raising HTML head present, parse_thread aborts to None
        // (matching Python's caught re.error), NOT the plaintext tree.
        assert!(parse_thread(Some(body_html), Some(body_text)).is_none());
    }

    #[test]
    fn escape_to_html_linkifies_and_brs() {
        let out = escape_to_html("see https://a.com & more\nnext");
        assert!(out.contains(r#"<a href="https://a.com" target="_blank" rel="noopener">https://a.com</a>"#));
        assert!(out.contains("&amp;"));
        assert!(out.contains("<br>"));
    }

    #[test]
    fn splitlines_matches_python_boundaries() {
        assert_eq!(splitlines("a\nb\r\nc\rd"), vec!["a", "b", "c", "d"]);
        // Trailing newline -> no trailing empty element.
        assert_eq!(splitlines("a\n"), vec!["a"]);
        assert_eq!(splitlines("a\u{0b}b"), vec!["a", "b"]);
    }

    /// Full-output parity against the live Python (`src.email_thread_parser`).
    /// The expected JSON is the verbatim output of `parse_thread(...)` for each
    /// fixture, captured from the Python source of truth.
    #[test]
    fn full_parity_against_python() {
        struct Case {
            name: &'static str,
            html: Option<&'static str>,
            text: Option<&'static str>,
            expected: &'static str,
        }
        let cases = [
            Case {
                name: "gmail_attrib_then_quote",
                html: None,
                text: Some("Thanks!\n\nOn Mon, May 5, 2026 at 10:00 AM, Alice <a@x.com> wrote:\n> Hello there\n> second line"),
                expected: r#"[{"level":0,"body_html":"Thanks!","meta":null},{"level":1,"body_html":"Hello there<br>second line","meta":"Alice <a@x.com> · Mon, May 5, 2026 at 10:00 AM"}]"#,
            },
            Case {
                name: "plain_quote_levels",
                html: None,
                text: Some("Top reply\n> quoted level 1\n>> quoted level 2"),
                expected: r#"[{"level":0,"body_html":"Top reply","meta":null},{"level":1,"body_html":"quoted level 1","meta":null},{"level":2,"body_html":"quoted level 2","meta":null}]"#,
            },
            Case {
                name: "cjk",
                html: None,
                text: Some("返信します。\n\n2026年5月11日(月) 21:28 <alice@example.com>:\n> 元のメッセージ"),
                expected: r#"[{"level":0,"body_html":"返信します。","meta":null},{"level":1,"body_html":"元のメッセージ","meta":"alice@example.com · 2026年5月11日(月) 21:28"}]"#,
            },
            Case {
                name: "outlook_block",
                html: None,
                text: Some("My reply text.\n\nFrom: Bob <b@y.com>\nSent: Monday, May 5, 2026\nSubject: Hi\n\nOriginal body line"),
                expected: r#"[{"level":0,"body_html":"My reply text.","meta":null},{"level":1,"body_html":"Original body line","meta":"Bob <b@y.com> · Monday, May 5, 2026"}]"#,
            },
            Case {
                name: "html_nest",
                html: Some(r#"<div>Reply body<blockquote class="gmail_quote">quoted lvl1<blockquote>deep lvl2</blockquote></blockquote></div>"#),
                text: None,
                expected: r#"[{"level":0,"body_html":"Reply body","meta":null},{"level":1,"body_html":"quoted lvl1","meta":null},{"level":2,"body_html":"deep lvl2","meta":null}]"#,
            },
            Case {
                name: "html_apple_cite",
                html: Some(r#"<div>My reply<blockquote type="cite">On May 1, Carol &lt;c@z.com&gt; wrote:<br>old content</blockquote></div>"#),
                text: None,
                expected: r#"[{"level":0,"body_html":"My reply","meta":null},{"level":1,"body_html":"On May 1, Carol &lt;c@z.com&gt; wrote:<br/>old content","meta":"Carol <c@z.com> · May 1"}]"#,
            },
            Case {
                name: "original_message",
                html: None,
                text: Some("Reply text\n\n----- Original Message -----\nFrom: Dave <d@w.com>\nSent: Tue, May 6\n\nBody of original"),
                expected: r#"[{"level":0,"body_html":"Reply text","meta":null},{"level":1,"body_html":"Body of original","meta":"Dave <d@w.com> · Tue, May 6"}]"#,
            },
            Case {
                name: "forwarded_message",
                html: None,
                text: Some("See below.\n\n----- Forwarded message -----\nFrom: Eve <e@v.com>\nSent: Wed, May 7\n\nForwarded body"),
                expected: r#"[{"level":0,"body_html":"See below.","meta":null},{"level":1,"body_html":"Forwarded body","meta":"Eve <e@v.com> · Wed, May 7"}]"#,
            },
            Case {
                name: "korean",
                html: None,
                text: Some("답장입니다.\n\nAlice 님이 작성:\n> 원본 메시지"),
                expected: r#"[{"level":0,"body_html":"답장입니다.","meta":null},{"level":1,"body_html":"원본 메시지","meta":"Alice 님이 작성:"}]"#,
            },
            Case {
                name: "chinese",
                html: None,
                text: Some("回复\n\nBob 写道:\n> 原始内容"),
                expected: r#"[{"level":0,"body_html":"回复","meta":null},{"level":1,"body_html":"原始内容","meta":"Bob 写道:"}]"#,
            },
            // bs4 `html.parser` minimal formatter leaves U+00A0 as the literal
            // character — it does NOT re-encode it as `&nbsp;`. The quoted
            // turn's body_html therefore contains raw NBSP between words.
            Case {
                name: "html_nbsp_literal",
                html: Some("<div>Reply<blockquote class=\"gmail_quote\">a&nbsp;b&nbsp;quoted</blockquote></div>"),
                text: None,
                expected: "[{\"level\":0,\"body_html\":\"Reply\",\"meta\":null},{\"level\":1,\"body_html\":\"a\u{a0}b\u{a0}quoted\",\"meta\":null}]",
            },
        ];
        for c in &cases {
            let got = parse_thread(c.html, c.text);
            let expected: Value = serde_json::from_str(c.expected).unwrap();
            let got_value = serde_json::to_value(&got).unwrap();
            assert_eq!(got_value, expected, "fixture `{}` mismatch", c.name);
        }
    }

    #[test]
    fn version_constant() {
        assert_eq!(THREAD_PARSER_VERSION, 6);
    }
}
