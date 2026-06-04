// src/visual_report.rs  <- src/visual_report.py
//! Generate a self-contained, styled HTML page from deep research results.
//!
//! Takes the markdown report, sources, and stats produced by DeepResearcher
//! and wraps them in an editorial-quality HTML document with:
//! - System/local typography, no remote font provider
//! - Dark/light theme via prefers-color-scheme
//! - Hero section with animated gradient + optional hero image
//! - Inline OG images between sections
//! - Auto-generated table of contents from headings
//! - Collapsible compact sources list
//! - Print/Share toolbar
//!
//! Pure CPU/string module — DEFAULT build, no async / HTTP / DB.
//!
//! ENGINE SWAP (documented observable drift): the Python module renders markdown
//! with `python-markdown` (extensions `extra, codehilite, toc, tables,
//! sane_lists` + Pygments `codehilite`). The Rust port renders with `comrak`
//! (CommonMark + GFM tables / strikethrough / autolinks / footnotes). The
//! consequences are limited and bounded:
//!   * No Pygments `codehilite` syntax-highlight spans inside `<pre>` blocks —
//!     comrak emits plain `<pre><code>` (the CSS class is `code` in Python; the
//!     spans are purely cosmetic and the page CSS never depends on them).
//!   * Heading `id` anchors diverge for non-ASCII / punctuated headings — and
//!     this is actually CLOSER to working than Python. python-markdown's `toc`
//!     extension (enabled in the Python `_md_to_html`) emits `<h2 id="…">`
//!     headings using ITS OWN slugifier (lowercase, transliterate, strip
//!     non-`\w`), so Python's own anchor-injection `re.sub(r'(<h2>)…')` never
//!     matches the already-id'd tags and is effectively dead code — the body
//!     heading ids in Python are always the toc slugifier's output. comrak emits
//!     *bare* `<h2>` headings, so here the `(<h2>)`-prefix regex DOES fire and
//!     injects ids built with the module's `_make_slug` (`[^a-z0-9]+` -> `-`).
//!     The two slug algorithms disagree on common inputs:
//!       - `Café Society` -> toc `cafe-society` (transliterates é) vs `_make_slug`
//!         `caf-society` (drops é entirely);
//!       - `API/SDK Notes` -> toc `apisdk-notes` (strips `/`) vs `_make_slug`
//!         `api-sdk-notes` (replaces `/` with `-`).
//!     So for any heading containing non-ASCII letters or punctuation like `/`,
//!     the body heading `id` (and therefore the TOC/quick-link href it must
//!     match) differs between Python and Rust. Reproducing python-markdown's
//!     exact toc slugifier (Unicode transliteration tables) is out of scope; the
//!     Rust ids are internally consistent (sidebar/quick-link hrefs use the same
//!     `_make_slug`, so in-page navigation resolves), whereas Python's own TOC
//!     links use `_make_slug` while the body ids use the toc slugifier — meaning
//!     Python's anchors are already broken for exactly these inputs and Rust's
//!     resolve. This is a documented, bounded consequence of the engine swap,
//!     NOT byte-for-byte parity of the `id` attribute.
//!   * Minor list / inline-attribute differences between the two CommonMark
//!     dialects are possible but do not affect the structural CSS hooks.
//! Everything else — the template, the per-category CSS, the TOC structure, the
//! image injection, the sources panel, the JS — is a faithful 1:1 port.

use crate::src::research_utils::strip_thinking;
use comrak::{markdown_to_html, Options};
use fancy_regex::Regex as FancyRegex;
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use url::Url;

// ---------------------------------------------------------------------------
// html.escape  (quote=True) parity
// ---------------------------------------------------------------------------

/// `html.escape(s, quote=True)`: `&`->`&amp;`, `<`->`&lt;`, `>`->`&gt;`,
/// `"`->`&quot;`, `'`->`&#x27;`. The `html-escape` crate's
/// `encode_quoted_attribute` uses exactly this five-character table (verified
/// against its `escape_quote` impl), so it is a faithful drop-in for the
/// single-quoted `onerror=` handlers the template relies on.
fn esc(s: &str) -> String {
    html_escape::encode_quoted_attribute(s).into_owned()
}

/// `text[:n]` over a Python `str` — Python slices by code point, not byte.
fn char_slice(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Python `len(str)` — number of Unicode code points (not UTF-8 bytes).
fn char_len(s: &str) -> usize {
    s.chars().count()
}

/// `urlparse(url).hostname or ""` — the displayed source domain.
///
/// Two faithful adjustments over a bare `url::Url::parse(url).host_str()`:
///   * IDN: `url::Url` punycode-encodes the host (`例え.jp` -> `xn--r8jz45g.jp`),
///     but Python's `urlparse(...).hostname` returns the raw Unicode host. We
///     decode the host back to Unicode via `idna::domain_to_unicode` (a no-op
///     for pure-ASCII hosts) so the visible `.sdomain` matches Python.
///   * Scheme-relative URLs (`//foo.com/x`): `urlparse` is lenient and returns
///     `foo.com`, but `url::Url::parse` requires a base and errors. We retry
///     with a synthetic `http:` scheme so the host is still recovered.
/// Anything else that fails to parse yields `""`, matching Python's
/// `hostname or ""` (and `urlparse` never actually raises on a `str`, so the
/// `except Exception: domain = url` branch is dead — empty string is the real
/// Python outcome for a hostless URL).
fn urlparse_hostname(url: &str) -> String {
    let host = match Url::parse(url) {
        Ok(parsed) => parsed.host_str().map(|h| h.to_string()),
        Err(_) => {
            // Scheme-relative `//host/...`: prepend a scheme and retry.
            if url.starts_with("//") {
                Url::parse(&format!("http:{url}"))
                    .ok()
                    .and_then(|p| p.host_str().map(|h| h.to_string()))
            } else {
                None
            }
        }
    };
    match host {
        Some(h) => {
            // Decode punycode back to the Unicode host (no-op for ASCII hosts).
            let (unicode, _result) = idna::domain_to_unicode(&h);
            unicode
        }
        None => String::new(),
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Bare-URL autolink: a `https?://…` run not already inside `](…)` markdown link
// syntax. The two negative lookbehinds `(?<!\]\()(?<!\()` need fancy-regex.
static AUTOLINK_RE: Lazy<FancyRegex> =
    Lazy::new(|| FancyRegex::new(r"(?<!\]\()(?<!\()(https?://[^\s\)<>]+)").unwrap());

/// Convert bare URLs to markdown links before processing.
///
/// Skips URLs already inside markdown link syntax `[text](url)`.
fn autolink_urls(md_text: &str) -> String {
    // `re.sub(pattern, r'[\1](\1)', md_text)` — wrap each bare URL as `[url](url)`.
    AUTOLINK_RE
        .replace_all(md_text, |caps: &fancy_regex::Captures| {
            let u = &caps[1];
            format!("[{u}]({u})")
        })
        .into_owned()
}

// `<a href="(https?://)`  ->  add target="_blank" rel="noopener noreferrer".
static EXTERNAL_LINK_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"<a href="(https?://)"#).unwrap());

/// Convert markdown to HTML with the comrak engine (see the module-level engine
/// swap note). External links are rewritten to open in a new tab.
fn md_to_html(md_text: &str) -> String {
    let md_text = autolink_urls(md_text);

    let mut options = Options::default();
    // `extensions=["extra", "tables", "sane_lists", ...]` — the structural GFM
    // subset python-markdown's `extra`/`tables` enable.
    options.extension.table = true;
    options.extension.strikethrough = true;
    options.extension.autolink = true;
    options.extension.footnotes = true;
    options.extension.tasklist = true;
    // python-markdown passes raw inline/block HTML through unescaped; comrak
    // escapes it by default, so opt into raw-HTML passthrough to match.
    options.render.r#unsafe = true;

    let result = markdown_to_html(&md_text, &options);

    // Make external links open in new tab (verbatim port of the post-process).
    EXTERNAL_LINK_RE
        .replace_all(&result, r#"<a target="_blank" rel="noopener noreferrer" href="$1"#)
        .into_owned()
}

#[derive(Clone)]
struct Heading {
    level: usize,
    text: String,
    slug: String,
}

// `^(#{2,3})\s+(.+)$`  (MULTILINE) — h2/h3 ATX headings.
static HEADING_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?m)^(#{2,3})[ \t]+(.+)$").unwrap());
// `^\*\*([^*]+)\*\*\s*$`  (MULTILINE) — bold-only lines (fallback heading source).
static BOLD_LINE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?m)^\*\*([^*]+)\*\*[ \t]*$").unwrap());
// `[^a-z0-9]+`  — slug normaliser (lower-cased input).
static SLUG_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^a-z0-9]+").unwrap());

// `_plain_heading_text` regexes — strip images, links, HTML tags, and emphasis
// from a raw heading line so the slug and TOC text are clean plain text.
static HEADING_IMG_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"!\[([^\]]*)\]\([^)]+\)").unwrap());
static HEADING_LINK_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\[([^\]]+)\]\([^)]+\)").unwrap());
static HEADING_REF_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\[([^\]]+)\]\[[^\]]+\]").unwrap());
static HEADING_TAG_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"<[^>]+>").unwrap());
static HEADING_EMPH_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[`*_~]+").unwrap());
static HEADING_WS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\s+").unwrap());

/// Strip markdown/HTML markup from a raw heading line, returning plain text.
///
/// Port of the nested `_plain_heading_text` in Python's `_extract_headings`:
///   `text.strip().rstrip('#').strip()`  — trailing `#` (closed ATX headings)
///   then strip images, links, ref-links, HTML tags, and emphasis chars.
///   Then `html.unescape` + collapse whitespace.
fn plain_heading_text(raw: &str) -> String {
    let text = raw.trim().trim_end_matches('#').trim().to_string();
    // `re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', text)` — image alt text only.
    let text = HEADING_IMG_RE.replace_all(&text, "$1").into_owned();
    // `re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)` — inline link text.
    let text = HEADING_LINK_RE.replace_all(&text, "$1").into_owned();
    // `re.sub(r'\[([^\]]+)\]\[[^\]]+\]', r'\1', text)` — reference link text.
    let text = HEADING_REF_RE.replace_all(&text, "$1").into_owned();
    // `re.sub(r'<[^>]+>', '', text)` — strip HTML tags.
    let text = HEADING_TAG_RE.replace_all(&text, "").into_owned();
    // `re.sub(r'[`*_~]+', '', text)` — strip emphasis/code markers.
    let text = HEADING_EMPH_RE.replace_all(&text, "").into_owned();
    // `html.unescape(text)` — decode HTML entities.
    let text = html_escape::decode_html_entities(&text).into_owned();
    // `re.sub(r'\s+', ' ', text).strip()` — normalise whitespace.
    HEADING_WS_RE
        .replace_all(text.trim(), " ")
        .trim()
        .to_string()
}

/// Pull h2/h3 headings from markdown for the table of contents.
fn extract_headings(md_text: &str) -> Vec<Heading> {
    let mut headings: Vec<Heading> = Vec::new();
    // `seen_slugs: Dict[str, int]` — dedup counter, mirrors Python insertion.
    let mut seen_slugs: HashMap<String, i64> = HashMap::new();

    // Closure equivalent of the nested `_make_slug`.
    // Added `"section"` fallback when the normalized slug is empty (Python:
    // `if not slug: slug = "section"`).
    let mut make_slug = |text: &str| -> String {
        let lowered = text.to_lowercase();
        let mut slug = SLUG_RE.replace_all(&lowered, "-").into_owned();
        slug = slug.trim_matches('-').to_string();
        if slug.is_empty() {
            slug = "section".to_string();
        }
        if seen_slugs.contains_key(&slug) {
            let c = seen_slugs.get_mut(&slug).unwrap();
            *c += 1;
            slug = format!("{slug}-{}", *c);
        } else {
            seen_slugs.insert(slug.clone(), 0);
        }
        slug
    };

    for m in HEADING_RE.captures_iter(md_text) {
        let level = m.get(1).unwrap().as_str().len();
        // Apply `_plain_heading_text` to strip markup before slug/text use.
        let text = plain_heading_text(m.get(2).unwrap().as_str());
        // `if not text: continue` — skip empty headings.
        if text.is_empty() {
            continue;
        }
        let slug = make_slug(&text);
        headings.push(Heading { level, text, slug });
    }

    if headings.is_empty() {
        for m in BOLD_LINE_RE.captures_iter(md_text) {
            // Also apply `_plain_heading_text` here (Python's fallback path
            // calls `_plain_heading_text(m.group(1)).rstrip(':')`).
            let text = plain_heading_text(m.get(1).unwrap().as_str())
                .trim_end_matches(':')
                .to_string();
            // `if 3 < len(text) < 80` — code-point length, exclusive bounds.
            let n = char_len(&text);
            if n > 3 && n < 80 {
                let slug = make_slug(&text);
                headings.push(Heading {
                    level: 2,
                    text,
                    slug,
                });
            }
        }
    }
    headings
}

// `<h2>` and `<h3>` tags for positional id injection (BeautifulSoup zip parity).
static H2_H3_TAG_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"<(h[23])(\s[^>]*)?>").unwrap());

/// Force rendered h2/h3 IDs to match the generated sidebar links.
///
/// Python now uses BeautifulSoup to find all `h2`/`h3` elements and zip them
/// positionally with the `headings` list — i.e. the Nth rendered heading gets
/// the slug of the Nth entry in `headings`, regardless of text content. This
/// avoids the old regex-text-match approach which was vulnerable to heading
/// text containing regex metacharacters (injection risk) and silently skipped
/// headings when the rendered HTML text differed from the raw markdown text.
///
/// The Rust port replicates this by scanning `report_html` once to find the
/// byte positions of every `<h2…>` / `<h3…>` open tag in document order, then
/// replacing each matched tag (up to `min(rendered, headings.len())`) with
/// `<hN id="slug">`, preserving any extra attributes already on the tag.
fn apply_heading_ids(report_html: &str, headings: &[Heading]) -> String {
    if headings.is_empty() {
        return report_html.to_string();
    }

    // Collect (start, end, full_match_str) for every <h2…> / <h3…> open tag.
    let tag_spans: Vec<(usize, usize, &str)> = H2_H3_TAG_RE
        .find_iter(report_html)
        .map(|m| (m.start(), m.end(), m.as_str()))
        .collect();

    // Zip positionally: only replace as many tags as we have headings.
    let pairs: Vec<_> = tag_spans.iter().zip(headings.iter()).collect();

    // Build the output in one left-to-right pass, replacing each matched tag.
    let mut out = String::with_capacity(report_html.len() + headings.len() * 20);
    let mut cursor = 0usize;
    for ((start, end, _matched), heading) in &pairs {
        out.push_str(&report_html[cursor..*start]);
        // `element["id"] = heading["slug"]` — inject id, keep tag name.
        let tag_name = format!("h{}", heading.level);
        out.push_str(&format!("<{tag_name} id=\"{}\">", heading.slug));
        cursor = *end;
    }
    out.push_str(&report_html[cursor..]);
    out
}

// Overlay buttons shown on each image: reroll (swap for the next unused
// scraped image) + hide (remove and skip on future renders). Reroll is wired up
// in the page script using the embedded spare-image pool.
const IMG_OVERLAY_BTNS: &str = concat!(
    "<button class=\"img-reroll-btn\" type=\"button\" title=\"Swap for another image\">",
    "<svg width=\"14\" height=\"14\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2.5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><polyline points=\"23 4 23 10 17 10\"/><path d=\"M20.49 15a9 9 0 11-2.12-9.36L23 10\"/></svg>",
    "</button>",
    "<button class=\"img-hide-btn\" type=\"button\" title=\"Hide image\">",
    "<svg width=\"14\" height=\"14\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2.5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><line x1=\"18\" y1=\"6\" x2=\"6\" y2=\"18\"/><line x1=\"6\" y1=\"6\" x2=\"18\" y2=\"18\"/></svg>",
    "</button>"
);

// `/(icon|logo|favicon)([._/-]|$)` IGNORECASE — path-segment boundary check.
// The old naive `.contains("/icon")` check dropped real photos whose slug
// merely contained the word (e.g. `/iconic-moment.jpg`). The regex anchors on
// a word boundary so `/icon.png`, `/logo.svg`, `/favicon.ico` still match but
// `/iconic-moment.jpg` and `/logos-history.png` do not.
static ICON_LOGO_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)/(icon|logo|favicon)([._/-]|$)").unwrap());

/// True if a URL path points at an icon/logo/favicon asset (path-segment boundary).
///
/// Port of Python's `_is_icon_or_logo_url`:
/// ```python
/// _ICON_LOGO_RE = re.compile(r'/(icon|logo|favicon)([._/-]|$)', re.IGNORECASE)
/// def _is_icon_or_logo_url(url: str) -> bool:
///     return bool(_ICON_LOGO_RE.search(url or ""))
/// ```
fn is_icon_or_logo_url(url: &str) -> bool {
    ICON_LOGO_RE.is_match(url)
}

// `</h2>` close-tag positions for image injection.
static H2_CLOSE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"</h2>").unwrap());

/// Insert OG images between h2 sections as figures.
///
/// Returns `(html, consumed)` where `consumed` is how many of `images` were
/// actually placed — the rest become the spare pool for reroll.
fn inject_images(report_html: &str, images: &[String]) -> (String, usize) {
    if images.is_empty() {
        return (report_html.to_string(), 0);
    }

    // Find positions after closing `</h2>` + following paragraph.
    let h2_positions: Vec<usize> = H2_CLOSE_RE
        .find_iter(report_html)
        .map(|m| m.end())
        .collect();
    if h2_positions.is_empty() {
        return (report_html.to_string(), 0);
    }

    // Insert an image after every 2nd heading (skip first heading = title):
    // `insert_after = h2_positions[1::2]`.
    let mut insert_after: Vec<usize> = Vec::new();
    let mut idx = 1usize;
    while idx < h2_positions.len() {
        insert_after.push(h2_positions[idx]);
        idx += 2;
    }

    let mut report_html = report_html.to_string();
    let mut img_idx = 0usize;
    // Work backwards to preserve positions: `for pos in reversed(insert_after)`.
    for &pos in insert_after.iter().rev() {
        if img_idx >= images.len() {
            break;
        }
        let img_url = &images[img_idx];
        img_idx += 1;
        let url_esc = esc(img_url);
        let figure = format!(
            "\n<figure class=\"section-image\" data-img-url=\"{url_esc}\">\
<img src=\"{url_esc}\" alt=\"\" loading=\"lazy\" \
onerror=\"this.parentElement.style.display='none'\">\
{IMG_OVERLAY_BTNS}\
</figure>\n"
        );
        report_html = format!("{}{}{}", &report_html[..pos], figure, &report_html[pos..]);
    }

    (report_html, img_idx)
}

// ---------------------------------------------------------------------------
// HTML template
//
// PORTING NOTE: the Python template is a single `str.format()` call. In the
// Python source every literal CSS / JS brace is *doubled* (`{{` / `}}`) so it
// survives `.format()`, and the substitution tokens are single-braced `{name}`.
// Here the template is a plain Rust raw string with SINGLE braces everywhere
// (CSS/JS braces are literal) and the substitution tokens kept as `{name}`. We
// substitute them with `render_template` (a single left-to-right pass that
// matches each known `{name}` token exactly once at its position), which is the
// faithful equivalent of `str.format()` — a value inserted for one token is
// never re-scanned for another token.
// ---------------------------------------------------------------------------

const TEMPLATE: &str = include_str!("visual_report_template.html");

/// Single-pass `{name}` token substitution, mirroring Python `str.format()`
/// semantics: the template is scanned once, each `{name}` placeholder is
/// replaced by its mapped value, and substituted values are NOT re-scanned for
/// further placeholders. Unknown `{…}` runs (none exist in the template — every
/// CSS/JS brace is a literal single brace and never spells a known token) are
/// copied verbatim.
fn render_template(template: &str, vars: &[(&str, &str)]) -> String {
    let lookup: HashMap<&str, &str> = vars.iter().copied().collect();
    let mut out = String::with_capacity(template.len() + 4096);
    let bytes = template.as_bytes();
    let mut i = 0usize;
    while i < bytes.len() {
        if bytes[i] == b'{' {
            // Try to read a `{name}` token where name is one of our keys.
            if let Some(close) = template[i + 1..].find('}') {
                let name = &template[i + 1..i + 1 + close];
                if let Some(val) = lookup.get(name) {
                    out.push_str(val);
                    i = i + 1 + close + 1;
                    continue;
                }
            }
            // Not a known token — emit the literal brace and advance one byte.
            out.push('{');
            i += 1;
        } else {
            // Copy the next char (handles multi-byte UTF-8 cleanly).
            let ch = template[i..].chars().next().unwrap();
            out.push(ch);
            i += ch.len_utf8();
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Per-category CSS appended to the template's `{category_css}` slot.
///
/// Always emits the per-category palette block when ANY category is set — it
/// contains `body.category-X`-scoped rules so it only re-skins the page for the
/// matching category. The legacy `styles[category]` block adds structural CSS
/// specific to that one type.
fn category_css(category: Option<&str>) -> String {
    let category = match category {
        Some(c) if !c.is_empty() => c,
        _ => return String::new(),
    };

    // The palette + per-category structural block (`palettes` in Python). This
    // is a literal CSS string in the Python source.
    let palettes = include_str!("visual_report_palettes.css");

    // `styles` dict — structural CSS keyed by category.
    let style = match category {
        "product" => include_str!("visual_report_style_product.css"),
        "comparison" => include_str!("visual_report_style_comparison.css"),
        "howto" => include_str!("visual_report_style_howto.css"),
        "landscape" => include_str!("visual_report_style_landscape.css"),
        "factcheck" => include_str!("visual_report_style_factcheck.css"),
        _ => "",
    };

    format!("{palettes}{style}")
}

// Generic placeholder headings skipped when choosing a report title.
static GENERIC_HEADINGS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "report",
        "deep research report",
        "research",
        "executive summary",
        "summary",
        "tl;dr",
        "introduction",
        "overview",
        "abstract",
        "findings",
        "key findings",
        "results",
        "conclusion",
        "conclusions",
        "table of contents",
    ]
    .into_iter()
    .collect()
});

// `^# +(.+?)\s*$` and `^## +(.+?)\s*$` (MULTILINE) — title candidate sources.
static H1_TITLE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?m)^# +(.+?)[ \t]*$").unwrap());
static H2_TITLE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?m)^## +(.+?)[ \t]*$").unwrap());

/// Pull a real title from the report's first heading rather than reusing the
/// raw user query. Returns `(title, markdown_with_title_stripped)`.
///
/// Falls back to the query when no heading is present. Skips generic
/// placeholders ("Executive Summary", "Introduction", …) and tries the next
/// heading. If the chosen title was the report's own top heading, that heading
/// is removed from the markdown so it doesn't duplicate the hero h1.
fn extract_report_title(markdown_text: &str, fallback: &str) -> (String, String) {
    if markdown_text.is_empty() {
        return (fallback.to_string(), markdown_text.to_string());
    }

    // (level, match_start, match_end, candidate_title)
    let mut candidates: Vec<(usize, usize, usize, String)> = Vec::new();
    for (level, re) in [(1usize, &*H1_TITLE_RE), (2usize, &*H2_TITLE_RE)] {
        for m in re.captures_iter(markdown_text) {
            // `m.group(1).strip().rstrip('#').strip()`.
            let cand = m
                .get(1)
                .unwrap()
                .as_str()
                .trim()
                .trim_end_matches('#')
                .trim()
                .to_string();
            if !cand.is_empty() && !GENERIC_HEADINGS.contains(cand.to_lowercase().as_str()) {
                let whole = m.get(0).unwrap();
                candidates.push((level, whole.start(), whole.end(), cand));
            }
        }
    }

    // Prefer h1 over h2; among same-level, prefer the earliest.
    // `candidates.sort(key=lambda t: (t[0], t[1].start()))` — stable.
    candidates.sort_by_key(|t| (t.0, t.1));

    if let Some((_level, start, end, title)) = candidates.into_iter().next() {
        // `markdown_text[:match.start()] + markdown_text[match.end():]`, then
        // `.lstrip()` — strip Unicode leading whitespace.
        let mut stripped = String::with_capacity(markdown_text.len());
        stripped.push_str(&markdown_text[..start]);
        stripped.push_str(&markdown_text[end..]);
        let stripped = stripped.trim_start().to_string();
        return (title, stripped);
    }
    (fallback.to_string(), markdown_text.to_string())
}

// `^#{2,3}\s+` — "are there any markdown headings?" probe (MULTILINE search).
static HEADING_PROBE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?m)^#{2,3}[ \t]+").unwrap());
// `[#*_\[\]()]` — strip markdown punctuation for the meta description.
static DESC_STRIP_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[#*_\[\]()]").unwrap());

/// `str(val)` for a JSON stats value, Python-style: strings render *without*
/// quotes, ints/floats render plainly, bools as `True`/`False`. Mirrors
/// `str(stats.get(key))` — the stats panel renders raw values, not JSON.
fn py_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Bool(b) => {
            if *b {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        Value::Null => "None".to_string(),
        other => other.to_string(),
    }
}

/// CPython `json.dumps` defaults to `ensure_ascii=True`: every non-ASCII code
/// point is escaped as `\uXXXX` (astral chars as a UTF-16 surrogate pair),
/// lowercase hex. serde_json writes raw UTF-8 instead, so we post-process the
/// serialized text to match byte-for-byte. This mirrors the same fix already
/// applied to `atomic_io::ensure_ascii`; it is safe because, after
/// serialization, the only non-ASCII bytes live inside JSON string values
/// (serde already escapes `"`, `\`, and control chars as ASCII).
fn ensure_ascii(s: &str) -> String {
    if s.is_ascii() {
        return s.to_string();
    }
    let mut out = String::with_capacity(s.len());
    let mut buf = [0u16; 2];
    for ch in s.chars() {
        if (ch as u32) < 0x80 {
            out.push(ch);
        } else {
            for unit in ch.encode_utf16(&mut buf) {
                out.push_str(&format!("\\u{:04x}", unit));
            }
        }
    }
    out
}

/// JSON-encode a value safe to embed inside a `<script>` block.
///
/// `json.dumps` doesn't escape `/`, so a string containing the literal
/// substring `</script>` would terminate the script element early. Escape the
/// closing slash to keep the inline JSON inert as HTML.
fn json_for_script(value: &Value) -> String {
    // `json.dumps(value)` — serde_json's default compact form matches
    // json.dumps' separators for the simple values used here (strings / arrays
    // of strings). Apply `ensure_ascii=True` (CPython default) so non-ASCII code
    // points become `\uXXXX`, THEN `.replace("</", "<\\/")` — same order as
    // Python (`json.dumps(...).replace("</", "<\\/")`).
    let dumped = serde_json::to_string(value).unwrap_or_else(|_| "null".to_string());
    ensure_ascii(&dumped).replace("</", "<\\/")
}

/// JSON-encode a string so it's safe to embed inside a `<script>` block.
fn json_dumps_str(s: &str) -> String {
    json_for_script(&Value::String(s.to_string()))
}

/// Generate the full self-contained HTML report.
///
/// Faithful port of `visual_report.generate_visual_report`. `sources` is the
/// list of source dicts (objects with `url` / `title` / `image` keys), `stats`
/// the metric object rendered in the stats bar.
#[allow(clippy::too_many_arguments)]
pub fn generate_visual_report(
    question: &str,
    report_markdown: &str,
    sources: Option<&[Value]>,
    stats: Option<&Value>,
    category: Option<&str>,
    session_id: Option<&str>,
    hidden_images: Option<&[String]>,
) -> String {
    let sources: &[Value] = sources.unwrap_or(&[]);
    // `hidden_images_set = set(hidden_images or [])`.
    let hidden_images_set: HashSet<&str> = hidden_images
        .unwrap_or(&[])
        .iter()
        .map(|s| s.as_str())
        .collect();

    // Strip thinking artifacts.
    let mut report_markdown = strip_thinking(Some(report_markdown)).unwrap_or_default();

    // Use the report's first heading as the title (synthesized by the LLM)
    // rather than the raw user query. Fall back to the query if absent.
    let (synthesized, stripped) = extract_report_title(&report_markdown, question);
    report_markdown = stripped;
    // `synthesized[:120] + ("..." if len(synthesized) > 120 else "")`.
    let title_text = if char_len(&synthesized) > 120 {
        format!("{}...", char_slice(&synthesized, 120))
    } else {
        char_slice(&synthesized, 120)
    };

    // Promote bold-only lines to ## headings if no markdown headings exist.
    if !HEADING_PROBE_RE.is_match(&report_markdown) {
        report_markdown = BOLD_LINE_RE
            .replace_all(&report_markdown, |caps: &regex::Captures| {
                format!("## {}", caps.get(1).unwrap().as_str().trim())
            })
            .into_owned();
    }

    let mut report_html = md_to_html(&report_markdown);

    // Add id anchors to h2/h3 for TOC linking — positional zip (BeautifulSoup
    // parity), not a text-content regex (which was injection-prone and silently
    // missed headings when rendered HTML diverged from markdown source).
    let headings = extract_headings(&report_markdown);
    report_html = apply_heading_ids(&report_html, &headings);

    // Collect all OG images from sources (skip icons, tiny images, known junk).
    // The naive `.contains("/icon")` etc. checks were replaced by the precise
    // `is_icon_or_logo_url` regex (path-segment boundary) so real photos whose
    // slug merely contains "icon"/"logo" (e.g. `/iconic-moment.jpg`) are kept.
    const IMAGE_BLOCKLIST: &[&str] = &["cdn.shopify.com/s/files/1/0179/4388/7926/files/icon.png"];
    let mut seen_images: HashSet<String> = HashSet::new();
    let mut all_images: Vec<String> = Vec::new();
    for s in sources {
        let img = s.get("image").and_then(|v| v.as_str()).unwrap_or("");
        if !img.is_empty()
            && img.starts_with("https://")
            && !seen_images.contains(img)
            && !hidden_images_set.contains(img)
            // `not img.endswith((".svg", ".ico", ".gif"))`
            && ![".svg", ".ico", ".gif"].iter().any(|e| img.ends_with(e))
            && !IMAGE_BLOCKLIST.iter().any(|b| img.contains(b))
            && !is_icon_or_logo_url(img)
        {
            seen_images.insert(img.to_string());
            all_images.push(img.to_string());
        }
    }

    // Hero image = first available. data-img-url drives the per-image hide
    // button rendered by the script at the bottom of the page.
    let mut hero_image_html = String::new();
    if !all_images.is_empty() {
        let hero_url = esc(&all_images[0]);
        hero_image_html = format!(
            "<div class=\"hero-image\" data-img-url=\"{hero_url}\">\
<img src=\"{hero_url}\" alt=\"\" loading=\"lazy\" \
onerror=\"this.parentElement.style.display='none'\">\
{IMG_OVERLAY_BTNS}\
</div>"
        );
    }

    // Product quick-links bar.
    if category == Some("product") && !headings.is_empty() {
        let product_headings: Vec<&Heading> =
            headings.iter().filter(|h| h.level == 3).collect();
        if !product_headings.is_empty() {
            let pills = product_headings
                .iter()
                .map(|h| {
                    format!(
                        "<a href=\"#{}\" class=\"quick-link\">{}</a>",
                        h.slug,
                        esc(&char_slice(&h.text, 40))
                    )
                })
                .collect::<Vec<_>>()
                .join(" ");
            report_html = format!("<div class=\"quick-links-bar\">{pills}</div>\n{report_html}");
        }
    }

    // Inject remaining images between sections. Whatever isn't placed (hero took
    // [0], sections took the next `consumed`) becomes the spare pool the reroll
    // button draws from to swap out an irrelevant image in-page.
    let section_pool: Vec<String> = if all_images.len() > 1 {
        all_images[1..].to_vec()
    } else {
        Vec::new()
    };
    let (injected_html, consumed) = inject_images(&report_html, &section_pool);
    report_html = injected_html;
    let spare_images: Vec<String> = section_pool[consumed..].to_vec();

    // Build TOC.
    let mut toc_lines: Vec<String> = Vec::new();
    for h in &headings {
        let depth_class = format!("depth-{}", h.level);
        toc_lines.push(format!(
            "<a href=\"#{}\" class=\"{depth_class}\">{}</a>",
            h.slug,
            esc(&h.text)
        ));
    }
    let toc_html = if toc_lines.is_empty() {
        String::new()
    } else {
        toc_lines.join("\n      ")
    };

    // Build stats bar.
    let stat_pairs: [(&str, &str); 6] = [
        ("Duration", "Duration"),
        ("Rounds", "Rounds"),
        ("Queries", "Queries"),
        ("URLs", "URLs Analyzed"),
        ("Model", "Model"),
        ("Search", "Search"),
    ];
    let mut stat_items: Vec<String> = Vec::new();
    for (key, label) in stat_pairs {
        // `val = stats.get(key); if val is not None`.
        let val = stats.and_then(|s| s.get(key));
        if let Some(v) = val {
            if !v.is_null() {
                stat_items.push(format!(
                    "<div class=\"stat\"><span class=\"stat-value\">{}</span> {}</div>",
                    esc(&py_str(v)),
                    esc(label)
                ));
            }
        }
    }
    let stats_html = stat_items.join("\n  ");

    // Build sources panel — compact collapsible list.
    let mut sources_html = String::new();
    if !sources.is_empty() {
        let mut items: Vec<String> = Vec::new();
        for (i0, s) in sources.iter().enumerate() {
            let i = i0 + 1; // `enumerate(sources, 1)`.
            let url = s.get("url").and_then(|v| v.as_str()).unwrap_or("");
            // `html.escape(s.get("title", "") or url)`.
            let raw_title = s.get("title").and_then(|v| v.as_str()).unwrap_or("");
            let title = esc(if raw_title.is_empty() { url } else { raw_title });
            // `domain = urlparse(url).hostname or ""`, strip leading `www.`.
            let mut domain = urlparse_hostname(url);
            if let Some(rest) = domain.strip_prefix("www.") {
                domain = rest.to_string();
            }
            items.push(format!(
                "<a href=\"{}\" target=\"_blank\" rel=\"noopener noreferrer\">\
<span class=\"snum\">{i}.</span>\
<span>{title}</span>\
<span class=\"sdomain\">{}</span>\
</a>",
                esc(url),
                esc(&domain)
            ));
        }
        sources_html = format!(
            "<div class=\"sources-panel\">\n\
<details>\n\
<summary>Sources ({})</summary>\n\
<div class=\"sources-list\">\n{}\n</div>\n</details>\n</div>",
            sources.len(),
            items.join("\n")
        );
    }

    // `datetime.now().strftime("%B %d, %Y at %H:%M")` — naive *local* time.
    let timestamp = chrono::Local::now()
        .format("%B %d, %Y at %H:%M")
        .to_string();

    // Build description for OG/meta tags (first 160 chars of plain text).
    let desc_stripped = DESC_STRIP_RE.replace_all(&report_markdown, "");
    let desc_text = char_slice(&desc_stripped, 160).trim().to_string();

    let mut og_image_meta = String::new();
    if !all_images.is_empty() {
        og_image_meta = format!(
            "<meta property=\"og:image\" content=\"{}\">",
            esc(&all_images[0])
        );
    }

    let mut chat_cta_html = String::new();
    if let Some(sid) = session_id {
        chat_cta_html = format!(
            "<div class=\"chat-cta\">\
<button id=\"btn-chat-about\" class=\"chat-cta-btn\" \
data-research-id=\"{}\">\
<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" \
stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\" \
width=\"18\" height=\"18\">\
<path d=\"M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z\"/>\
</svg>\
<span>Discuss</span>\
</button>\
<div class=\"chat-cta-hint\">Opens a new chat with this report as context.</div>\
</div>",
            esc(sid)
        );
    }

    // "Restore hidden images" toolbar button — only render if there are any
    // hidden images on this research AND we have a session_id (needed for the
    // POST endpoint).
    let mut restore_btn_html = String::new();
    if session_id.is_some() && !hidden_images_set.is_empty() {
        let n = hidden_images_set.len();
        let plural = if n == 1 { "" } else { "s" };
        restore_btn_html = format!(
            "<button id=\"btn-restore-images\" type=\"button\" \
title=\"Restore {n} hidden image{plural}\">\
<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" \
stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\">\
<path d=\"M1 4v6h6\"/><path d=\"M3.51 15a9 9 0 1 0 2.13-9.36L1 10\"/>\
</svg>\
Show hidden ({n})\
</button>"
        );
    }

    let body_class = match category {
        Some(c) if !c.is_empty() => format!("category-{c}"),
        _ => String::new(),
    };
    let category_css_str = category_css(category);
    let session_id_js = json_dumps_str(session_id.unwrap_or(""));
    let spare_images_js = json_for_script(&Value::Array(
        spare_images.iter().map(|s| Value::String(s.clone())).collect(),
    ));
    let title_esc = esc(&title_text);
    let desc_esc = esc(&desc_text);
    let question_html = esc(&synthesized);

    render_template(
        TEMPLATE,
        &[
            ("title", &title_esc),
            ("description", &desc_esc),
            ("og_image_meta", &og_image_meta),
            ("question_html", &question_html),
            ("hero_image_html", &hero_image_html),
            ("stats_html", &stats_html),
            ("toc_html", &toc_html),
            ("report_html", &report_html),
            ("sources_html", &sources_html),
            ("chat_cta_html", &chat_cta_html),
            ("restore_btn_html", &restore_btn_html),
            ("timestamp", &timestamp),
            ("category_css", &category_css_str),
            ("body_class", &body_class),
            ("session_id_js", &session_id_js),
            ("spare_images_js", &spare_images_js),
        ],
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn esc_matches_python_html_escape() {
        // html.escape(s, quote=True): & < > " '  -> the five-entity table.
        assert_eq!(
            esc("a & b < c > d \" e ' f"),
            "a &amp; b &lt; c &gt; d &quot; e &#x27; f"
        );
    }

    #[test]
    fn char_slice_is_codepoint_based() {
        // "héllo" sliced to 3 code points = "hél" (not byte-truncated).
        assert_eq!(char_slice("héllo", 3), "hél");
        assert_eq!(char_len("héllo"), 5);
    }

    #[test]
    fn autolink_skips_existing_links() {
        // A bare URL is wrapped; one already inside ](…) is left alone.
        let out = autolink_urls("see https://a.com and [x](https://b.com)");
        assert!(out.contains("[https://a.com](https://a.com)"));
        assert!(out.contains("[x](https://b.com)"));
        // The already-linked URL must not be double-wrapped.
        assert!(!out.contains("[https://b.com](https://b.com)"));
    }

    #[test]
    fn extract_headings_slug_dedup() {
        let md = "## Intro\n\nbody\n\n### Intro\n\nmore";
        let hs = extract_headings(md);
        assert_eq!(hs.len(), 2);
        assert_eq!(hs[0].slug, "intro");
        // Second occurrence of the same slug gets a `-1` suffix.
        assert_eq!(hs[1].slug, "intro-1");
        assert_eq!(hs[0].level, 2);
        assert_eq!(hs[1].level, 3);
    }

    #[test]
    fn extract_headings_bold_fallback() {
        // No ATX headings -> bold-only lines (3 < len < 80) become level-2.
        let md = "**Findings of Note**\n\nsome text\n\n**Hi**\n";
        let hs = extract_headings(md);
        // "Findings of Note" qualifies (len 16, >3 and <80); "Hi" (len 2) does not.
        assert_eq!(hs.len(), 1);
        assert_eq!(hs[0].text, "Findings of Note");
        assert_eq!(hs[0].level, 2);
    }

    #[test]
    fn extract_report_title_skips_generic_and_strips() {
        let md = "# Executive Summary\n\n## The Real Title\n\nbody here";
        let (title, stripped) = extract_report_title(md, "fallback query");
        // h1 "Executive Summary" is generic -> next candidate wins.
        assert_eq!(title, "The Real Title");
        // The chosen heading line is removed from the markdown.
        assert!(!stripped.contains("## The Real Title"));
        // ... and the document is lstripped.
        assert!(stripped.starts_with("# Executive Summary") || stripped.starts_with("body"));
    }

    #[test]
    fn extract_report_title_prefers_h1() {
        let md = "## Second\n\n# First Real\n\nbody";
        let (title, _stripped) = extract_report_title(md, "q");
        // h1 outranks h2 regardless of position.
        assert_eq!(title, "First Real");
    }

    #[test]
    fn extract_report_title_fallback_when_only_generic() {
        let md = "# Summary\n\n## Overview\n\nbody";
        let (title, stripped) = extract_report_title(md, "my query");
        assert_eq!(title, "my query");
        // Nothing stripped when fallback is used.
        assert_eq!(stripped, md);
    }

    #[test]
    fn py_str_no_quotes_for_strings() {
        assert_eq!(py_str(&json!("Tavily")), "Tavily");
        assert_eq!(py_str(&json!(42)), "42");
        assert_eq!(py_str(&json!(true)), "True");
    }

    #[test]
    fn json_for_script_escapes_close_tag() {
        let v = Value::Array(vec![Value::String("</script>".to_string())]);
        let out = json_for_script(&v);
        // The closing slash must be escaped so the inline JSON stays inert.
        assert!(!out.contains("</"));
        assert!(out.contains("<\\/script>"));
    }

    #[test]
    fn json_for_script_ensure_ascii() {
        // CPython `json.dumps(["https://x.com/é"])` -> `["https://x.com/é"]`.
        let v = Value::Array(vec![Value::String("https://x.com/é".to_string())]);
        let out = json_for_script(&v);
        assert_eq!(out, "[\"https://x.com/\\u00e9\"]");
        // BMP + astral: surrogate pair, lowercase hex (matches json.dumps).
        let v2 = Value::String("a—😀".to_string());
        let out2 = json_for_script(&v2);
        assert_eq!(out2, "\"a\\u2014\\ud83d\\ude00\"");
    }

    #[test]
    fn urlparse_hostname_idn_and_scheme_relative() {
        // IDN: punycode decoded back to the Unicode host (matches urlparse).
        assert_eq!(urlparse_hostname("https://例え.jp/x"), "例え.jp");
        // Scheme-relative URL: urlparse returns the host; url::Url needs a base,
        // so we retry with a synthetic scheme.
        assert_eq!(urlparse_hostname("//foo.com/x"), "foo.com");
        // Plain ASCII host is unchanged (idna decode is a no-op).
        assert_eq!(urlparse_hostname("https://www.example.com/a"), "www.example.com");
        // Hostless / unparseable -> "" (Python `hostname or ""`).
        assert_eq!(urlparse_hostname("not a url"), "");
    }

    #[test]
    fn render_template_single_pass() {
        // A value inserted for one token is never re-scanned for another.
        let tmpl = "a={a} b={b} css{literal}";
        let out = render_template(tmpl, &[("a", "{b}"), ("b", "X")]);
        // `{a}` -> literal "{b}" (NOT re-substituted), `{b}` -> "X",
        // `{literal}` is an unknown token -> copied verbatim.
        assert_eq!(out, "a={b} b=X css{literal}");
    }

    #[test]
    fn full_report_has_structure_and_substitutions() {
        let sources = vec![json!({
            "url": "https://www.example.com/path",
            "title": "Example Source",
            "image": "https://img.example.com/photo.jpg"
        })];
        let stats = json!({ "Rounds": 3, "Model": "gpt-x" });
        let html = generate_visual_report(
            "What is the best widget?",
            "# Findings\n\n## The Best Widget\n\nText about widgets. See https://ref.com here.",
            Some(&sources),
            Some(&stats),
            Some("comparison"),
            Some("sess-123"),
            None,
        );
        // No unsubstituted placeholders remain.
        assert!(!html.contains("{report_html}"));
        assert!(!html.contains("{stats_html}"));
        assert!(!html.contains("{title}"));
        // Doctype + body class from the category.
        assert!(html.starts_with("<!DOCTYPE html>"));
        assert!(html.contains("class=\"category-comparison\""));
        // The hero h1 (the chosen title) came from the non-generic heading,
        // not the raw query: the hero block renders `<h1>{question_html}</h1>`.
        assert!(html.contains("<h1>The Best Widget</h1>"));
        // The generic "Findings" h1 was NOT chosen as the title (so the hero
        // h1 is not it); it stays in the body markdown and comrak renders it as
        // a plain `<h1>` — faithful to Python (only the chosen heading line is
        // stripped from the source).
        assert!(html.matches("<h1>").count() >= 2);
        // Stats rendered without JSON quoting.
        assert!(html.contains("<span class=\"stat-value\">3</span> Rounds"));
        assert!(html.contains("<span class=\"stat-value\">gpt-x</span> Model"));
        // Hero image placed and overlay buttons present.
        assert!(html.contains("class=\"hero-image\""));
        assert!(html.contains("img-reroll-btn"));
        // Sources panel with the www-stripped domain.
        assert!(html.contains("Sources (1)"));
        assert!(html.contains("example.com"));
        // Chat CTA present because session_id was supplied.
        assert!(html.contains("data-research-id=\"sess-123\""));
        // The bare URL in the body was autolinked + opened in a new tab.
        assert!(html.contains("target=\"_blank\" rel=\"noopener noreferrer\" href=\"https://ref.com"));
        // session_id embedded in the script as JSON.
        assert!(html.contains("\"sess-123\""));
    }

    #[test]
    fn no_session_no_cta_no_restore() {
        let html = generate_visual_report(
            "q",
            "## Heading\n\nbody",
            None,
            None,
            None,
            None,
            None,
        );
        // The chat-CTA *button* is not rendered (no session id), so the
        // data-research-id attribute is absent. NOTE: the literal id
        // "btn-chat-about" still appears in the always-embedded page JS
        // (`getElementById('btn-chat-about')`), exactly as in Python — so we
        // assert on the rendered markup (the CTA div), not the JS reference.
        assert!(!html.contains("class=\"chat-cta\""));
        assert!(!html.contains("data-research-id"));
        // The restore button is not rendered either (the JS reference to
        // btn-restore-images + the "Show hidden (N)" code comment are always
        // present in the embedded script; the actual <button> is not).
        assert!(!html.contains("id=\"btn-restore-images\" type=\"button\""));
        // No category -> empty body class, no category CSS palette block.
        assert!(html.contains("<body class=\"\">"));
        assert!(!html.contains("category-product"));
    }

    #[test]
    fn restore_button_pluralizes() {
        let hidden = vec!["https://x.com/a.jpg".to_string()];
        let html = generate_visual_report(
            "q",
            "## H\n\nbody",
            None,
            None,
            None,
            Some("s1"),
            Some(&hidden),
        );
        // Singular form for exactly one hidden image.
        assert!(html.contains("Show hidden (1)"));
        assert!(html.contains("Restore 1 hidden image\""));
    }

    // ------------------------------------------------------------------
    // Tests for parity changes (upstream 7c7ac10)
    // ------------------------------------------------------------------

    #[test]
    fn is_icon_or_logo_url_precise_boundary() {
        // Must match: icon/logo/favicon at a path-segment or basename boundary.
        assert!(is_icon_or_logo_url("https://example.com/icon.png"));
        assert!(is_icon_or_logo_url("https://example.com/logo.svg"));
        assert!(is_icon_or_logo_url("https://example.com/favicon.ico"));
        assert!(is_icon_or_logo_url("https://example.com/assets/icon-48.png"));
        assert!(is_icon_or_logo_url("https://example.com/assets/logo/brand.png"));
        assert!(is_icon_or_logo_url("https://example.com/assets/ICON.PNG")); // case-insensitive
        // Must NOT match: the word appears as part of a longer slug (real photos).
        assert!(!is_icon_or_logo_url("https://example.com/iconic-moment.jpg"));
        assert!(!is_icon_or_logo_url("https://example.com/logos-history.png"));
        assert!(!is_icon_or_logo_url("https://example.com/photo.jpg"));
        // Edge: empty string / no slash before the keyword.
        assert!(!is_icon_or_logo_url(""));
        // The word after a slash but then more alphanumeric chars — not a boundary.
        assert!(!is_icon_or_logo_url("https://example.com/iconography/photo.jpg"));
    }

    #[test]
    fn image_filter_keeps_iconic_photo() {
        // A real photo whose path contains "iconic" would previously have been
        // dropped by the naive `.contains("/icon")` check. With `is_icon_or_logo_url`
        // it is kept.
        let sources = vec![json!({
            "url": "https://example.com/page",
            "title": "Page",
            "image": "https://img.example.com/iconic-moment.jpg"
        })];
        let html = generate_visual_report(
            "q",
            "## Section\n\nbody",
            Some(&sources),
            None,
            None,
            None,
            None,
        );
        // The image must appear (hero or section) — it was NOT filtered out.
        assert!(html.contains("iconic-moment.jpg"));
    }

    #[test]
    fn image_filter_still_drops_real_icon() {
        // Actual icon URLs at a boundary must still be filtered.
        let sources = vec![json!({
            "url": "https://example.com/page",
            "title": "Page",
            "image": "https://img.example.com/icon.png"
        })];
        let html = generate_visual_report(
            "q",
            "## Section\n\nbody",
            Some(&sources),
            None,
            None,
            None,
            None,
        );
        assert!(!html.contains("icon.png"));
    }

    #[test]
    fn plain_heading_text_strips_markup() {
        // Images: keep alt text.
        assert_eq!(
            plain_heading_text("![Alt text](https://img.example.com/x.png)"),
            "Alt text"
        );
        // Inline links: keep link text.
        assert_eq!(
            plain_heading_text("[My Link](https://example.com)"),
            "My Link"
        );
        // Reference links.
        assert_eq!(
            plain_heading_text("[My Ref][ref-id]"),
            "My Ref"
        );
        // HTML tags stripped.
        assert_eq!(plain_heading_text("<em>Bold</em> text"), "Bold text");
        // Emphasis markers stripped.
        assert_eq!(plain_heading_text("**Strong** and *em*"), "Strong and em");
        // Trailing # (closed ATX heading) stripped.
        assert_eq!(plain_heading_text("Title ###"), "Title");
        // Whitespace normalised.
        assert_eq!(plain_heading_text("  foo   bar  "), "foo bar");
        // HTML entities decoded.
        assert_eq!(plain_heading_text("foo &amp; bar"), "foo & bar");
    }

    #[test]
    fn extract_headings_empty_after_strip_is_skipped() {
        // A heading that is only markup (e.g. an image with no alt text) should
        // produce an empty plain text after stripping and be skipped entirely.
        let md = "## ![](https://img.example.com/hero.png)\n\n## Real Section\n\nbody";
        let hs = extract_headings(md);
        // The all-image heading collapses to "" and must be skipped.
        assert_eq!(hs.len(), 1);
        assert_eq!(hs[0].text, "Real Section");
    }

    #[test]
    fn extract_headings_section_slug_fallback() {
        // A heading whose text survives plain_heading_text but normalises to
        // all non-alphanumeric chars for SLUG_RE gets the "section" fallback slug.
        //
        // "§" is not an emphasis/HTML marker so plain_heading_text keeps it,
        // but SLUG_RE strips it entirely -> empty slug -> "section" fallback.
        let md = "## §\n\nbody";
        let hs = extract_headings(md);
        assert_eq!(hs.len(), 1);
        assert_eq!(hs[0].slug, "section");

        // "---": dashes are not in [` * _ ~] so plain_heading_text returns "---"
        // (non-empty), SLUG_RE strips to "" -> "section" fallback.
        let md2 = "## ---\n\nbody";
        let hs2 = extract_headings(md2);
        assert_eq!(hs2.len(), 1);
        assert_eq!(hs2[0].slug, "section");

        // Empty-heading skip: an image with no alt text -> plain_heading_text("")
        // returns "" -> heading is skipped entirely (not even a "section" slug).
        let md3 = "## ![](https://x.com/img.png)\n\n## Real\n\nbody";
        let hs3 = extract_headings(md3);
        assert_eq!(hs3.len(), 1);
        assert_eq!(hs3[0].text, "Real");
    }

    #[test]
    fn apply_heading_ids_positional_not_by_text() {
        // The old regex approach matched headings by text content, which could
        // fail when rendered HTML differed from source (e.g. entity encoding).
        // The new positional approach zips by document order regardless of text.
        let html = "<h2>First</h2><p>body</p><h3>Second</h3><p>more</p>";
        let headings = vec![
            Heading { level: 2, text: "First".to_string(), slug: "first".to_string() },
            Heading { level: 3, text: "Second".to_string(), slug: "second".to_string() },
        ];
        let out = apply_heading_ids(html, &headings);
        assert!(out.contains("<h2 id=\"first\">First</h2>"));
        assert!(out.contains("<h3 id=\"second\">Second</h3>"));
    }

    #[test]
    fn apply_heading_ids_fewer_headings_than_rendered() {
        // If fewer headings than rendered tags, only the first N get IDs.
        let html = "<h2>A</h2><h2>B</h2><h2>C</h2>";
        let headings = vec![
            Heading { level: 2, text: "A".to_string(), slug: "a".to_string() },
            Heading { level: 2, text: "B".to_string(), slug: "b".to_string() },
        ];
        let out = apply_heading_ids(html, &headings);
        assert!(out.contains("<h2 id=\"a\">A</h2>"));
        assert!(out.contains("<h2 id=\"b\">B</h2>"));
        // Third heading gets no id (no heading entry for it).
        assert!(out.contains("<h2>C</h2>"));
    }

    #[test]
    fn apply_heading_ids_empty_headings_is_noop() {
        let html = "<h2>A</h2>";
        let out = apply_heading_ids(html, &[]);
        assert_eq!(out, html);
    }

    #[test]
    fn heading_ids_in_full_report() {
        // End-to-end: `extract_report_title` picks the first non-generic heading
        // as the page title and STRIPS it from the body markdown. With the input
        // below, "## First Section" is chosen as the title (it is the first
        // non-generic h2) and removed from the body before rendering, so:
        //   - The hero <h1> reads "First Section".
        //   - The body only renders "### Subsection", which gets id="subsection".
        //   - There is NO id="first-section" in the body (Python-faithful).
        let html = generate_visual_report(
            "q",
            "## First Section\n\nbody\n\n### Subsection\n\nmore",
            None,
            None,
            None,
            None,
            None,
        );
        // The chosen title appears in the hero h1.
        assert!(html.contains("<h1>First Section</h1>"));
        // The subsection heading is present in the body with its slug as id.
        assert!(html.contains("id=\"subsection\""));
        // "first-section" is NOT a body id — the heading was stripped to become
        // the hero title, exactly as Python does.
        assert!(!html.contains("id=\"first-section\""));
    }
}
