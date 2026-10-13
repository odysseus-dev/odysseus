# src/visual_report.py
"""
Generate a self-contained, styled HTML page from deep research results.

Takes the markdown report, sources, and stats produced by DeepResearcher
and wraps them in an editorial-quality HTML document with:
- System/local typography, no remote font provider
- Dark/light theme via prefers-color-scheme
- Hero section with animated gradient + optional hero image
- Inline OG images between sections
- Auto-generated table of contents from headings
- Collapsible compact sources list
- Print/Share toolbar
"""
import html
import json
import logging
import math
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from src.research_utils import strip_thinking
from urllib.parse import urlparse

import markdown
import nh3

logger = logging.getLogger(__name__)

# Tags/attributes permitted in rendered research-report HTML. Starts from nh3's
# safe defaults (which drop <script>, inline event handlers, and javascript:
# URLs) and adds back only the formatting the report itself emits: the
# collapsible raw-findings block (<details>/<summary>), heading anchors for the
# table of contents (id), codehilite classes, table alignment, and the
# target/rel that _md_to_html puts on external links.
_REPORT_ALLOWED_TAGS = set(nh3.ALLOWED_TAGS) | {"details", "summary"}
_REPORT_ALLOWED_ATTRS = {k: set(v) for k, v in nh3.ALLOWED_ATTRIBUTES.items()}
_REPORT_ALLOWED_ATTRS.setdefault("details", set()).add("markdown")
for _h in ("h1", "h2", "h3", "h4", "h5", "h6"):
    _REPORT_ALLOWED_ATTRS.setdefault(_h, set()).add("id")
for _t in ("span", "code", "pre", "div", "table", "td", "th"):
    _REPORT_ALLOWED_ATTRS.setdefault(_t, set()).add("class")
for _t in ("td", "th"):
    _REPORT_ALLOWED_ATTRS.setdefault(_t, set()).add("align")
_REPORT_ALLOWED_ATTRS.setdefault("a", set()).update({"href", "title", "target", "rel"})
_REPORT_ALLOWED_ATTRS.setdefault("img", set()).update({"src", "alt", "title"})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _autolink_urls(md_text: str) -> str:
    """Convert bare URLs to markdown links before processing.

    Skips URLs already inside markdown link syntax [text](url).
    """
    if not isinstance(md_text, str):
        return md_text
    # Match bare URLs not already inside ](...)
    return re.sub(
        r'(?<!\]\()(?<!\()(https?://[^\s\)<>]+)',
        r'[\1](\1)',
        md_text,
    )


def _md_to_html(md_text: str) -> str:
    """Convert markdown to HTML with common extensions.

    Research-report markdown is assembled from LLM output over crawled web
    pages (untrusted content), and report pages are served under a relaxed
    `script-src 'unsafe-inline'` CSP. python-markdown passes raw HTML through
    verbatim, so the rendered output is allowlist-sanitized to strip any
    <script>/inline-event-handler/javascript: markup before it reaches the page.
    """
    md_text = _autolink_urls(md_text)
    result = markdown.markdown(
        md_text,
        extensions=["extra", "codehilite", "toc", "tables", "sane_lists", "md_in_html"],
        extension_configs={
            "codehilite": {"css_class": "code", "guess_lang": False},
            "toc": {"marker": "", "toc_depth": "2-3"},
        },
    )
    # Make external links open in new tab
    result = re.sub(
        r'<a href="(https?://)',
        r'<a target="_blank" rel="noopener noreferrer" href="\1',
        result,
    )
    # Sanitize: report content is untrusted and the report CSP allows inline
    # scripts, so strip active content while keeping the formatting above.
    result = nh3.clean(
        result,
        tags=_REPORT_ALLOWED_TAGS,
        attributes=_REPORT_ALLOWED_ATTRS,
        link_rel=None,
    )
    return result


def _extract_headings(md_text: str) -> List[Dict[str, str]]:
    """Pull h2/h3 headings from markdown for table of contents."""
    if not isinstance(md_text, str):
        return []
    headings = []
    seen_slugs: Dict[str, int] = {}

    # Strip fenced code blocks before scanning for "## ..." lines: a heading-
    # looking comment inside ``` / ~~~ is NOT rendered as an <h2> by the
    # markdown renderer, so counting it here desynced the TOC anchor ids
    # (built by zipping these headings against the rendered <h2>/<h3>), making
    # every later TOC link point at the wrong section.
    md_text = re.sub(r'(?ms)^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$', '', md_text)

    def _plain_heading_text(text: str) -> str:
        text = text.strip().rstrip("#").strip()
        text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', text)
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        text = re.sub(r'\[([^\]]+)\]\[[^\]]+\]', r'\1', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'[`*_~]+', '', text)
        text = html.unescape(text)
        return re.sub(r'\s+', ' ', text).strip()

    def _make_slug(text: str) -> str:
        base = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
        if not base:
            base = "section"
        if base in seen_slugs:
            # Increment until the disambiguated candidate is itself unused, so a
            # generated "intro-1" can't collide with a natural "intro-1" slug.
            n = seen_slugs[base]
            while True:
                n += 1
                cand = f"{base}-{n}"
                if cand not in seen_slugs:
                    break
            seen_slugs[base] = n
            seen_slugs[cand] = 0
            return cand
        seen_slugs[base] = 0
        return base

    for m in re.finditer(r'^(#{2,3})\s+(.+)$', md_text, re.MULTILINE):
        level = len(m.group(1))
        text = _plain_heading_text(m.group(2))
        if not text:
            continue
        headings.append({"level": level, "text": text, "slug": _make_slug(text)})
    if not headings:
        for m in re.finditer(r'^\*\*([^*]+)\*\*\s*$', md_text, re.MULTILINE):
            text = _plain_heading_text(m.group(1)).rstrip(':')
            if 3 < len(text) < 80:
                headings.append({"level": 2, "text": text, "slug": _make_slug(text)})
    return headings


def _apply_heading_ids(report_html: str, headings: List[Dict[str, str]]) -> str:
    """Force rendered h2/h3 IDs to match the generated sidebar links."""
    if not headings:
        return report_html

    soup = BeautifulSoup(report_html, "html.parser")
    rendered_headings = soup.find_all(["h2", "h3"])
    for element, heading in zip(rendered_headings, headings):
        expected_name = f"h{heading['level']}"
        if element.name != expected_name:
            logger.debug(
                "Visual report heading level mismatch: rendered %s for TOC %s",
                element.name,
                expected_name,
            )
        element["id"] = heading["slug"]
    if len(rendered_headings) != len(headings):
        logger.debug(
            "Visual report heading count mismatch: rendered=%s toc=%s",
            len(rendered_headings),
            len(headings),
        )
    return str(soup)


# Overlay buttons shown on each image: reroll (swap for the next unused
# scraped image) + hide (remove and skip on future renders). Reroll is
# wired up in the page script using the embedded spare-image pool.
_IMG_OVERLAY_BTNS = (
    '<button class="img-reroll-btn" type="button" title="Swap for another image">'
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>'
    '</button>'
    '<button class="img-hide-btn" type="button" title="Hide image">'
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
    '</button>'
)


def _wrap_report_sections(report_html: str) -> str:
    """Group each top-level h2 and its content into an editorial section."""
    soup = BeautifulSoup(report_html, "html.parser")
    for details in soup.find_all("details"):
        summary = details.find("summary", recursive=False)
        if summary and summary.get_text(" ", strip=True).lower() == "research trace":
            details["class"] = list(details.get("class") or []) + ["research-trace"]
    current_section = None
    for child in list(soup.contents):
        if getattr(child, "name", None) == "h2":
            current_section = soup.new_tag("section")
            current_section["class"] = ["report-section"]
            child.insert_before(current_section)
        if current_section is not None:
            current_section.append(child.extract())
    return str(soup)


def _extract_research_trace(report_html: str) -> Tuple[str, str]:
    """Remove the trace from the article body so it can sit beside Sources."""
    soup = BeautifulSoup(report_html, "html.parser")
    trace = soup.select_one("details.research-trace")
    if trace is None:
        return report_html, ""

    previous = trace.find_previous_sibling()
    if previous is not None and getattr(previous, "name", None) == "hr":
        previous.decompose()
    trace_html = str(trace.extract())

    for section in soup.select("section.report-section"):
        meaningful = section.get_text(" ", strip=True) or section.find(
            ["img", "table", "pre", "blockquote"]
        )
        if not meaningful:
            section.decompose()
    return str(soup), trace_html


def _inject_images(
    report_html: str,
    images: List[str],
    image_labels: Optional[Dict[str, Tuple[str, str]]] = None,
) -> Tuple[str, int]:
    """Insert OG images between h2 sections as figures.

    Returns (html, consumed) where ``consumed`` is how many of ``images``
    were actually placed — the rest become the spare pool for reroll.
    """
    if not images:
        return report_html, 0

    # Find positions after closing </h2> + following paragraph
    h2_positions = [m.end() for m in re.finditer(r'</h2>', report_html)]
    if not h2_positions:
        return report_html, 0

    # Insert an image after every 2nd heading (skip first heading = title)
    img_idx = 0
    insert_after = h2_positions[1::2]  # every 2nd h2
    # Work backwards to preserve positions
    for pos in reversed(insert_after):
        if img_idx >= len(images):
            break
        img_url = images[img_idx]
        img_idx += 1
        url_esc = html.escape(img_url)
        title, domain = (image_labels or {}).get(img_url, ("", ""))
        caption = ""
        if title or domain:
            caption = (
                '<figcaption>'
                f'<span>{html.escape(title or "Source image")}</span>'
                f'<span>{html.escape(domain)}</span>'
                '</figcaption>'
            )
        figure = (
            f'\n<figure class="section-image" data-img-url="{url_esc}">'
            f'<img src="{url_esc}" alt="{html.escape(title)}" loading="lazy" '
            f'onerror="this.parentElement.style.display=\'none\'">'
            f'{caption}'
            f'{_IMG_OVERLAY_BTNS}'
            f'</figure>\n'
        )
        report_html = report_html[:pos] + figure + report_html[pos:]

    return report_html, img_idx


def _source_evidence_summary(sources: List[Dict]) -> Dict[str, object]:
    """Summarize source diversity and quality for the visible report chrome."""
    domains = set()
    primary_count = 0
    browser_count = 0
    scores = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        try:
            hostname = (urlparse(str(source.get("url") or "")).hostname or "").lower()
            if hostname.startswith("www."):
                hostname = hostname[4:]
            if hostname:
                domains.add(hostname)
        except Exception:
            pass
        if str(source.get("source_kind") or "").lower() == "primary":
            primary_count += 1
        if str(source.get("retrieval") or "").lower() == "browser":
            browser_count += 1
        try:
            score = int(source.get("source_score"))
            if 0 <= score <= 100:
                scores.append(score)
        except (TypeError, ValueError):
            pass
    return {
        "sources": len(sources),
        "domains": len(domains),
        "primary": primary_count,
        "browser": browser_count,
        "average_score": round(sum(scores) / len(scores)) if scores else None,
        "rated": len(scores),
    }


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="article">
{og_image_meta}
<meta name="theme-color" content="#b8543a" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#131214" media="(prefers-color-scheme: dark)">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='75' font-size='75'>O</text></svg>">
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

:root {{
  --font-display: 'Charter', 'Iowan Old Style', Georgia, serif;
  --font-body: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  --bg: #fbf9f4;
  --bg-surface: #ffffff;
  --bg-surface-alt: #f1ede4;
  --border: rgba(0,0,0,0.08);
  --border-strong: rgba(0,0,0,0.16);
  --text: #1a1817;
  --text-dim: #5a5651;
  --text-muted: #8a8580;
  --accent: #b8543a;
  --accent-light: #d97a5e;
  --accent-bg: rgba(184,84,58,0.06);
  --gold: #c9952e;
  --gold-bg: rgba(201,149,46,0.09);
  --aurora-a: rgba(184,84,58,0.10);
  --aurora-b: rgba(201,149,46,0.08);
  --aurora-c: rgba(64,98,128,0.07);
  --radius: 12px;
  --shadow-sm: 0 1px 3px rgba(0,0,0,0.05);
  --shadow-md: 0 4px 24px rgba(0,0,0,0.07);
  --max-w: 760px;
}}

@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #131214; --bg-surface: #1c1a1e; --bg-surface-alt: #25232a;
    --border: rgba(255,255,255,0.07); --border-strong: rgba(255,255,255,0.16);
    --text: #ece8e2; --text-dim: #a8a39c; --text-muted: #6f6b66;
    --accent: #e88f73; --accent-light: #f4ad95; --accent-bg: rgba(232,143,115,0.09);
    --gold: #e8c05a; --gold-bg: rgba(232,192,90,0.09);
    --aurora-a: rgba(232,143,115,0.13);
    --aurora-b: rgba(232,192,90,0.09);
    --aurora-c: rgba(125,180,224,0.10);
    --shadow-sm: 0 1px 3px rgba(0,0,0,0.4); --shadow-md: 0 4px 28px rgba(0,0,0,0.55);
  }}
}}

html {{
  scroll-behavior: smooth;
  /* Give smooth-scroll some breathing room so anchors land below the
     fixed toolbar instead of being shoved straight under it. */
  scroll-padding-top: 4rem;
}}
body {{
  font-family: var(--font-body);
  background: var(--bg);
  color: var(--text);
  line-height: 1.75;
  font-size: 17px;
  font-feature-settings: 'ss01', 'cv11';
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
  position: relative;
  min-height: 100vh;
}}
.reading-progress {{
  position: fixed;
  inset: 0 0 auto;
  z-index: 200;
  height: 3px;
  background: transparent;
  pointer-events: none;
}}
.reading-progress-bar {{
  width: 0;
  height: 100%;
  background: var(--accent);
  box-shadow: 0 0 10px color-mix(in srgb, var(--accent) 45%, transparent);
  transition: width 0.08s linear;
}}

/* ── Aurora background ─────────────────────────────────
   Slowly-drifting layered blobs in the accent palette. Sits behind
   the content, fixed to the viewport so scrolling doesn't reset the
   composition. Subtle grain on top stops it reading as 'flat CSS'. */
body::before {{
  content: '';
  position: fixed;
  inset: -20vh -20vw;
  z-index: -2;
  background:
    radial-gradient(40vw 50vh at 18% 22%, var(--aurora-a) 0%, transparent 60%),
    radial-gradient(45vw 55vh at 82% 12%, var(--aurora-b) 0%, transparent 65%),
    radial-gradient(55vw 60vh at 50% 88%, var(--aurora-c) 0%, transparent 70%);
  filter: blur(20px);
  animation: aurora-drift 28s ease-in-out infinite alternate;
  pointer-events: none;
}}
body::after {{
  content: '';
  position: fixed;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  /* Subtle film-grain — SVG turbulence baked to a data-URL. */
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 0.32 0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>");
  opacity: 0.045;
  mix-blend-mode: overlay;
}}
@keyframes aurora-drift {{
  0%   {{ transform: translate3d(0,0,0) scale(1);     }}
  50%  {{ transform: translate3d(2vw,-1vh,0) scale(1.04); }}
  100% {{ transform: translate3d(-1vw,1.5vh,0) scale(1.02); }}
}}
@media (prefers-reduced-motion: reduce) {{
  body::before {{ animation: none; }}
}}

/* ── Toolbar (top-right, anchored to the report) ─── */
.toolbar {{
  position: absolute;
  top: 1rem;
  right: 0;
  z-index: 100;
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
  opacity: 0.82;
  transition: opacity 0.2s ease;
}}
.toolbar:hover {{ opacity: 1; }}
.toolbar button {{
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 14px;
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  background: var(--bg-surface);
  color: var(--text);
  font-family: inherit;
  font-size: 0.78rem;
  font-weight: 500;
  cursor: pointer;
  box-shadow: var(--shadow-sm);
  transition: background 0.15s;
  position: relative;
}}
.toolbar button:hover {{ background: var(--bg-surface-alt); }}
.toolbar button svg {{ width: 14px; height: 14px; flex-shrink: 0; }}
.toolbar .toast {{
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  background: var(--text);
  color: var(--bg);
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 0.72rem;
  white-space: nowrap;
  opacity: 0;
  transition: opacity 0.15s;
  pointer-events: none;
}}
.toolbar .toast.show {{ opacity: 1; }}
.dropdown {{ position: relative; }}
.export-bookmark {{
  width: 38px;
  transition: width 0.2s ease;
}}
.export-bookmark:hover,
.export-bookmark:focus-within,
.export-bookmark.is-open {{ width: 108px; }}
.toolbar .export-bookmark > #btn-export {{
  width: 100%;
  min-width: 38px;
  height: 34px;
  padding: 6px 10px;
  overflow: hidden;
  justify-content: flex-start;
  white-space: nowrap;
  border-radius: 7px;
  box-shadow: var(--shadow-sm);
}}
.export-bookmark-label,
.export-bookmark-caret {{
  opacity: 0;
  transform: translateX(4px);
  transition: opacity 0.14s ease, transform 0.2s ease;
}}
.export-bookmark:hover .export-bookmark-label,
.export-bookmark:hover .export-bookmark-caret,
.export-bookmark:focus-within .export-bookmark-label,
.export-bookmark:focus-within .export-bookmark-caret,
.export-bookmark.is-open .export-bookmark-label,
.export-bookmark.is-open .export-bookmark-caret {{
  opacity: 1;
  transform: translateX(0);
}}
.export-bookmark-caret {{
  margin-left: auto;
  color: var(--text-muted);
  font-size: 0.64rem;
}}
.export-bookmark .dropdown-menu {{ right: 8px; }}
.dropdown-menu {{
  display: none;
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  background: var(--bg-surface);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  box-shadow: var(--shadow-md);
  overflow: hidden;
  min-width: 140px;
}}
.dropdown-menu.open {{ display: block; }}
.dropdown-menu button {{
  display: block;
  width: 100%;
  padding: 8px 14px;
  border: none;
  background: none;
  color: var(--text);
  font-family: inherit;
  font-size: 0.8rem;
  text-align: left;
  cursor: pointer;
}}
.dropdown-menu button:hover {{ background: var(--bg-surface-alt); }}
.dropdown-menu button.menu-secondary {{
  border-top: 1px solid var(--border-strong);
  color: var(--text-muted);
}}

/* ── Hero ──────────────────────────────────────────── */
.hero {{
  position: relative;
  background: transparent;
  color: var(--text);
  padding: 5.5rem 2rem 2.5rem;
  text-align: center;
  overflow: hidden;
}}
.hero::before {{
  content: '';
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse 70% 60% at 50% 40%, color-mix(in srgb, var(--accent) 10%, transparent) 0%, transparent 70%);
  pointer-events: none;
}}
/* A hair-thin gradient hairline divider under the hero to anchor it
   without putting it on a heavy boxed background. */
.hero::after {{
  content: '';
  position: absolute;
  left: 50%; bottom: 0;
  width: min(60%, 320px);
  height: 1px;
  transform: translateX(-50%);
  background: linear-gradient(90deg, transparent, var(--border-strong), transparent);
}}
.hero-label {{
  position: relative;
  text-transform: uppercase;
  letter-spacing: 0.28em;
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--accent);
  opacity: 0.85;
  margin-bottom: 1.4rem;
  font-family: var(--font-body);
}}
.hero h1 {{
  position: relative;
  font-family: var(--font-display);
  font-size: clamp(2rem, 4.5vw, 3rem);
  font-weight: 600;
  font-variation-settings: 'opsz' 120, 'SOFT' 50;
  line-height: 1.15;
  max-width: 720px;
  margin: 0 auto;
  letter-spacing: -0.02em;
  color: var(--text);
}}

/* ── Hero image ───────────────────────────────────── */
.hero-image {{
  max-width: 920px;
  margin: -2rem auto 0;
  position: relative;
  z-index: 1;
  padding: 0 2rem;
}}
.hero-image img {{
  width: 100%;
  aspect-ratio: 2 / 1;
  object-fit: cover;
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  box-shadow: var(--shadow-md);
  display: block;
}}
.hero-image figcaption {{
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.55rem 0.25rem 0;
  color: var(--text-muted);
  font-size: 0.68rem;
  line-height: 1.35;
}}
.hero-image figcaption span:last-child {{ white-space: nowrap; }}

/* ── Section images ───────────────────────────────── */
.section-image {{
  width: calc(100% + 4rem);
  margin: 2rem 0 2rem -2rem;
  position: relative;
}}
.section-image img {{
  width: 100%;
  aspect-ratio: 16 / 8;
  object-fit: cover;
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: var(--shadow-sm);
  display: block;
}}
.section-image figcaption {{
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.55rem 0.25rem 0;
  color: var(--text-muted);
  font-family: var(--font-body);
  font-size: 0.68rem;
  line-height: 1.35;
}}
.section-image figcaption span:first-child {{ overflow-wrap: anywhere; }}
.section-image figcaption span:last-child {{ white-space: nowrap; }}

/* ── Per-image hide button ────────────────────────────
   A small X that appears on hover (or always on touch) so users can
   remove irrelevant OG images. Click POSTs the URL to the backend so
   the next render skips it. */
.img-hide-btn {{
  position: absolute;
  top: 10px; right: 10px;
  width: 28px; height: 28px;
  display: inline-flex; align-items: center; justify-content: center;
  background: rgba(0,0,0,0.55);
  color: #fff;
  border: none;
  border-radius: 50%;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s ease, background 0.15s ease, transform 0.05s ease;
  z-index: 2;
  padding: 0;
}}
.hero-image .img-hide-btn {{ top: 14px; right: 2.5rem; }}
/* Reroll sits just to the left of the hide button. */
.img-reroll-btn {{
  position: absolute;
  top: 10px; right: 46px;
  width: 28px; height: 28px;
  display: inline-flex; align-items: center; justify-content: center;
  background: rgba(0,0,0,0.55);
  color: #fff;
  border: none;
  border-radius: 50%;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s ease, background 0.15s ease, transform 0.05s ease;
  z-index: 2;
  padding: 0;
}}
.hero-image .img-reroll-btn {{ top: 14px; right: calc(2.5rem + 36px); }}
.section-image:hover .img-hide-btn,
.section-image:hover .img-reroll-btn,
.hero-image:hover .img-hide-btn,
.hero-image:hover .img-reroll-btn {{ opacity: 1; }}
.img-hide-btn:hover {{ background: var(--accent); }}
.img-reroll-btn:hover {{ background: var(--accent); }}
.img-hide-btn:active,
.img-reroll-btn:active {{ transform: scale(0.92); }}
.img-reroll-btn.spinning svg {{ animation: img-reroll-spin 0.6s linear infinite; }}
.img-reroll-btn:disabled {{ display: none; }}
@keyframes img-reroll-spin {{ to {{ transform: rotate(360deg); }} }}
@media (hover: none) {{
  /* Touch devices have no hover — show the buttons at low opacity always. */
  .img-hide-btn, .img-reroll-btn {{ opacity: 0.7; }}
}}
.section-image.fading,
.hero-image.fading {{
  opacity: 0;
  transform: scale(0.96);
  transition: opacity 0.25s ease, transform 0.25s ease;
}}

/* ── Stats bar ─────────────────────────────────────── */
.stats-bar {{
  display: flex;
  justify-content: center;
  gap: 1.5rem;
  flex-wrap: wrap;
  padding: 0.9rem 2rem;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border);
  font-size: 0.82rem;
  color: var(--text-dim);
}}
.stat {{ display: flex; align-items: center; gap: 0.35rem; }}
.stat-value {{ font-weight: 600; color: var(--text); }}

/* ── Evidence profile ──────────────────────────────── */
.evidence-profile {{
  max-width: calc(var(--max-w) + 260px);
  margin: 0 auto;
  padding: 1.15rem 2rem;
  display: grid;
  grid-template-columns: minmax(150px, 1.25fr) repeat(4, minmax(90px, 1fr));
  border-bottom: 1px solid var(--border);
}}
.evidence-intro {{ padding-right: 1.5rem; }}
.evidence-read-time {{
  display: flex;
  align-items: center;
  gap: 0.55rem;
  color: var(--text);
  font-size: 0.82rem;
}}
.evidence-read-time svg {{
  width: 16px;
  height: 16px;
  color: var(--accent);
  flex: 0 0 auto;
}}
.evidence-read-time strong {{ font-size: 1rem; font-weight: 650; }}
.evidence-kicker {{
  display: block;
  color: var(--accent);
  font-size: 0.67rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.12em;
}}
.evidence-caption {{
  display: block;
  margin-top: 0.18rem;
  color: var(--text-muted);
  font-size: 0.72rem;
  line-height: 1.35;
}}
.evidence-metric {{
  padding: 0 1rem;
  border-left: 1px solid var(--border);
}}
.evidence-value {{
  display: block;
  color: var(--text);
  font-size: 1rem;
  font-weight: 650;
  line-height: 1.25;
}}
.evidence-label {{
  display: block;
  margin-top: 0.16rem;
  color: var(--text-muted);
  font-size: 0.68rem;
  line-height: 1.25;
}}
.mobile-section-nav {{ display: none; }}

/* ── Layout ────────────────────────────────────────── */
.layout {{
  display: grid;
  grid-template-columns: 200px 1fr;
  max-width: calc(var(--max-w) + 260px);
  margin: 0 auto;
}}
@media (max-width: 900px) {{
  .layout {{ grid-template-columns: 1fr; }}
  .toc-sidebar {{ display: none; }}
}}

/* ── TOC sidebar ───────────────────────────────────── */
.toc-sidebar {{
  position: sticky; top: 0; height: 100vh; overflow-y: auto;
  padding: 3.2rem 0.8rem 2rem 1.4rem;
  border-right: 1px solid var(--border);
  font-size: 0.78rem;
}}
.toc-sidebar nav {{ position: relative; }}
.toc-title {{
  margin: 0 0 0.75rem 0.85rem;
  color: var(--text-muted);
  font-size: 0.64rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.12em;
}}
.toc-sidebar nav a {{
  position: relative;
  display: block;
  color: var(--text-dim);
  text-decoration: none;
  padding: 0.42rem 0.7rem 0.42rem 0.85rem;
  margin: 1px 0;
  border-radius: 6px;
  line-height: 1.4;
  letter-spacing: -0.005em;
  transition: color 0.18s ease, background 0.18s ease, padding-left 0.18s ease;
}}
/* Sliding accent indicator on the left edge of each TOC link */
.toc-sidebar nav a::before {{
  content: '';
  position: absolute;
  left: 0; top: 50%;
  width: 2px; height: 0;
  background: var(--accent);
  transform: translateY(-50%);
  border-radius: 1px;
  transition: height 0.18s ease, opacity 0.18s ease;
  opacity: 0;
}}
.toc-sidebar nav a:hover {{
  color: var(--text);
  background: var(--accent-bg);
  padding-left: 1rem;
}}
.toc-sidebar nav a:hover::before {{
  height: 60%;
  opacity: 1;
}}
.toc-sidebar nav a.active {{
  color: var(--accent);
  font-weight: 600;
  background: var(--accent-bg);
}}
.toc-sidebar nav a.active::before {{
  height: 80%;
  opacity: 1;
}}
.toc-sidebar nav a.depth-3 {{
  padding-left: 1.3rem;
  font-size: 0.72rem;
  color: var(--text-muted);
}}
.toc-sidebar nav a.depth-3:hover {{ padding-left: 1.45rem; }}

/* ── Content ───────────────────────────────────────── */
.content {{
  max-width: var(--max-w);
  min-width: 0;
  width: 100%;
  padding: 3rem 2.5rem 4rem;
  counter-reset: report-section;
}}
.report-section {{
  position: relative;
  margin-bottom: 4.25rem;
  counter-increment: report-section;
}}
.report-section:last-of-type {{ margin-bottom: 1rem; }}
body[class^="standard-report-"] .report-section {{
  padding-left: 1.2rem;
  border-left: 1px solid var(--border);
}}
body[class^="standard-report-"] .report-section > h2::before {{
  content: counter(report-section, decimal-leading-zero);
  display: block;
  margin-bottom: 0.5rem;
  color: var(--accent);
  font-family: var(--font-body);
  font-size: 0.65rem;
  font-weight: 750;
  line-height: 1;
  letter-spacing: 0.12em;
}}
body[class^="standard-report-"] .report-section:nth-of-type(even) {{
  border-left-color: color-mix(in srgb, var(--accent) 45%, var(--border));
}}

/* Display headings — Fraunces optical-size driven so they get more
   contrast and personality at the larger end. */
.content h2 {{
  font-family: var(--font-display);
  font-size: clamp(1.55rem, 2.4vw, 1.85rem);
  font-weight: 600;
  font-variation-settings: 'opsz' 96, 'SOFT' 50;
  margin: 3rem 0 1rem;
  padding-bottom: 0.55rem;
  border-bottom: 1px solid transparent;
  border-image: linear-gradient(90deg, var(--accent) 0%, transparent 65%) 1;
  letter-spacing: -0.022em;
  line-height: 1.2;
  color: var(--text);
}}
.content > h2:first-child,
.report-section:first-of-type > h2:first-child {{ margin-top: 0; }}
.content h3 {{
  font-family: var(--font-display);
  font-size: 1.22rem;
  font-weight: 600;
  font-variation-settings: 'opsz' 32;
  margin: 2.2rem 0 0.6rem;
  letter-spacing: -0.015em;
  color: var(--text);
}}
.content h4 {{
  font-family: var(--font-body);
  font-size: 0.78rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--text-dim);
  margin: 1.6rem 0 0.5rem;
}}
.content p {{ margin-bottom: 1.1rem; hanging-punctuation: first last; }}

/* Drop cap on the very first paragraph of the body — old-school editorial
   touch that anchors the reader. */
.content > p:first-of-type::first-letter,
.content > h2:first-child + p::first-letter,
.report-section:first-of-type > h2:first-child + p::first-letter {{
  font-family: var(--font-display);
  font-weight: 700;
  font-variation-settings: 'opsz' 144;
  font-size: 3.6em;
  line-height: 0.85;
  float: left;
  margin: 0.15em 0.12em 0 -0.04em;
  color: var(--accent);
}}

.content a {{
  color: var(--accent);
  text-decoration: underline;
  text-decoration-color: color-mix(in srgb, var(--accent) 35%, transparent);
  text-decoration-thickness: 1.5px;
  text-underline-offset: 3px;
  transition: text-decoration-color 0.15s, color 0.15s;
}}
.content a:hover {{
  text-decoration-color: var(--accent);
  color: var(--accent-light);
}}
.content ul, .content ol {{ margin: 0 0 1.1rem 1.6rem; }}
.content li {{ margin-bottom: 0.4rem; }}
.content li::marker {{ color: var(--accent); }}
.content li > ul, .content li > ol {{ margin-top: 0.4rem; margin-bottom: 0; }}
.content blockquote {{
  position: relative;
  border-left: 3px solid var(--gold);
  background: var(--gold-bg);
  padding: 1.1rem 1.4rem 1.1rem 2.6rem;
  margin: 1.5rem 0;
  border-radius: 0 var(--radius) var(--radius) 0;
  color: var(--text);
  font-family: var(--font-display);
  font-style: italic;
  font-size: 1.05rem;
  line-height: 1.55;
}}
.content blockquote::before {{
  content: '\\201C';
  position: absolute;
  left: 0.5rem; top: 0.3rem;
  font-family: var(--font-display);
  font-size: 3rem;
  font-style: normal;
  color: var(--gold);
  opacity: 0.5;
  line-height: 1;
}}
.content hr {{ border: none; height: 1px; background: linear-gradient(90deg, transparent, var(--border-strong), transparent); margin: 2rem 0; }}
.content code {{ font-family: var(--font-mono); font-size: 0.86em; background: var(--bg-surface-alt); padding: 0.15em 0.4em; border-radius: 4px; }}
.content pre {{ background: var(--bg-surface-alt); border: 1px solid var(--border); border-radius: var(--radius); padding: 1.25rem 1.5rem; overflow-x: auto; margin: 1.25rem 0; font-size: 0.86rem; line-height: 1.6; }}
.content pre code {{ background: none; padding: 0; }}
.content table {{ width: 100%; border-collapse: collapse; margin: 1.25rem 0; font-size: 0.9rem; border-radius: var(--radius); overflow: hidden; box-shadow: var(--shadow-sm); }}
.content th {{ text-align: left; padding: 0.7rem 1rem; background: var(--accent-bg); font-weight: 600; border-bottom: 2px solid var(--border-strong); }}
.content td {{ padding: 0.6rem 1rem; border-bottom: 1px solid var(--border); vertical-align: top; }}
.content tr:last-child td {{ border-bottom: none; }}
.content tr:hover td {{ background: var(--accent-bg); }}

/* Generated visual explanations */
.visual-html-story {{
  width: calc(100% + 3rem);
  margin: 1.5rem 0 2.5rem -1.5rem;
  border-block: 1px solid var(--border-strong);
  background: var(--bg);
}}
.visual-html-story iframe {{
  display: block;
  width: 100%;
  min-height: 680px;
  border: 0;
  background: var(--bg);
  color-scheme: dark light;
}}
.visual-diagram {{
  width: calc(100% + 2rem);
  margin: 2rem 0 2.25rem -1rem;
  padding: 1.1rem 1rem 1rem;
  border-block: 1px solid var(--border-strong);
  background: color-mix(in srgb, var(--bg-surface) 82%, transparent);
}}
.visual-diagram figcaption {{
  margin: 0 0 0.8rem;
  color: var(--text);
  font-family: var(--font-display);
  font-size: 1rem;
  font-weight: 650;
  line-height: 1.35;
}}
.visual-diagram-canvas {{
  width: 100%;
  max-width: 100%;
  overflow-x: auto;
  overscroll-behavior-inline: contain;
  scrollbar-color: var(--border-strong) transparent;
}}
.visual-diagram svg {{
  display: block;
  width: 100%;
  height: auto;
  min-width: 660px;
  color: var(--accent);
}}
.visual-diagram-layers svg,
.visual-diagram-timeline svg {{ min-width: 540px; }}
.visual-edge {{
  fill: none;
  stroke: color-mix(in srgb, var(--accent) 66%, var(--text-muted));
  stroke-width: 2.25;
}}
.visual-edge-label {{
  fill: var(--text-muted);
  font-family: var(--font-body);
  font-size: 12px;
  paint-order: stroke;
  stroke: var(--bg-surface);
  stroke-width: 5px;
  stroke-linejoin: round;
}}
.visual-diagram marker path {{ fill: var(--accent); }}
.visual-node rect {{
  fill: var(--bg-surface);
  stroke: color-mix(in srgb, var(--accent) 52%, var(--border-strong));
  stroke-width: 1.5;
}}
.visual-node-1 rect {{ fill: color-mix(in srgb, var(--accent) 7%, var(--bg-surface)); }}
.visual-node-2 rect {{ fill: color-mix(in srgb, var(--gold) 7%, var(--bg-surface)); }}
.visual-node-3 rect {{ fill: color-mix(in srgb, var(--text-muted) 7%, var(--bg-surface)); }}
.visual-node-index {{ fill: var(--accent); }}
.visual-node-index-text {{
  fill: #fff;
  font-family: var(--font-body);
  font-size: 11px;
  font-weight: 750;
}}
.visual-node-label {{
  fill: var(--text);
  font-family: var(--font-body);
  font-size: 14px;
  font-weight: 700;
}}
.visual-node-detail {{
  fill: var(--text-muted);
  font-family: var(--font-body);
  font-size: 11.5px;
}}
@media (max-width: 600px) {{
  .visual-html-story {{
    width: calc(100% + 1.5rem);
    margin-left: -0.75rem;
  }}
  .visual-html-story iframe {{ min-height: 760px; }}
  .visual-diagram {{
    width: calc(100% + 1.5rem);
    margin-left: -0.75rem;
    padding-inline: 0.75rem;
  }}
  .visual-diagram figcaption {{ font-size: 0.92rem; }}
}}

/* ── Sources (collapsible list) ───────────────────── */
.sources-panel {{ margin-top: 3rem; border-top: 2px solid var(--border); padding-top: 1.5rem; }}
.sources-panel details {{ margin: 0; }}
.sources-panel summary {{
  display: flex; align-items: center; gap: 0.5rem;
  cursor: pointer; font-size: 1rem; font-weight: 600;
  color: var(--text); padding: 0.5rem 0; list-style: none;
  user-select: none;
}}
.sources-panel summary::-webkit-details-marker {{ display: none; }}
.sources-panel summary::before {{
  content: '\\25B6'; font-size: 0.65em; color: var(--text-muted);
  transition: transform 0.2s;
}}
.sources-panel details[open] summary::before {{ transform: rotate(90deg); }}
.sources-list {{ padding: 0.5rem 0 0; }}
.sources-list a {{
  display: grid;
  grid-template-columns: 1.8rem minmax(0, 1fr) auto;
  align-items: start;
  gap: 0.65rem;
  padding: 0.7rem 0.25rem;
  font-size: 0.85rem;
  color: var(--text); text-decoration: none;
  border-bottom: 1px solid var(--border);
  transition: color 0.15s, background 0.15s;
}}
.sources-list a:last-child {{ border-bottom: 0; }}
.sources-list a:hover {{ color: var(--accent); background: var(--accent-bg); }}
.sources-list .snum {{
  color: var(--text-muted); font-size: 0.75rem;
  text-align: right;
}}
.source-copy {{ min-width: 0; line-height: 1.35; }}
.source-title {{ display: block; overflow-wrap: anywhere; }}
.source-meta {{
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
  margin-top: 0.28rem;
  color: var(--text-muted);
  font-size: 0.7rem;
}}
.source-badge {{
  display: inline-flex;
  align-items: center;
  min-height: 1.25rem;
  padding: 0 0.4rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg-surface-alt);
  color: var(--text-dim);
  font-size: 0.64rem;
  font-weight: 650;
  text-transform: capitalize;
}}
.source-badge.primary {{
  border-color: color-mix(in srgb, var(--accent) 30%, var(--border));
  background: var(--accent-bg);
  color: var(--accent);
}}
.source-score {{
  color: var(--text-muted);
  font-size: 0.7rem;
  white-space: nowrap;
}}

/* ── Research trace ─────────────────────────────────── */
.research-trace {{
  margin: 3rem 0 0;
  padding-top: 1.5rem;
  border-top: 2px solid var(--border);
  color: var(--text-dim);
  font-family: var(--font-body);
  font-size: 11px;
  line-height: 1.5;
}}
.research-trace > summary {{
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
  padding: 0.5rem 0;
  color: var(--text);
  font-size: 1rem;
  font-weight: 600;
  list-style: none;
  user-select: none;
}}
.research-trace > summary::-webkit-details-marker {{ display: none; }}
.research-trace > summary::before {{
  content: '\25B6';
  color: var(--text-muted);
  font-size: 0.65em;
  transition: transform 0.2s;
}}
.research-trace[open] > summary::before {{ transform: rotate(90deg); }}
.research-trace h3 {{
  margin: 1rem 0 0.35rem;
  color: var(--text-dim);
  font-family: var(--font-body);
  font-size: 11px;
  letter-spacing: 0;
}}
.research-trace ul,
.research-trace ol {{ margin: 0.35rem 0 0.7rem 1.2rem; }}
.research-trace li {{ margin-bottom: 0.22rem; }}
.research-trace pre {{
  margin: 0.45rem 0 0.8rem;
  padding: 0.7rem 0.8rem;
  border-radius: 6px;
  font-size: 10px;
  line-height: 1.45;
}}
.research-trace code {{ font-size: 10px; }}
.sources-panel > .research-trace {{
  margin: 0;
  padding-top: 0;
  border-top: 1px solid var(--border);
}}

@media (max-width: 900px) {{
  .toolbar {{ top: 0.75rem; }}
  .evidence-profile {{
    grid-template-columns: repeat(2, 1fr);
    padding: 0.9rem 1rem;
    row-gap: 0.8rem;
  }}
  .evidence-intro {{ grid-column: 1 / -1; padding-right: 0; }}
  .evidence-metric {{ padding: 0 0.75rem; }}
  .evidence-metric:nth-last-child(-n+2) {{ border-top: 1px solid var(--border); padding-top: 0.75rem; }}
  .mobile-section-nav {{
    position: sticky;
    top: 0;
    z-index: 90;
    display: flex;
    align-items: center;
    gap: 0.65rem;
    min-height: 2.75rem;
    padding: 0.45rem 0.75rem;
    background: color-mix(in srgb, var(--panel, var(--bg)) 94%, transparent);
    border: 1px solid color-mix(in srgb, var(--accent) 28%, var(--border));
    border-radius: 6px;
    margin: 0 0.75rem 0.75rem;
    box-shadow: 0 2px 12px color-mix(in srgb, var(--accent) 8%, transparent);
    backdrop-filter: blur(14px);
  }}
  .mobile-section-nav svg {{ width: 15px; height: 15px; color: var(--accent); opacity: 0.85; flex: 0 0 auto; }}
  .mobile-section-nav select {{
    width: 100%;
    min-width: 0;
    min-height: 1.9rem;
    padding: 0 1.8rem 0 0.55rem;
    border: 1px solid color-mix(in srgb, var(--border) 82%, transparent);
    border-radius: 4px;
    appearance: auto;
    background: color-mix(in srgb, var(--bg) 82%, transparent);
    color: var(--text);
    font: 600 0.78rem/1.35 var(--font-body);
    outline: none;
    cursor: pointer;
    color-scheme: dark;
  }}
  .mobile-section-nav select option {{
    background: var(--bg);
    color: var(--text);
  }}
  .mobile-section-nav select:focus {{
    border-color: var(--accent);
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 18%, transparent);
  }}
  .content {{ padding: 2.25rem 1.25rem 3rem; }}
  .section-image {{ width: 100%; margin: 1.5rem 0; }}
  .hero-image {{ padding: 0 1rem; }}
  .hero-image figcaption {{ padding-inline: 0.1rem; }}
  body[class^="standard-report-"] .report-section {{ padding-left: 0.8rem; }}
  .toolbar button {{ padding: 6px 9px; }}
}}

@media (max-width: 600px) {{
  /* The chapter jump bar sits outside .content, so let it use the complete
     mobile viewport instead of inheriting article-style side margins. */
  .mobile-section-nav {{
    width: 100%;
    box-sizing: border-box;
    margin-left: 0;
    margin-right: 0;
    border-left: 0;
    border-right: 0;
    border-radius: 0;
  }}
  .export-bookmark {{ width: 38px; }}
  .export-bookmark.is-open {{ width: 108px; }}
}}

@media (hover: none) {{
  .export-bookmark {{ width: 38px; }}
  .export-bookmark.is-open {{ width: 108px; }}
}}

/* ── Chat-about CTA ────────────────────────────────── */
.chat-cta {{
  margin: 3rem 0 1rem; padding: 1.5rem;
  text-align: center;
  border: 1px solid var(--border); border-radius: 12px;
  background: var(--bg-surface);
}}
.chat-cta-btn {{
  display: inline-flex; align-items: center; gap: 8px;
  padding: 10px 18px; font-size: 0.95rem; font-weight: 600;
  background: var(--accent); color: #fff;
  border: none; border-radius: 8px; cursor: pointer;
  font-family: inherit;
  transition: filter 0.15s, transform 0.05s;
}}
.chat-cta-btn:hover:not(:disabled) {{ filter: brightness(1.1); }}
.chat-cta-btn:active:not(:disabled) {{ transform: translateY(1px); }}
.chat-cta-btn:disabled {{ opacity: 0.6; cursor: progress; }}
.chat-cta-hint {{
  margin-top: 8px; font-size: 0.8rem; color: var(--text-muted);
}}

/* ── Footer ────────────────────────────────────────── */
.report-footer {{
  text-align: center; padding: 2rem; font-size: 0.75rem;
  color: var(--text-muted); border-top: 1px solid var(--border); margin-top: 2rem;
}}

/* ── Animations ────────────────────────────────────── */
@media (prefers-reduced-motion: no-preference) {{
  .report-section, .content > p, .content > details {{
    animation: fadeUp 0.4s ease both;
  }}
  @keyframes fadeUp {{
    from {{ opacity: 0; transform: translateY(8px); }}
    to   {{ opacity: 1; transform: translateY(0); }}
  }}
}}

/* ── Print ─────────────────────────────────────────── */
@media print {{
  .toc-sidebar, .toolbar, .mobile-section-nav, .reading-progress {{ display: none !important; }}
  .layout {{ grid-template-columns: 1fr; }}
  .hero {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
}}
{category_css}
</style>
</head>
<body class="{body_class}">

<div class="reading-progress" aria-hidden="true"><div class="reading-progress-bar"></div></div>

<!-- Toolbar: Export and report display actions -->
<div class="toolbar">
  {restore_btn_html}
  <div class="dropdown export-bookmark">
    <button id="btn-export" type="button" title="Export" aria-haspopup="menu" aria-expanded="false">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      <span class="export-bookmark-label">Export</span><span class="export-bookmark-caret" aria-hidden="true">&#9662;</span>
    </button>
    <div class="dropdown-menu" id="export-menu" role="menu">
      <button id="btn-pdf">Save as PDF</button>
      <button id="btn-html">Download HTML</button>
      <button id="btn-hide-toolbar" class="menu-secondary">Hide toolbar</button>
    </div>
  </div>
</div>

<div class="hero">
  <div class="hero-label">Odysseus &mdash; Deep Research Report</div>
  <h1>{question_html}</h1>
</div>

{hero_image_html}

<div class="stats-bar">
  {stats_html}
</div>

{evidence_profile_html}

{mobile_toc_html}

<div class="layout">
  <aside class="toc-sidebar">
    <nav>
      <div class="toc-title">In this report</div>
      {toc_html}
    </nav>
  </aside>
  <main class="content">
    {report_html}

    {sources_html}

    {chat_cta_html}
  </main>
</div>

<div class="report-footer">
  Generated by Odysseus Deep Research &middot; {timestamp}
</div>

<script>
(function() {{
  // ESC closes the report tab. window.close() works when the tab was
  // opened via window.open() (which is how the panel launches it). If the
  // browser blocks self-close (rare — e.g. report opened by direct URL),
  // fall back to history.back() so ESC still feels responsive.
  document.addEventListener('keydown', function(e) {{
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    // Don't hijack ESC while typing in a field or with an open dropdown.
    var t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    var menu = document.getElementById('export-menu');
    if (menu && menu.classList.contains('open')) {{
      menu.classList.remove('open');
      var bookmark = menu.closest('.export-bookmark');
      if (bookmark) bookmark.classList.remove('is-open');
      var button = document.getElementById('btn-export');
      if (button) button.setAttribute('aria-expanded', 'false');
      return;
    }}
    try {{ window.close(); }} catch (err) {{}}
    // window.close() is a no-op when the tab wasn't script-opened; in that
    // case fall back to navigation so the key isn't ignored.
    setTimeout(function() {{ if (!window.closed) history.back(); }}, 50);
  }});

  // Export dropdown toggle. The downloaded HTML removes this control, so
  // keep the live report script valid when it is opened without the toolbar.
  var exportBtn = document.getElementById('btn-export');
  var exportMenu = document.getElementById('export-menu');
  var exportBookmark = exportBtn && exportBtn.closest('.export-bookmark');
  function setExportMenuOpen(open) {{
    if (!exportMenu || !exportBtn) return;
    exportMenu.classList.toggle('open', open);
    exportBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (exportBookmark) exportBookmark.classList.toggle('is-open', open);
  }}
  if (exportBtn && exportMenu) {{
    exportBtn.addEventListener('click', function(e) {{
      e.stopPropagation();
      setExportMenuOpen(!exportMenu.classList.contains('open'));
    }});
    document.addEventListener('click', function() {{ setExportMenuOpen(false); }});
  }}

  // Save as PDF (browser print)
  var pdfBtn = document.getElementById('btn-pdf');
  if (pdfBtn) pdfBtn.addEventListener('click', function() {{
    setExportMenuOpen(false);
    window.print();
  }});

  // Download HTML
  var htmlBtn = document.getElementById('btn-html');
  if (htmlBtn) htmlBtn.addEventListener('click', function() {{
    setExportMenuOpen(false);
    var exportedDocument = document.documentElement.cloneNode(true);
    var exportedToolbar = exportedDocument.querySelector('.toolbar .dropdown');
    if (exportedToolbar) exportedToolbar.remove();
    var blob = new Blob(['<!DOCTYPE html>\\n' + exportedDocument.outerHTML], {{ type: 'text/html' }});
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = document.title.replace(/[^a-z0-9]+/gi, '-').substring(0, 60) + '.html';
    a.click();
  }});

  // Keep toolbar dismissal available without a permanent close control.
  var hideToolbarBtn = document.getElementById('btn-hide-toolbar');
  if (hideToolbarBtn) hideToolbarBtn.addEventListener('click', function() {{
    setExportMenuOpen(false);
    var toolbar = exportBtn && exportBtn.closest('.toolbar');
    if (toolbar) toolbar.style.display = 'none';
  }});

  // Per-image hide — fades the image out, then POSTs to the backend so
  // future renders of this report skip the URL. Falls back to a silent
  // no-op if there's no session_id (e.g. the report was opened from a
  // saved-HTML download where the backend isn't reachable).
  var __sessionId = {session_id_js};
  // Unused scraped images — the reroll pool. Each is used at most once.
  var __spareImages = {spare_images_js};

  // Match the report accent to the hero image when its host permits canvas
  // sampling. The server-generated palette remains the fallback for images
  // without cross-origin access.
  function __applyImagePalette(url) {{
    if (!url) return;
    var probe = new Image();
    probe.crossOrigin = 'anonymous';
    probe.onload = function() {{
      try {{
        var canvas = document.createElement('canvas');
        canvas.width = 32; canvas.height = 32;
        var ctx = canvas.getContext('2d', {{ willReadFrequently: true }});
        ctx.drawImage(probe, 0, 0, 32, 32);
        var pixels = ctx.getImageData(0, 0, 32, 32).data;
        var r = 0, g = 0, b = 0, weight = 0;
        for (var i = 0; i < pixels.length; i += 4) {{
          if (pixels[i + 3] < 180) continue;
          var hi = Math.max(pixels[i], pixels[i + 1], pixels[i + 2]);
          var lo = Math.min(pixels[i], pixels[i + 1], pixels[i + 2]);
          var saturation = hi ? (hi - lo) / hi : 0;
          if (saturation < 0.2 || hi < 35 || lo > 235) continue;
          var pixelWeight = 0.5 + saturation;
          r += pixels[i] * pixelWeight;
          g += pixels[i + 1] * pixelWeight;
          b += pixels[i + 2] * pixelWeight;
          weight += pixelWeight;
        }}
        if (weight < 8) return;
        r = Math.round(r / weight); g = Math.round(g / weight); b = Math.round(b / weight);
        var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
        var luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
        var target = dark ? 178 : 112;
        var scale = Math.max(0.65, Math.min(1.8, target / Math.max(1, luminance)));
        r = Math.max(0, Math.min(255, Math.round(r * scale)));
        g = Math.max(0, Math.min(255, Math.round(g * scale)));
        b = Math.max(0, Math.min(255, Math.round(b * scale)));
        var root = document.documentElement.style;
        root.setProperty('--accent', 'rgb(' + r + ',' + g + ',' + b + ')');
        root.setProperty('--accent-light', 'rgb(' +
          Math.min(255, r + 32) + ',' + Math.min(255, g + 32) + ',' + Math.min(255, b + 32) + ')');
        root.setProperty('--accent-bg', 'rgba(' + r + ',' + g + ',' + b + ',0.08)');
        root.setProperty('--aurora-a', 'rgba(' + r + ',' + g + ',' + b + ',0.12)');
      }} catch (err) {{ /* Cross-origin image: retain the generated palette. */ }}
    }};
    probe.src = url;
  }}

  var heroWrap = document.querySelector('.hero-image[data-img-url]');
  if (heroWrap) __applyImagePalette(heroWrap.dataset.imgUrl);

  // Persist a rejected URL so future renders skip it.
  function __persistHide(url) {{
    if (!__sessionId || !url) return;
    fetch('/api/research/' + encodeURIComponent(__sessionId) + '/hide-image', {{
      method: 'POST',
      credentials: 'same-origin',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ url: url }}),
    }}).catch(function(err) {{ console.warn('hide-image POST failed', err); }});
  }}

  // Once the pool is empty, there's nothing to swap to — hide all reroll btns.
  function __syncRerollAvailability() {{
    if (__spareImages.length === 0) {{
      document.querySelectorAll('.img-reroll-btn').forEach(function(b) {{ b.disabled = true; }});
    }}
  }}

  document.querySelectorAll('.img-hide-btn').forEach(function(btn) {{
    btn.addEventListener('click', function(e) {{
      e.preventDefault(); e.stopPropagation();
      var wrap = btn.closest('[data-img-url]');
      if (!wrap) return;
      var url = wrap.dataset.imgUrl;
      wrap.classList.add('fading');
      setTimeout(function() {{ wrap.remove(); }}, 280);
      __persistHide(url);
    }});
  }});

  // Reroll — swap the current image for the next unused scraped one, and
  // persist-hide the rejected URL so it won't resurface on reload.
  document.querySelectorAll('.img-reroll-btn').forEach(function(btn) {{
    btn.addEventListener('click', function(e) {{
      e.preventDefault(); e.stopPropagation();
      // Per-button busy flag — a rapid double-click would otherwise both
      // shift the spare pool, but only the second probe's image would land,
      // silently consuming the first one. Bail until finish() clears it.
      if (btn.dataset._busy === '1') return;
      if (__spareImages.length === 0) {{ btn.disabled = true; return; }}
      var wrap = btn.closest('[data-img-url]');
      if (!wrap) return;
      var img = wrap.querySelector('img');
      if (!img) return;
      btn.dataset._busy = '1';
      var oldUrl = wrap.dataset.imgUrl;
      var newUrl = __spareImages.shift();
      btn.classList.add('spinning');
      // Swap once the new image has loaded (or failed) to avoid a flash of empty.
      var probe = new Image();
      var done = false;
      var finish = function(ok) {{
        if (done) return; done = true;
        btn.classList.remove('spinning');
        delete btn.dataset._busy;
        if (ok) {{
          img.src = newUrl;
          wrap.dataset.imgUrl = newUrl;
          if (wrap.classList.contains('hero-image')) __applyImagePalette(newUrl);
          __persistHide(oldUrl);
        }} else {{
          // Bad candidate — persist-hide it so it can't resurface on reload,
          // then try the next spare if any remain. Busy flag already cleared
          // so the synthetic click below proceeds.
          __persistHide(newUrl);
          if (__spareImages.length) btn.click();
        }}
        __syncRerollAvailability();
      }};
      probe.onload = function() {{ finish(true); }};
      probe.onerror = function() {{ finish(false); }};
      probe.src = newUrl;
    }});
  }});
  __syncRerollAvailability();

  // "Show hidden (N)" button — clears the hidden_images list on the
  // server, then reloads the page so all images come back.
  var restoreBtn = document.getElementById('btn-restore-images');
  if (restoreBtn && __sessionId) {{
    restoreBtn.addEventListener('click', function() {{
      restoreBtn.disabled = true;
      restoreBtn.textContent = 'Restoring…';
      fetch('/api/research/' + encodeURIComponent(__sessionId) + '/unhide-images', {{
        method: 'POST', credentials: 'same-origin',
      }}).then(function() {{ window.location.reload(); }})
        .catch(function(err) {{
          restoreBtn.disabled = false;
          restoreBtn.textContent = 'Failed — retry?';
          console.warn('unhide-images POST failed', err);
        }});
    }});
  }}

  // TOC: explicit smooth-scroll handler (some browsers/anchor plugins
  // bypass the CSS `scroll-behavior: smooth` rule on hash clicks).
  // Also keeps the URL hash updated and toggles an `.active` highlight.
  var tocLinks = document.querySelectorAll('.toc-sidebar nav a[href^="#"]');
  function scrollToHeading(target) {{
    var start = window.pageYOffset || document.documentElement.scrollTop || document.body.scrollTop || 0;
    var toolbarOffset = 64;
    var maxTop = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
    var destination = Math.max(0, Math.min(maxTop, start + target.getBoundingClientRect().top - toolbarOffset));
    var distance = destination - start;
    if (Math.abs(distance) < 1) return;
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {{
      window.scrollTo(0, destination);
      return;
    }}
    var startedAt = null;
    var duration = 520;
    var step = function(timestamp) {{
      if (startedAt === null) startedAt = timestamp;
      var progress = Math.min(1, (timestamp - startedAt) / duration);
      var eased = progress < 0.5
        ? 2 * progress * progress
        : 1 - Math.pow(-2 * progress + 2, 2) / 2;
      window.scrollTo(0, start + distance * eased);
      if (progress < 1) window.requestAnimationFrame(step);
    }};
    window.requestAnimationFrame(step);
  }}
  tocLinks.forEach(function(link) {{
    link.addEventListener('click', function(e) {{
      var id = link.getAttribute('href').slice(1);
      var target = document.getElementById(id);
      if (!target) return;
      e.preventDefault();
      scrollToHeading(target);
      history.replaceState(null, '', '#' + id);
    }});
  }});

  var mobileToc = document.getElementById('mobile-section-select');
  if (mobileToc) {{
    mobileToc.addEventListener('change', function() {{
      var target = document.getElementById(mobileToc.value);
      if (!target) return;
      scrollToHeading(target);
      history.replaceState(null, '', '#' + mobileToc.value);
    }});
  }}

  // Highlight the TOC entry that matches whichever heading is currently
  // closest to the top of the viewport. IntersectionObserver keeps it
  // cheap (no scroll listener spam).
  var tocMap = {{}};
  tocLinks.forEach(function(link) {{
    tocMap[link.getAttribute('href').slice(1)] = link;
  }});
  var activeId = null;
  function setActive(id) {{
    if (id === activeId) return;
    if (activeId && tocMap[activeId]) tocMap[activeId].classList.remove('active');
    if (id && tocMap[id]) tocMap[id].classList.add('active');
    if (id && mobileToc && mobileToc.value !== id) mobileToc.value = id;
    activeId = id;
  }}
  var headings = document.querySelectorAll('.content h2[id], .content h3[id]');
  if (headings.length && 'IntersectionObserver' in window) {{
    var visible = new Set();
    var io = new IntersectionObserver(function(entries) {{
      entries.forEach(function(en) {{
        if (en.isIntersecting) visible.add(en.target.id);
        else visible.delete(en.target.id);
      }});
      // Pick the visible heading that's furthest down in document order
      // before the current scroll — i.e. the section we're reading.
      var current = null;
      for (var i = 0; i < headings.length; i++) {{
        if (visible.has(headings[i].id)) {{ current = headings[i].id; break; }}
      }}
      if (current) setActive(current);
    }}, {{ rootMargin: '-10% 0px -75% 0px', threshold: 0 }});
    headings.forEach(function(h) {{ io.observe(h); }});
  }}

  // Thin page-level progress gives long reports a stable sense of position.
  var progressBar = document.querySelector('.reading-progress-bar');
  var progressTicking = false;
  function updateReadingProgress() {{
    var root = document.documentElement;
    var available = Math.max(1, root.scrollHeight - window.innerHeight);
    var current = window.pageYOffset || root.scrollTop || 0;
    progressBar.style.width = Math.max(0, Math.min(100, current / available * 100)) + '%';
    progressTicking = false;
  }}
  window.addEventListener('scroll', function() {{
    if (!progressTicking) {{
      progressTicking = true;
      window.requestAnimationFrame(updateReadingProgress);
    }}
  }}, {{ passive: true }});
  updateReadingProgress();

  // Chat about this research — POST to spinoff and redirect to the new chat
  var chatBtn = document.getElementById('btn-chat-about');
  if (chatBtn) {{
    chatBtn.addEventListener('click', function() {{
      var researchId = chatBtn.dataset.researchId;
      if (!researchId) return;
      var origLabel = chatBtn.innerHTML;
      chatBtn.disabled = true;
      chatBtn.innerHTML = '<span>Creating chat…</span>';
      fetch('/api/research/spinoff/' + encodeURIComponent(researchId), {{
        method: 'POST', credentials: 'same-origin',
      }}).then(function(res) {{
        if (!res.ok) {{
          return res.json().then(function(d) {{
            throw new Error(d && d.detail ? d.detail : ('HTTP ' + res.status));
          }}, function() {{ throw new Error('HTTP ' + res.status); }});
        }}
        return res.json();
      }}).then(function(data) {{
        if (!data || !data.session_id) {{
          throw new Error('Server did not return a session id');
        }}
        var url = '/#' + data.session_id;
        var opened = false;
        // The report typically opens in a new tab — if we have access to the
        // original Odysseus tab, navigate it and close this report tab so the
        // user lands directly in the new chat.
        try {{
          if (window.opener && !window.opener.closed) {{
            window.opener.location.href = url;
            window.opener.location.reload();
            window.opener.focus();
            opened = true;
            window.close();
          }}
        }} catch (e) {{ /* cross-origin or detached opener — fall through */ }}
        if (!opened) {{
          // No opener (report was opened directly via URL) — open the chat in a
          // new tab so the report stays available.
          var w = window.open(url, '_blank');
          if (w) {{
            chatBtn.disabled = false;
            chatBtn.innerHTML = '<span>Chat opened in new tab</span>';
          }} else {{
            // Popup blocked — navigate this tab as a last resort.
            window.location.href = url;
            window.location.reload();
          }}
        }}
      }}).catch(function(err) {{
        chatBtn.disabled = false;
        chatBtn.innerHTML = origLabel;
        alert('Could not start follow-up chat: ' + err.message);
      }});
    }});
  }}
}})();

// Colorize comparison table cells
if (document.body.classList.contains('category-comparison')) {{
  const pos = /^(yes|excellent|best|great|strong|fast|high|superior|winner|free|unlimited|native|full|advanced|built[- ]in|✓|✅|⭐)/i;
  const neg = /^(no|none|poor|weak|slow|low|limited|lacking|missing|basic|minimal|✗|❌|N\\/A$)/i;
  const mid = /^(moderate|average|fair|partial|some|decent|okay|mixed|varies|depends)/i;
  document.querySelectorAll('.content table td').forEach(td => {{
    if (td.cellIndex === 0) return;
    const t = td.textContent.trim();
    if (pos.test(t)) td.classList.add('cmp-pos');
    else if (neg.test(t)) td.classList.add('cmp-neg');
    else if (mid.test(t)) td.classList.add('cmp-mid');
  }});
}}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _standard_visual_variant(question: str, session_id: Optional[str] = None) -> int:
    """Standard is a format, so every standard report uses the same palette."""
    return 0


def _category_css(category: Optional[str], standard_variant: Optional[int] = None) -> str:
    if not category:
        palettes = (
            ("#277f83", "#45aeb2", "rgba(39,127,131,0.07)", "rgba(39,127,131,0.11)"),
            ("#4b72a9", "#7096cf", "rgba(75,114,169,0.07)", "rgba(75,114,169,0.11)"),
            ("#4f8b61", "#70b27f", "rgba(79,139,97,0.07)", "rgba(79,139,97,0.11)"),
            ("#7653a8", "#9a78c9", "rgba(118,83,168,0.07)", "rgba(118,83,168,0.11)"),
            ("#aa5c78", "#cf7f9b", "rgba(170,92,120,0.07)", "rgba(170,92,120,0.11)"),
        )
        accent, light, accent_bg, aurora_a = palettes[standard_variant or 0]
        return f"""
/* Stable palette for uncategorized reports. */
body.standard-report-v{standard_variant or 0} {{
  --accent: {accent};
  --accent-light: {light};
  --accent-bg: {accent_bg};
  --aurora-a: {aurora_a};
  --aurora-b: rgba(201,149,46,0.06);
  --aurora-c: rgba(64,98,128,0.07);
}}
@media (prefers-color-scheme: dark) {{
  body.standard-report-v{standard_variant or 0} {{
    --accent: {light};
    --accent-light: #e1c7d2;
    --accent-bg: rgba(255,255,255,0.09);
    --aurora-a: rgba(255,255,255,0.08);
  }}
}}
"""
    # Per-category palette overrides — applied BEFORE the structural rules so
    # everything that reads --accent / --aurora-* automatically retints. The
    # default (no category) keeps the warm terracotta defined in :root.
    palettes = """
/* ── Category palettes ───────────────────────────────────
   Override the accent + aurora vars per category so each report
   type has a distinct visual identity. */
body.category-product {
  --accent: #2a8a8c;
  --accent-light: #4ab0b2;
  --accent-bg: rgba(42,138,140,0.07);
  --aurora-a: rgba(42,138,140,0.11);
  --aurora-b: rgba(201,149,46,0.06);
  --aurora-c: rgba(64,98,128,0.06);
}
body.category-comparison {
  --accent: #7a4cb8;
  --accent-light: #9d76d0;
  --accent-bg: rgba(122,76,184,0.07);
  --aurora-a: rgba(122,76,184,0.11);
  --aurora-b: rgba(184,84,58,0.05);
  --aurora-c: rgba(64,98,128,0.07);
}
body.category-howto {
  --accent: #3d8a3d;
  --accent-light: #62b162;
  --accent-bg: rgba(61,138,61,0.07);
  --aurora-a: rgba(61,138,61,0.11);
  --aurora-b: rgba(201,149,46,0.07);
  --aurora-c: rgba(42,138,140,0.05);
}
body.category-landscape {
  --accent: #b88a2e;
  --accent-light: #d4a955;
  --accent-bg: rgba(184,138,46,0.08);
  --aurora-a: rgba(184,138,46,0.13);
  --aurora-b: rgba(184,84,58,0.06);
  --aurora-c: rgba(122,76,184,0.05);
}
body.category-visual {
  --accent: #247f78;
  --accent-light: #45aaa0;
  --accent-bg: rgba(36,127,120,0.08);
  --aurora-a: rgba(36,127,120,0.12);
  --aurora-b: rgba(181,72,105,0.06);
  --aurora-c: rgba(62,102,146,0.07);
}
@media (prefers-color-scheme: dark) {
  body.category-product {
    --accent: #5cc8cb; --accent-light: #8fdde0;
    --accent-bg: rgba(92,200,203,0.10);
    --aurora-a: rgba(92,200,203,0.13);
    --aurora-b: rgba(232,192,90,0.07);
    --aurora-c: rgba(125,180,224,0.08);
  }
  body.category-comparison {
    --accent: #b896e8; --accent-light: #d0b8f0;
    --accent-bg: rgba(184,150,232,0.10);
    --aurora-a: rgba(184,150,232,0.13);
    --aurora-b: rgba(232,143,115,0.06);
    --aurora-c: rgba(125,180,224,0.08);
  }
  body.category-howto {
    --accent: #82c882; --accent-light: #a8dba8;
    --accent-bg: rgba(130,200,130,0.09);
    --aurora-a: rgba(130,200,130,0.12);
    --aurora-b: rgba(232,192,90,0.07);
    --aurora-c: rgba(92,200,203,0.07);
  }
  body.category-landscape {
    --accent: #e6c069; --accent-light: #f0d390;
    --accent-bg: rgba(230,192,105,0.10);
    --aurora-a: rgba(230,192,105,0.15);
    --aurora-b: rgba(232,143,115,0.07);
    --aurora-c: rgba(184,150,232,0.06);
  }
  body.category-visual {
    --accent: #62c9bf; --accent-light: #91ded7;
    --accent-bg: rgba(98,201,191,0.10);
    --aurora-a: rgba(98,201,191,0.13);
    --aurora-b: rgba(224,115,147,0.07);
    --aurora-c: rgba(115,165,218,0.08);
  }
}

/* ── Per-category font pairings ───────────────────────
   Body font shifts between serif (long-form categories) and sans
   (practical/data categories) so each report reads as a different
   publication, not just a re-tinted version of the same template. */

/* Long-form: literary serif for both display and body */
body:not([class*="category-"]),
body.category-landscape {
  --font-body: 'Source Serif 4', 'Iowan Old Style', Georgia, serif;
}

/* Comparison: analytical serif display + clean sans body */
body.category-comparison {
  --font-display: 'Playfair Display', Georgia, serif;
  --font-body: 'Inter', system-ui, sans-serif;
}

/* How-to: friendly geometric sans, top to bottom */
body.category-howto {
  --font-display: 'Manrope', system-ui, sans-serif;
  --font-body: 'Inter', system-ui, sans-serif;
}

/* Product: techy/engineery — IBM Plex Sans display + Inter body */
body.category-product {
  --font-display: 'IBM Plex Sans', system-ui, sans-serif;
  --font-body: 'Inter', system-ui, sans-serif;
}

/* Visual: neutral sans keeps diagram labels and prose in one system. */
body.category-visual {
  --font-display: 'Manrope', system-ui, sans-serif;
  --font-body: 'Inter', system-ui, sans-serif;
}

/* Source Serif sits visually larger than Inter at the same px — pull it
   back one notch for the categories that use it as body so line length
   and rhythm stay comparable across categories. */
body:not([class*="category-"]) body, /* no-op selector, kept for clarity */
body.category-landscape { font-size: 16.5px; }

/* Drop cap looks bad on geometric sans — kill it for those categories */
body.category-product   .content > p:first-of-type::first-letter,
body.category-howto     .content > p:first-of-type::first-letter,
body.category-comparison .content > p:first-of-type::first-letter,
body.category-product   .content > h2:first-child + p::first-letter,
body.category-howto     .content > h2:first-child + p::first-letter,
body.category-comparison .content > h2:first-child + p::first-letter {
  font-size: 1em; float: none; margin: 0; color: inherit;
  font-family: inherit; font-weight: inherit;
}
body.category-visual .content > p:first-of-type::first-letter,
body.category-visual .content > h2:first-child + p::first-letter {
  font-size: 1em; float: none; margin: 0; color: inherit;
  font-family: inherit; font-weight: inherit;
}

/* ── Per-category background effects ───────────────
   Each category overrides body::before so the page reads as a
   distinctly-textured surface. Aurora stays the default. */

/* Product → blueprint grid that slowly pans */
body.category-product::before {
  background:
    linear-gradient(to right, var(--aurora-a) 1px, transparent 1px),
    linear-gradient(to bottom, var(--aurora-a) 1px, transparent 1px),
    radial-gradient(70vw 60vh at 50% 50%, var(--aurora-a) 0%, transparent 75%);
  background-size: 56px 56px, 56px 56px, 100% 100%;
  filter: none;
  animation: cat-grid-pan 60s linear infinite;
}
@keyframes cat-grid-pan {
  to { background-position: 56px 56px, 56px 56px, 0 0; }
}

/* Comparison → dot grid + slow opacity pulse */
body.category-comparison::before {
  background:
    radial-gradient(circle, var(--aurora-a) 1.4px, transparent 1.8px),
    radial-gradient(60vw 55vh at 25% 25%, var(--aurora-b) 0%, transparent 65%),
    radial-gradient(60vw 55vh at 75% 75%, var(--aurora-c) 0%, transparent 65%);
  background-size: 26px 26px, 100% 100%, 100% 100%;
  filter: none;
  animation: cat-dot-pulse 14s ease-in-out infinite alternate;
}
@keyframes cat-dot-pulse {
  from { opacity: 0.65; }
  to   { opacity: 1; }
}

/* How-to → flat surface with a very subtle vignette. Drop the flow-lines
   pattern — it competes visually with the step number rails on the
   right-hand side of each H2. The reading should feel like an O'Reilly
   procedure: clean, scannable, no decoration in the way. */
body.category-howto::before {
  background:
    radial-gradient(70vw 70vh at 50% 0%, var(--aurora-a) 0%, transparent 60%),
    radial-gradient(50vw 50vh at 50% 100%, var(--aurora-b) 0%, transparent 65%);
  filter: blur(40px);
  animation: none;
}

/* Landscape → horizontal horizon bands that slowly shift sideways */
body.category-landscape::before {
  background:
    linear-gradient(
      180deg,
      transparent 0%,
      var(--aurora-a) 22%,
      transparent 35%,
      var(--aurora-b) 55%,
      transparent 68%,
      var(--aurora-c) 85%,
      transparent 100%
    );
  background-size: 100% 200%;
  filter: blur(40px);
  animation: cat-horizon-drift 36s ease-in-out infinite alternate;
}
@keyframes cat-horizon-drift {
  0%   { background-position: 0 0; }
  100% { background-position: 0 100%; }
}

@media (prefers-reduced-motion: reduce) {
  body.category-product::before,
  body.category-comparison::before,
  body.category-howto::before,
  body.category-landscape::before,
  body.category-visual::before {
    animation: none;
  }
}

/* ─────────────────────────────────────────────────────
   PER-CATEGORY STRUCTURAL TREATMENTS
   Each category gets distinctive structural CSS so the page
   reads as a different publication — not just retinted.
   ───────────────────────────────────────────────────── */

/* ── HOWTO: O'Reilly-style numbered procedure ─────── */
body.category-howto .content { counter-reset: howto-step; }
body.category-howto .content h2 {
  counter-increment: howto-step;
  display: flex; align-items: center; gap: 14px;
  border-bottom: none;
  padding-left: 0;
  margin-top: 3.5rem;
}
body.category-howto .content h2::before {
  content: counter(howto-step);
  display: inline-flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  width: 40px; height: 40px;
  border-radius: 12px;
  background: var(--accent);
  color: #fff;
  font-family: var(--font-display);
  font-size: 1.15rem;
  font-weight: 700;
  letter-spacing: 0;
  box-shadow: 0 4px 12px color-mix(in srgb, var(--accent) 30%, transparent);
}
/* Step body gets a colored left rail so you can scan "this is step 1's stuff" */
body.category-howto .content h2 ~ p,
body.category-howto .content h2 ~ ul,
body.category-howto .content h2 ~ ol,
body.category-howto .content h2 ~ pre,
body.category-howto .content h2 ~ blockquote {
  border-left: 2px solid color-mix(in srgb, var(--accent) 25%, transparent);
  padding-left: 1rem;
  margin-left: 4px;
}
body.category-howto .content h2:has(+ *) ~ h2 ~ * { border-left: none; padding-left: 0; margin-left: 0; }
/* Terminal-style code blocks — green $ prompt, monospaced, dark surface */
body.category-howto .content pre {
  background: #1a1a1e;
  color: #d4e4d4;
  border: 1px solid color-mix(in srgb, var(--accent) 20%, transparent);
  border-radius: 8px;
  position: relative;
  padding-left: 2.6rem;
}
body.category-howto .content pre::before {
  content: '$';
  position: absolute;
  left: 1.1rem; top: 1.15rem;
  color: var(--accent);
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 0.86rem;
  opacity: 0.85;
}
body.category-howto .content pre code { color: inherit; }

/* ── LANDSCAPE: editorial briefing with H3 player cards ─ */
body.category-landscape .content h3 {
  /* Each H3 in landscape = a "player" in the field — give it a card frame */
  margin-top: 2.5rem;
  padding: 14px 18px 4px;
  border-left: 3px solid var(--accent);
  background: color-mix(in srgb, var(--accent) 4%, transparent);
  border-radius: 0 8px 8px 0;
  font-family: var(--font-display);
  font-size: 1.18rem;
}
body.category-landscape .content h3 + p {
  margin-top: 0;
  padding: 0 18px 14px;
  background: color-mix(in srgb, var(--accent) 4%, transparent);
  border-left: 3px solid var(--accent);
  margin-left: 0;
  border-radius: 0 0 8px 0;
}
/* Pull-quote treatment for any standalone blockquote */
body.category-landscape .content blockquote {
  font-size: 1.2rem;
  line-height: 1.5;
  max-width: 90%;
  margin: 2rem auto;
  text-align: center;
  border-left: none;
  border-top: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
  border-bottom: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
  background: transparent;
  border-radius: 0;
  padding: 1.5rem 1rem;
  font-style: italic;
}
body.category-landscape .content blockquote::before {
  display: none;
}

/* ── COMPARISON: lab-report tables with winner badges ─ */
body.category-comparison .content {
  font-feature-settings: 'tnum' on, 'ss01';  /* tabular numerals for tables */
}
body.category-comparison .content table {
  font-size: 0.92rem;
  box-shadow: 0 6px 20px rgba(0,0,0,0.06);
}
body.category-comparison .content th {
  background: color-mix(in srgb, var(--accent) 18%, var(--bg-surface));
  color: var(--text);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  font-size: 0.72rem;
  font-weight: 700;
}
body.category-comparison .content td:first-child {
  font-weight: 600;
  background: color-mix(in srgb, var(--accent) 6%, transparent);
}
/* The first H3 inside a comparison report often names the recommended pick */
body.category-comparison .content h3:first-of-type::after {
  content: 'Pick';
  display: inline-block;
  margin-left: 10px;
  padding: 2px 10px;
  background: var(--accent);
  color: #fff;
  font-family: var(--font-body);
  font-size: 0.65rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  border-radius: 999px;
  vertical-align: middle;
}

/* ── PRODUCT: spec-sheet cards for each H3 ─────────── */
body.category-product .content h3 {
  /* Each product gets a spec-card frame — bordered, slight bg lift */
  margin-top: 2.4rem;
  padding: 16px 18px;
  border: 1px solid color-mix(in srgb, var(--accent) 28%, var(--border));
  background: var(--bg-surface);
  border-radius: 10px;
  display: flex; align-items: baseline; gap: 10px;
  font-family: var(--font-display);
  letter-spacing: -0.01em;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}
body.category-product .content h3::after {
  /* small "spec" tag on each product heading */
  content: 'SPEC';
  margin-left: auto;
  font-family: var(--font-body);
  font-size: 0.6rem;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  padding: 3px 8px;
  border-radius: 4px;
}
body.category-product .content h3 + p,
body.category-product .content h3 + ul,
body.category-product .content h3 + table {
  margin-top: 0.8rem;
  padding-left: 4px;
}
"""
    styles = {
        "product": """
/* Product category */
.category-product .content h3 {
  display:flex; align-items:baseline; gap:8px;
  border-bottom:1px solid var(--border); padding-bottom:6px;
}
.category-product .content table {
  width:100%; border-collapse:collapse; margin:1.2em 0; font-size:0.92em;
}
.category-product .content table th {
  background:var(--accent); color:#fff; padding:8px 12px; text-align:left;
}
.category-product .content table td { padding:8px 12px; border-bottom:1px solid var(--border); }
.category-product .content table tr:nth-child(even) td { background:var(--bg-surface); }
.category-product .content ul { columns:2; column-gap:2em; }
@media (max-width:600px) { .category-product .content ul { columns:1; } }
.category-product .content a[href*="amazon"],
.category-product .content a[href*="ebay"],
.category-product .content a[href*="shop"],
.category-product .content a[href*="buy"] {
  display:inline-block; padding:3px 10px; border-radius:4px;
  background:var(--accent); color:#fff; text-decoration:none; font-size:0.85em; margin:2px 4px;
}
.quick-links-bar {
  display:flex; flex-wrap:wrap; gap:6px; padding:12px 0; margin-bottom:12px;
  border-bottom:1px solid var(--border);
}
.quick-link {
  padding:5px 12px; border-radius:16px; font-size:0.82em; text-decoration:none;
  border:1px solid var(--border); color:var(--text); transition:all 0.15s;
  white-space:nowrap;
}
.quick-link:hover {
  background:var(--accent); color:#fff; border-color:var(--accent);
}
""",
        "comparison": """
/* Comparison category */
.category-comparison .content table {
  width:100%; border-collapse:collapse; margin:1.2em 0;
}
.category-comparison .content table th {
  background:var(--accent); color:#fff; padding:10px 14px;
  text-align:center; font-weight:600; position:sticky; top:0;
}
.category-comparison .content table td {
  padding:10px 14px; border-bottom:1px solid var(--border); text-align:center;
}
.category-comparison .content table tr:nth-child(even) td { background:var(--bg-surface); }
.category-comparison .content table td:first-child {
  text-align:left; font-weight:500; background:color-mix(in srgb, var(--accent) 8%, transparent);
}
.category-comparison .content table td.cmp-pos {
  color:#2e7d32; font-weight:600;
  background:color-mix(in srgb, #4caf50 10%, transparent);
}
.category-comparison .content table td.cmp-neg {
  color:#c62828; font-weight:600;
  background:color-mix(in srgb, #f44336 8%, transparent);
}
.category-comparison .content table td.cmp-mid {
  color:#e68a00;
  background:color-mix(in srgb, #ffa726 8%, transparent);
}
.category-comparison .content h2 ~ p strong:first-child {
  display:inline-block; padding:2px 8px; border-radius:3px;
  background:color-mix(in srgb, var(--accent) 15%, transparent); font-size:0.9em;
}
""",
        "howto": """
/* How-to category */
.category-howto .content h2 {
  counter-increment:step-counter;
}
.category-howto .content h2::before {
  content:counter(step-counter);
  display:inline-flex; align-items:center; justify-content:center;
  width:28px; height:28px; border-radius:50%;
  background:var(--accent); color:#fff; font-size:0.8em; font-weight:700;
  margin-right:10px; flex-shrink:0;
}
.category-howto .content { counter-reset:step-counter; }
.category-howto .content blockquote {
  border-left:3px solid var(--accent); background:color-mix(in srgb, var(--accent) 8%, transparent);
  padding:12px 16px; border-radius:0 6px 6px 0; margin:1em 0;
}
.category-howto .content blockquote strong:first-child {
  display:inline-block; margin-bottom:4px; text-transform:uppercase;
  font-size:0.82em; letter-spacing:0.5px;
}
.category-howto .content h2#quick-guide + ol,
.category-howto .content h2#quick-guide ~ ol:first-of-type {
  background:color-mix(in srgb, var(--accent) 8%, transparent);
  border:1px solid color-mix(in srgb, var(--accent) 20%, transparent);
  border-radius:8px; padding:14px 14px 14px 32px; font-size:0.95em; line-height:1.8;
}
.category-howto .content h2#quick-guide {
  counter-increment:none;
}
.category-howto .content h2#quick-guide::before {
  content:'\\26A1'; background:none; width:auto; height:auto; margin-right:6px;
}
""",
        "landscape": """
/* Landscape category */
.category-landscape .content h3 {
  display:flex; align-items:center; gap:8px;
  padding:8px 0; border-bottom:1px solid var(--border);
}
.category-landscape .content table {
  width:100%; border-collapse:collapse; margin:1em 0; font-size:0.92em;
}
.category-landscape .content table th {
  background:var(--accent); color:#fff; padding:8px 12px; text-align:left;
}
.category-landscape .content table td { padding:8px 12px; border-bottom:1px solid var(--border); }
.category-landscape .content table tr:nth-child(even) td { background:var(--bg-surface); }
.category-landscape .content blockquote {
  border-left:3px solid var(--gold, #d4a73a);
  background:color-mix(in srgb, var(--gold, #d4a73a) 8%, transparent);
  padding:10px 14px; border-radius:0 6px 6px 0;
}
""",
        "factcheck": """
/* Fact-check category */
.category-factcheck .hero {
  background:linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
}
.category-factcheck .content h2:first-of-type {
  font-size:1.4em; text-align:center; padding:16px 0; border:none;
  background:color-mix(in srgb, var(--accent) 8%, transparent);
  border-radius:8px; margin:1em 0;
}
.category-factcheck .content blockquote {
  position:relative; padding-left:20px;
}
.category-factcheck .content h2 ~ h3 {
  padding:6px 10px; border-radius:4px;
  border-left:3px solid var(--accent);
}
.category-factcheck .content strong:only-child {
  display:inline-block; padding:4px 12px; border-radius:4px;
  font-size:1.1em;
}
""",
        "visual": """
/* Visual explanation category */
.category-visual .hero-label::after { content:' / visual explanation'; }
.category-visual .content h2 {
  display:flex; align-items:center; gap:10px;
}
.category-visual .content h2::before {
  content:''; width:20px; height:2px; flex:0 0 auto;
  background:var(--accent);
}
.category-visual .report-section { margin-bottom:3.5rem; }
""",
    }
    # Always emit the per-category palette block when ANY category is set —
    # it contains body.category-X scoped rules so it only re-skins the page
    # for the matching category. The legacy `styles[category]` block adds
    # structural CSS specific to that one type.
    return palettes + styles.get(category, "")


_GENERIC_HEADINGS = {
    "report", "deep research report", "research",
    "executive summary", "summary", "tl;dr",
    "introduction", "overview", "abstract",
    "findings", "key findings", "results",
    "conclusion", "conclusions", "table of contents",
}


def _extract_report_title(markdown_text: str, fallback: str):
    """Pull a real title from the report's first heading rather than reusing
    the raw user query. Returns (title, markdown_with_title_stripped).

    Falls back to the query when no heading is present. Skips generic
    placeholders ("Executive Summary", "Introduction", etc.) and tries the
    next heading. If the chosen title was the report's own top heading, that
    heading is removed from the markdown so it doesn't duplicate the hero h1.
    """
    if not markdown_text:
        return fallback, markdown_text

    # Walk through headings (h1 first, then h2 anywhere) and use the first
    # non-generic one. Track the chosen match so we can strip it from the body.
    candidates = []
    for level, pattern in ((1, r'^# +(.+?)\s*$'), (2, r'^## +(.+?)\s*$')):
        for m in re.finditer(pattern, markdown_text, re.MULTILINE):
            cand = m.group(1).strip().rstrip('#').strip()
            if cand and cand.lower() not in _GENERIC_HEADINGS:
                candidates.append((level, m, cand))

    # Prefer h1 over h2; among same-level, prefer the earliest.
    candidates.sort(key=lambda t: (t[0], t[1].start()))
    if candidates:
        _level, match, title = candidates[0]
        stripped = markdown_text[:match.start()] + markdown_text[match.end():]
        return title, stripped.lstrip()
    return fallback, markdown_text


_ICON_LOGO_RE = re.compile(r'/(icon|logo|favicon)([._/-]|$)', re.IGNORECASE)


def _is_icon_or_logo_url(url: str) -> bool:
    """True if a URL path points at an icon/logo/favicon asset.

    Matches the icon/logo/favicon token only at a path-segment or basename
    boundary, so a real photo whose slug merely CONTAINS the word (e.g.
    /iconic-moment.jpg, /logos-history.png) is no longer dropped, while
    /icon.png, /logo.svg and /favicon.ico still are.
    """
    return bool(_ICON_LOGO_RE.search(url or ""))


_VISUAL_DIAGRAM_RE = re.compile(
    r"(?ms)^[ \t]*```visual-diagram[ \t]*\n(?P<body>.*?)^[ \t]*```[ \t]*$"
)
_VISUAL_HTML_RE = re.compile(
    r"(?ms)^[ \t]*```visual-html[ \t]*\n(?P<body>.*?)^[ \t]*```[ \t]*$"
)
_HTML_FENCE_RE = re.compile(
    r"(?ms)^[ \t]*```html[ \t]*\n(?P<body>.*?)^[ \t]*```[ \t]*$"
)
_VISUAL_LAYOUTS = {"flow", "cycle", "timeline", "layers"}


def _sanitize_visual_html(raw_html: str) -> str:
    """Sanitize a model-created visual artifact for a sandboxed srcdoc iframe."""
    soup = BeautifulSoup(raw_html or "", "html.parser")
    for tag in soup.find_all(("script", "iframe", "object", "embed", "form", "input", "button",
                              "textarea", "select", "video", "audio", "img", "link", "meta", "base")):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            lower = attr.lower()
            value = " ".join(tag.get(attr)) if isinstance(tag.get(attr), list) else str(tag.get(attr) or "")
            if lower.startswith("on") or lower in {"src", "srcset", "action", "formaction"}:
                del tag.attrs[attr]
            elif lower in {"href", "xlink:href"} and value and not value.startswith("#"):
                del tag.attrs[attr]
            elif lower == "style" and re.search(r"(?i)url\s*\(|expression\s*\(|javascript\s*:", value):
                del tag.attrs[attr]
    for style in soup.find_all("style"):
        css = style.string or style.get_text() or ""
        css = re.sub(r"(?is)@import\s+[^;]+;?", "", css)
        css = re.sub(r"(?is)url\s*\([^)]*\)", "none", css)
        css = re.sub(r"(?is)expression\s*\([^)]*\)", "", css)
        css = re.sub(r"(?i)javascript\s*:", "", css)
        style.string = css
    if soup.html:
        if not soup.head:
            soup.html.insert(0, soup.new_tag("head"))
        viewport = soup.new_tag("meta")
        viewport.attrs["name"] = "viewport"
        viewport.attrs["content"] = "width=device-width, initial-scale=1"
        soup.head.insert(0, viewport)
        return str(soup)
    return (
        '<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">'
        '</head><body>' + str(soup) + '</body></html>'
    )


def _extract_visual_html(report_markdown: str, *, allow_html_fallback: bool = False) -> Tuple[str, Dict[str, str]]:
    """Extract one visual-first artifact and embed it in an isolated iframe."""
    artifacts: Dict[str, str] = {}

    def replace(match: re.Match) -> str:
        if artifacts:
            return ""
        sanitized = _sanitize_visual_html(match.group("body"))
        if not sanitized.strip():
            return ""
        token = "ODYSSEUSVISUALHTML0TOKEN"
        artifacts[token] = (
            '<figure class="visual-html-story" role="group">'
            '<iframe sandbox="allow-same-origin" loading="lazy" scrolling="no" '
            'onload="const resize=()=>this.style.height=Math.min(2800,Math.max(560,this.contentDocument.documentElement.scrollHeight+4))+\'px\';'
            'resize();this._visualResizeObserver=new ResizeObserver(resize);'
            'this._visualResizeObserver.observe(this.contentDocument.documentElement)" '
            'title="Interactive visual explanation" '
            f'srcdoc="{html.escape(sanitized, quote=True)}"></iframe>'
            '</figure>'
        )
        return f"\n\n{token}\n\n"

    remaining = _VISUAL_HTML_RE.sub(replace, report_markdown or "")
    if allow_html_fallback and not artifacts:
        remaining = _HTML_FENCE_RE.sub(replace, remaining, count=1)
    return remaining, artifacts


def _diagram_text_lines(value: object, max_chars: int, max_lines: int) -> List[str]:
    """Wrap untrusted diagram copy into a small, predictable SVG text box."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return []
    words = text.split(" ")
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
        else:
            current = candidate
    if len(lines) < max_lines and current:
        lines.append(current)
    consumed = " ".join(lines)
    if len(consumed) < len(text) and lines:
        lines[-1] = lines[-1][: max(1, max_chars - 1)].rstrip() + "…"
    return lines[:max_lines]


def _svg_text(lines: List[str], x: float, y: float, class_name: str, step: int = 17) -> str:
    if not lines:
        return ""
    tspans = "".join(
        f'<tspan x="{x:.1f}" dy="{0 if index == 0 else step}">{html.escape(line)}</tspan>'
        for index, line in enumerate(lines)
    )
    return f'<text class="{class_name}" x="{x:.1f}" y="{y:.1f}" text-anchor="middle">{tspans}</text>'


def _render_visual_diagram(spec: object, index: int) -> str:
    """Render the model's constrained diagram JSON as escaped inline SVG."""
    if not isinstance(spec, dict):
        return ""
    layout = str(spec.get("layout") or "flow").strip().lower()
    if layout not in _VISUAL_LAYOUTS:
        layout = "flow"
    raw_nodes = spec.get("nodes")
    if not isinstance(raw_nodes, list):
        return ""
    nodes = []
    for raw in raw_nodes[:8]:
        if isinstance(raw, dict):
            label = str(raw.get("label") or "").strip()[:80]
            detail = str(raw.get("detail") or "").strip()[:140]
        else:
            label, detail = str(raw or "").strip()[:80], ""
        if label:
            nodes.append({"label": label, "detail": detail})
    if len(nodes) < 2:
        return ""

    width = 960
    node_w, node_h = 176, 92
    positions: List[Tuple[float, float]] = []
    if layout == "cycle":
        height = 520
        radius = 175 if len(nodes) > 4 else 145
        for i in range(len(nodes)):
            angle = -math.pi / 2 + (2 * math.pi * i / len(nodes))
            positions.append((width / 2 + math.cos(angle) * radius, height / 2 + math.sin(angle) * radius))
    elif layout == "layers":
        height = 120 + len(nodes) * 112
        positions = [(width / 2, 72 + i * 112) for i in range(len(nodes))]
        node_w = 520
        node_h = 78
    elif layout == "timeline":
        height = 190 + len(nodes) * 92
        positions = [
            (width * (0.32 if i % 2 == 0 else 0.68), 82 + i * 92)
            for i in range(len(nodes))
        ]
    elif len(nodes) <= 4:
        height = 230
        gap = (width - 120) / len(nodes)
        positions = [(60 + gap * (i + 0.5), 112) for i in range(len(nodes))]
        node_w = min(176, gap - 28)
    else:
        height = 120 + len(nodes) * 108
        positions = [(width / 2, 66 + i * 108) for i in range(len(nodes))]

    raw_edges = spec.get("edges")
    edges = []
    if isinstance(raw_edges, list):
        for edge in raw_edges[:12]:
            if not isinstance(edge, dict):
                continue
            try:
                source, target = int(edge.get("from")), int(edge.get("to"))
            except (TypeError, ValueError):
                continue
            if 0 <= source < len(nodes) and 0 <= target < len(nodes) and source != target:
                edges.append((source, target, str(edge.get("label") or "").strip()[:50]))
    if not edges:
        edges = [(i, (i + 1) % len(nodes), "") for i in range(len(nodes) - (0 if layout == "cycle" else 1))]

    marker_id = f"visual-arrow-{index}"
    edge_svg = []
    for source, target, label in edges:
        x1, y1 = positions[source]
        x2, y2 = positions[target]
        dx, dy = x2 - x1, y2 - y1
        distance = max((dx * dx + dy * dy) ** 0.5, 1)
        inset = min(node_w, node_h) * 0.48
        sx, sy = x1 + dx / distance * inset, y1 + dy / distance * inset
        tx, ty = x2 - dx / distance * inset, y2 - dy / distance * inset
        edge_svg.append(
            f'<path class="visual-edge" d="M {sx:.1f} {sy:.1f} L {tx:.1f} {ty:.1f}" marker-end="url(#{marker_id})"/>'
        )
        if label:
            edge_svg.append(
                _svg_text(_diagram_text_lines(label, 18, 1), (sx + tx) / 2, (sy + ty) / 2 - 7, "visual-edge-label", 14)
            )

    node_svg = []
    for node_index, (node, (cx, cy)) in enumerate(zip(nodes, positions)):
        node_svg.append(
            f'<g class="visual-node visual-node-{node_index % 4}">'
            f'<rect x="{cx - node_w / 2:.1f}" y="{cy - node_h / 2:.1f}" width="{node_w:.1f}" height="{node_h:.1f}" rx="12"/>'
            f'<circle class="visual-node-index" cx="{cx - node_w / 2 + 17:.1f}" cy="{cy - node_h / 2 + 17:.1f}" r="10"/>'
            f'<text class="visual-node-index-text" x="{cx - node_w / 2 + 17:.1f}" y="{cy - node_h / 2 + 21:.1f}" text-anchor="middle">{node_index + 1}</text>'
            f'{_svg_text(_diagram_text_lines(node["label"], 22 if node_w < 300 else 52, 2), cx, cy - 8, "visual-node-label")}'
            f'{_svg_text(_diagram_text_lines(node["detail"], 26 if node_w < 300 else 62, 2), cx, cy + 20, "visual-node-detail", 14)}'
            '</g>'
        )

    title = str(spec.get("title") or "Visual explanation").strip()[:120]
    return (
        f'<figure class="visual-diagram visual-diagram-{layout}" role="group">'
        f'<figcaption>{html.escape(title)}</figcaption>'
        f'<div class="visual-diagram-canvas"><svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}" preserveAspectRatio="xMidYMid meet">'
        f'<defs><marker id="{marker_id}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z"/></marker></defs>'
        + "".join(edge_svg)
        + "".join(node_svg)
        + '</svg></div></figure>'
    )


def _extract_visual_diagrams(report_markdown: str) -> Tuple[str, Dict[str, str]]:
    """Replace valid visual-diagram fences with inert tokens before Markdown."""
    diagrams: Dict[str, str] = {}

    def replace(match: re.Match) -> str:
        if len(diagrams) >= 6:
            return ""
        try:
            spec = json.loads(match.group("body"))
        except (TypeError, ValueError):
            return ""
        rendered = _render_visual_diagram(spec, len(diagrams))
        if not rendered:
            return ""
        token = f"ODYSSEUSVISUALDIAGRAM{len(diagrams)}TOKEN"
        diagrams[token] = rendered
        return f"\n\n{token}\n\n"

    return _VISUAL_DIAGRAM_RE.sub(replace, report_markdown or ""), diagrams


def generate_visual_report(
    question: str,
    report_markdown: str,
    sources: Optional[List[Dict]] = None,
    stats: Optional[Dict] = None,
    category: Optional[str] = None,
    session_id: Optional[str] = None,
    hidden_images: Optional[List[str]] = None,
) -> str:
    sources = sources or []
    stats = stats or {}
    hidden_images_set = set(hidden_images or [])

    # Strip thinking artifacts
    report_markdown = strip_thinking(report_markdown)

    # Use the report's first heading as the title (synthesized by the LLM)
    # rather than the raw user query. Fall back to the query if absent.
    synthesized, report_markdown = _extract_report_title(report_markdown, question)
    title_text = synthesized[:120] + ("..." if len(synthesized) > 120 else "")

    # Promote bold-only lines to ## headings if no markdown headings exist
    if not re.search(r'^#{2,3}\s+', report_markdown, re.MULTILINE):
        report_markdown = re.sub(
            r'^\*\*([^*]+)\*\*\s*$',
            lambda m: f'## {m.group(1).strip()}',
            report_markdown,
            flags=re.MULTILINE,
        )

    report_markdown, visual_artifacts = _extract_visual_html(
        report_markdown,
        allow_html_fallback=category == "visual",
    )
    report_markdown, visual_diagrams = _extract_visual_diagrams(report_markdown)
    report_html = _md_to_html(report_markdown)

    headings = _extract_headings(report_markdown)
    report_html = _apply_heading_ids(report_html, headings)
    for token, diagram_html in visual_diagrams.items():
        report_html = report_html.replace(f"<p>{token}</p>", diagram_html)
    for token, artifact_html in visual_artifacts.items():
        report_html = report_html.replace(f"<p>{token}</p>", artifact_html)

    # Collect all OG images from sources (skip icons, tiny images, known junk)
    _IMAGE_BLOCKLIST = {
        "cdn.shopify.com/s/files/1/0179/4388/7926/files/icon.png",
    }
    _seen_images = set()
    all_images = []
    image_labels = {}
    for s in sources:
        img = s.get("image", "")
        if (not (category == "visual" and visual_artifacts)
            and img and img.startswith("https://")
            and img not in _seen_images
            and img not in hidden_images_set
            and not img.endswith((".svg", ".ico", ".gif"))
            and not any(b in img for b in _IMAGE_BLOCKLIST)
            and not _is_icon_or_logo_url(img)):
            _seen_images.add(img)
            all_images.append(img)
            source_domain = ""
            try:
                source_domain = urlparse(str(s.get("url") or "")).hostname or ""
                if source_domain.startswith("www."):
                    source_domain = source_domain[4:]
            except Exception:
                pass
            image_labels[img] = (
                str(s.get("title") or source_domain or "Source image"),
                source_domain,
            )

    # Hero image = first available. data-img-url drives the per-image hide
    # button rendered by the script at the bottom of the page.
    hero_image_html = ""
    if all_images:
        hero_url = html.escape(all_images[0])
        hero_title, hero_domain = image_labels.get(all_images[0], ("", ""))
        hero_image_html = (
            f'<figure class="hero-image" data-img-url="{hero_url}">'
            f'<img src="{hero_url}" alt="{html.escape(hero_title)}" loading="lazy" '
            f'onerror="this.parentElement.style.display=\'none\'">'
            f'{_IMG_OVERLAY_BTNS}'
            '<figcaption>'
            f'<span>{html.escape(hero_title)}</span>'
            f'<span>{html.escape(hero_domain)}</span>'
            '</figcaption>'
            f'</figure>'
        )

    # Product quick-links bar
    if category == "product" and headings:
        product_headings = [h for h in headings if h["level"] == 3]
        if product_headings:
            pills = " ".join(
                f'<a href="#{h["slug"]}" class="quick-link">{html.escape(h["text"][:40])}</a>'
                for h in product_headings
            )
            report_html = f'<div class="quick-links-bar">{pills}</div>\n' + report_html

    # Inject remaining images between sections. Whatever isn't placed (hero
    # took [0], sections took the next `consumed`) becomes the spare pool the
    # reroll button draws from to swap out an irrelevant image in-page.
    report_html = _wrap_report_sections(report_html)
    section_pool = all_images[1:]
    report_html, _consumed = _inject_images(report_html, section_pool, image_labels)
    report_html, research_trace_html = _extract_research_trace(report_html)
    spare_images = section_pool[_consumed:]

    # Build TOC
    toc_lines = []
    for h in headings:
        depth_class = f"depth-{h['level']}"
        toc_lines.append(
            f'<a href="#{h["slug"]}" class="{depth_class}">{html.escape(h["text"])}</a>'
        )
    toc_html = "\n      ".join(toc_lines) if toc_lines else ""

    mobile_toc_html = ""
    if headings:
        options = []
        for h in headings:
            prefix = "\u2014 " if h["level"] == 3 else ""
            options.append(
                f'<option value="{h["slug"]}">{prefix}{html.escape(h["text"])}</option>'
            )
        mobile_toc_html = (
            '<div class="mobile-section-nav">'
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
            'aria-hidden="true"><line x1="8" y1="6" x2="21" y2="6"/>'
            '<line x1="8" y1="12" x2="21" y2="12"/>'
            '<line x1="8" y1="18" x2="21" y2="18"/>'
            '<line x1="3" y1="6" x2="3.01" y2="6"/>'
            '<line x1="3" y1="12" x2="3.01" y2="12"/>'
            '<line x1="3" y1="18" x2="3.01" y2="18"/></svg>'
            '<select id="mobile-section-select" aria-label="Jump to report section">'
            + "".join(options)
            + '</select></div>'
        )

    # Build stats bar
    visible_text = BeautifulSoup(report_html, "html.parser").get_text(" ", strip=True)
    word_count = len(re.findall(r"\b[\w'-]+\b", visible_text))
    reading_minutes = max(1, (word_count + 224) // 225)
    stat_items = []
    for key, label in [("Duration", "Duration"), ("Rounds", "Rounds"), ("Queries", "Queries"), ("URLs", "URLs Analyzed"), ("Model", "Model"), ("Search", "Search")]:
        val = stats.get(key)
        if val is not None:
            stat_items.append(
                f'<div class="stat"><span class="stat-value">{html.escape(str(val))}</span> {html.escape(label)}</div>'
            )
    stats_html = "\n  ".join(stat_items)

    generated_at = datetime.now()
    evidence = _source_evidence_summary(sources)
    evidence_profile_html = ""
    if sources:
        average_score = evidence["average_score"]
        quality_value = f'{average_score}/100' if average_score is not None else "Not rated"
        quality_label = (
            f'Average quality across {evidence["rated"]} rated sources'
            if evidence["rated"] else "Quality metadata unavailable"
        )
        evidence_profile_html = (
            '<section class="evidence-profile" aria-label="Evidence profile">'
            '<div class="evidence-intro evidence-read-time">'
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
            'aria-hidden="true"><circle cx="12" cy="12" r="9"/>'
            '<path d="M12 7v5l3 2"/></svg>'
            f'<span><strong>{reading_minutes} min</strong> read</span></div>'
            f'<div class="evidence-metric"><span class="evidence-value">{evidence["sources"]}</span>'
            '<span class="evidence-label">Sources used</span></div>'
            f'<div class="evidence-metric"><span class="evidence-value">{evidence["domains"]}</span>'
            '<span class="evidence-label">Distinct domains</span></div>'
            f'<div class="evidence-metric"><span class="evidence-value">{evidence["primary"]}</span>'
            '<span class="evidence-label">Primary sources</span></div>'
            f'<div class="evidence-metric"><span class="evidence-value">{quality_value}</span>'
            f'<span class="evidence-label">{quality_label}</span></div>'
            '</section>'
        )

    # Build one supporting-material panel containing Sources and Research Trace.
    support_details = []
    if sources:
        items = []
        for i, s in enumerate(sources, 1):
            url = s.get("url", "")
            title = html.escape(s.get("title", "") or url)
            domain = ""
            try:
                domain = urlparse(url).hostname or ""
                if domain.startswith("www."):
                    domain = domain[4:]
            except Exception:
                domain = url
            kind = str(s.get("source_kind") or "").strip().lower()
            retrieval = str(s.get("retrieval") or "").strip().lower()
            reason = str(s.get("source_reason") or "").strip()
            try:
                score = int(s.get("source_score"))
            except (TypeError, ValueError):
                score = None
            badges = []
            if kind:
                kind_class = " primary" if kind == "primary" else ""
                badges.append(
                    f'<span class="source-badge{kind_class}">{html.escape(kind)}</span>'
                )
            if retrieval == "browser":
                badges.append('<span class="source-badge">Rendered read</span>')
            meta_title = f' title="{html.escape(reason)}"' if reason else ""
            score_html = (
                f'<span class="source-score"{meta_title}>{score}/100</span>'
                if score is not None else ""
            )
            items.append(
                f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">'
                f'<span class="snum">{i}.</span>'
                f'<span class="source-copy"><span class="source-title">{title}</span>'
                f'<span class="source-meta"><span>{html.escape(domain)}</span>{"".join(badges)}</span></span>'
                f'{score_html}'
                f'</a>'
            )
        support_details.append(
            '<details>\n'
            f'<summary>Sources ({len(sources)})</summary>\n'
            '<div class="sources-list">\n'
            + "\n".join(items)
            + "\n</div>\n</details>"
        )
    if research_trace_html:
        support_details.append(research_trace_html)
    sources_html = (
        '<div class="sources-panel">\n'
        + "\n".join(support_details)
        + "\n</div>"
        if support_details else ""
    )

    timestamp = generated_at.strftime("%B %d, %Y at %H:%M")
    standard_variant = None if category else _standard_visual_variant(question, session_id)

    # Build description for OG/meta tags (first 160 chars of plain text)
    desc_text = re.sub(r'[#*_\[\]()]', '', report_markdown)[:160].strip()
    og_image_meta = ""
    if all_images:
        og_image_meta = f'<meta property="og:image" content="{html.escape(all_images[0])}">'

    chat_cta_html = ""
    if session_id:
        chat_cta_html = (
            '<div class="chat-cta">'
            '<button id="btn-chat-about" class="chat-cta-btn" '
            f'data-research-id="{html.escape(session_id)}">'
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
            'width="18" height="18">'
            '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'
            '</svg>'
            '<span>Discuss</span>'
            '</button>'
            '<div class="chat-cta-hint">Opens a new chat with this report as context.</div>'
            '</div>'
        )

    # Visual explanations are presentation-first, so their toolbar never
    # exposes the image restoration control. Other report formats retain it.
    restore_btn_html = ""
    if category != "visual" and session_id and hidden_images_set:
        restore_btn_html = (
            '<button id="btn-restore-images" type="button" '
            f'title="Restore {len(hidden_images_set)} hidden image'
            f'{"" if len(hidden_images_set) == 1 else "s"}">'
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>'
            '</svg>'
            f'Show hidden ({len(hidden_images_set)})'
            '</button>'
        )

    return _TEMPLATE.format(
        title=html.escape(title_text),
        description=html.escape(desc_text),
        og_image_meta=og_image_meta,
        question_html=html.escape(synthesized),
        hero_image_html=hero_image_html,
        stats_html=stats_html,
        evidence_profile_html=evidence_profile_html,
        mobile_toc_html=mobile_toc_html,
        toc_html=toc_html,
        report_html=report_html,
        sources_html=sources_html,
        chat_cta_html=chat_cta_html,
        restore_btn_html=restore_btn_html,
        timestamp=timestamp,
        category_css=_category_css(category, standard_variant),
        body_class=(
            f"category-{html.escape(str(category))}" if category
            else f"standard-report-v{standard_variant}"
        ),
        session_id_js=json_dumps_str(session_id or ""),
        spare_images_js=_json_for_script(spare_images),
    )


def _json_for_script(value) -> str:
    """JSON-encode a value safe to embed inside a <script> block.

    json.dumps doesn't escape '/', so a string containing the literal
    substring '</script>' would terminate the script element early.
    Escape the closing slash to keep the inline JSON inert as HTML.
    """
    return json.dumps(value).replace("</", "<\\/")


def json_dumps_str(s: str) -> str:
    """JSON-encode a string so it's safe to embed inside a <script> block."""
    return _json_for_script(s)
