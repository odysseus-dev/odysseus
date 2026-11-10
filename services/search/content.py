"""Webpage content fetching with caching, PDF extraction, and summarization helpers."""

import copy
import io
import json
import os
import re
import logging
from datetime import datetime, timedelta
from typing import List
from urllib.parse import urljoin, urlsplit, quote

import httpx
from bs4 import BeautifulSoup

from src.constants import WEB_FETCH_SOFT_MAX_BYTES, WEB_FETCH_HARD_MAX_BYTES, WEB_FETCH_USER_AGENT
from src import outbound_fetch as _outbound_fetch

from .analytics import RateLimitError, error_logger
from .cache import (
    CONTENT_CACHE_DIR,
    content_cache_index,
    generate_cache_key,
    cleanup_cache,
)

logger = logging.getLogger(__name__)

def _is_private_address(addr):
    return _outbound_fetch._is_private_address(addr)


def _resolve_hostname_ips(hostname):
    return _outbound_fetch._resolve_hostname_ips(hostname)


def _public_http_url(url):
    return _outbound_fetch._public_http_url(url, resolver=_resolve_hostname_ips)


def _resolve_public_ips(url):
    return _outbound_fetch._resolve_public_ips(url, resolver=_resolve_hostname_ips)


_PinnedBackend = _outbound_fetch._PinnedBackend
_PinnedTransport = _outbound_fetch._PinnedTransport
BodyTooLargeError = _outbound_fetch.BodyTooLargeError
_CappedFetch = _outbound_fetch._CappedFetch


def _get_public_url(url, headers, timeout, max_redirects=5, max_bytes=None):
    return _outbound_fetch._get_public_url(
        url,
        headers=headers,
        timeout=timeout,
        max_redirects=max_redirects,
        max_bytes=max_bytes,
        resolve_public_ips=_resolve_public_ips,
        transport_factory=_PinnedTransport,
    )


# PDF extraction (optional dependency)
try:
    from pdfminer.high_level import extract_text as pdf_extract_text
except ImportError:
    pdf_extract_text = None  # type: ignore

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None  # type: ignore


def _extract_pdf_text(pdf_bytes: bytes, url: str = "") -> str:
    """Extract PDF text with available permissive dependencies."""
    # Prefer pypdf's layout mode. Plain text extraction and pdfminer often
    # collapse table columns into an ambiguous number stream, which makes a
    # correct source passage easy for the model to misread.
    if PdfReader is not None:
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            pages: List[str] = []
            for idx, page in enumerate(reader.pages):
                try:
                    try:
                        page_text = page.extract_text(extraction_mode="layout") or ""
                    except TypeError:
                        page_text = page.extract_text() or ""
                except Exception as e:
                    logger.warning(f"pypdf extraction failed for {url} page {idx + 1}: {e}")
                    page_text = ""
                if page_text.strip():
                    pages.append(f"[Page {idx + 1}]\n{page_text.strip()}")
            if pages:
                return "\n\n".join(pages)
        except Exception as e:
            logger.warning(f"pypdf extraction failed for {url}: {e}")

    if pdf_extract_text is not None:
        try:
            text = pdf_extract_text(io.BytesIO(pdf_bytes)) or ""
            if text.strip():
                return text
        except Exception as e:
            logger.warning(f"pdfminer extraction failed for {url}: {e}")

    if PdfReader is None and pdf_extract_text is None:
        logger.error("No PDF text extractor installed; install pdfminer.six or pypdf.")
    return ""


# ----------------------------------------------------------------------
# HTML extraction helpers
# ----------------------------------------------------------------------
def _extract_meta(soup: BeautifulSoup) -> dict:
    """Pull meta description and keywords if present."""
    description = ""
    keywords = ""
    desc_tag = soup.find("meta", attrs={"name": re.compile("description", re.I)})
    if desc_tag and desc_tag.get("content"):
        description = desc_tag["content"].strip()
    kw_tag = soup.find("meta", attrs={"name": re.compile("keywords", re.I)})
    if kw_tag and kw_tag.get("content"):
        keywords = kw_tag["content"].strip()
    return {"description": description, "keywords": keywords}


def _extract_og_image(soup: BeautifulSoup) -> str:
    """Extract the best representative image URL from meta tags.

    Only returns absolute http(s) URLs -- skips relative paths and data URIs.
    """
    candidates = []
    for prop in ("og:image", "og:image:url", "og:image:secure_url"):
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content", "").strip():
            candidates.append(tag["content"].strip())
    tag = soup.find("meta", attrs={"name": "twitter:image"})
    if tag and tag.get("content", "").strip():
        candidates.append(tag["content"].strip())
    tag = soup.find("meta", attrs={"name": "thumbnail"})
    if tag and tag.get("content", "").strip():
        candidates.append(tag["content"].strip())
    for url in candidates:
        if url.startswith(("https://", "http://")) and not url.endswith((".svg", ".ico")):
            return url
    return ""


def _linked_text(area, base_url: str) -> str:
    """Preserve observed anchor destinations and block order without fetching links."""
    area = copy.copy(area)
    for anchor in area.find_all('a', href=True):
        label = ' '.join(anchor.get_text(' ', strip=True).split())
        href = str(anchor.get('href') or '').strip()
        if not label or not href or href.startswith('#'):
            continue
        target = urljoin(base_url, href)
        try:
            parsed = urlsplit(target)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                continue
        except ValueError:
            continue
        label = re.sub(r'([\\\[\]])', r'\\\1', label)
        target = quote(target, safe=":/?#[]@!$&'()*+,;=%~_-.")
        anchor.replace_with(f'[{label}](<{target}>)')
    for block in area.find_all(['p', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'article', 'br']):
        block.insert_before('\n')
        block.insert_after('\n')
    return '\n'.join(' '.join(line.split()) for line in area.get_text(' ', strip=False).splitlines() if line.strip())


def _page_entries(areas, base_url: str) -> list[dict]:
    """Recognize repeated listing structures, retaining DOM order, not popularity."""
    entries = []
    seen = set()
    for area in areas:
        nodes = ([area] if area.name == 'article' else []) + area.find_all(['li', 'article', 'tr'])
        for node in nodes:
            anchor = None
            if node.name == 'tr':
                cells = node.find_all(['td', 'th'], recursive=False)
                if cells and re.fullmatch(r'\d+[.)]?', cells[0].get_text(strip=True)):
                    anchor = next((a for a in node.find_all('a', href=True)
                                   if a.get_text(strip=True)), None)
            elif node.name == 'article':
                heading = node.find(['h1', 'h2', 'h3', 'h4'])
                anchor = heading.find('a', href=True) if heading else None
            elif node.parent and node.parent.name == 'ol':
                anchor = node.find('a', href=True)
            if not anchor:
                continue
            title = ' '.join(anchor.get_text(' ', strip=True).split())
            href = str(anchor.get('href') or '').strip()
            if not title or not href or href.startswith('#'):
                continue
            url = urljoin(base_url, href)
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                    continue
            except ValueError:
                continue
            if (title, url) not in seen:
                seen.add((title, url))
                entries.append({'title': title, 'url': url})
            if len(entries) == 100:
                return entries
    return entries if len(entries) >= 2 else []


def _extract_lists(soup: BeautifulSoup) -> List[List[str]]:
    """Return a list of lists, each inner list representing a <ul>/<ol>."""
    all_lists = []
    for lst in soup.find_all(["ul", "ol"]):
        items = [li.get_text(separator=" ", strip=True) for li in lst.find_all("li")]
        if items:
            all_lists.append(items)
    return all_lists


def _extract_tables(soup: BeautifulSoup) -> List[List[List[str]]]:
    """Return a list of tables, each table is a list of rows, each row a list of cell texts."""
    tables_data = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(separator=" ", strip=True) for td in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if rows:
            tables_data.append(rows)
    return tables_data


def _extract_code_blocks(soup: BeautifulSoup) -> List[str]:
    """Collect text from <pre> and <code> blocks."""
    blocks = []
    for tag in soup.find_all(["pre", "code"]):
        txt = tag.get_text(separator=" ", strip=True)
        if txt:
            blocks.append(txt)
    return blocks


def _detect_js_frameworks(soup: BeautifulSoup) -> bool:
    """Very naive detection of common JS frameworks."""
    js_indicators = [
        "react", "angular", "vue", "svelte", "next", "nuxt",
        "ember", "backbone", "jquery", "polymer", "mithril",
    ]
    for script in soup.find_all("script"):
        src = script.get("src", "").lower()
        if any(fr in src for fr in js_indicators):
            return True
        if script.string:
            content = script.string.lower()
            if any(fr in content for fr in js_indicators):
                return True
    if soup.find(attrs={"data-reactroot": True}) or soup.find(attrs={"ng-app": True}):
        return True
    return False


def _empty_result(url: str, error: str = "") -> dict:
    """Build a standard failure result dict."""
    return {
        "url": url,
        "title": "",
        "content": "",
        "lists": [],
        "tables": [],
        "code_blocks": [],
        "meta_description": "",
        "meta_keywords": "",
        "js_rendered": False,
        "js_message": "",
        "success": False,
        "error": error,
    }


# ----------------------------------------------------------------------
# Main content fetcher
# ----------------------------------------------------------------------
def fetch_webpage_content(url: str, timeout: int = 5, retry_attempt: int = 0,
                          max_bytes: int = None) -> dict:
    """Fetch and extract meaningful content from a webpage with caching.

    ``max_bytes`` raises the download budget per call (clamped to the hard
    cap); the default is the soft cap. When the body is cut short the result
    carries ``truncated``/``fetched_bytes``/``total_bytes`` so callers can
    tell the model the content is partial (#3812).
    """
    effective_cap = min(max_bytes or WEB_FETCH_SOFT_MAX_BYTES, WEB_FETCH_HARD_MAX_BYTES)
    # The cap is part of the cache identity: a truncated soft-cap fetch must
    # not be served to a later full-budget request for the same URL.
    cache_key = generate_cache_key(f"{url}#cap={effective_cap}#extract=semantic-links-v7")
    cache_file = CONTENT_CACHE_DIR / f"{cache_key}.cache"

    # Check cache
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            timestamp = datetime.fromisoformat(cached_data["timestamp"])
            if datetime.now() - timestamp < timedelta(hours=2):
                logger.debug(f"Content cache hit for URL: {url}")
                return cached_data["data"]
            else:
                cache_file.unlink(missing_ok=True)
                content_cache_index.pop(cache_key, None)
        except Exception as e:
            logger.warning(f"Failed to read content cache for {url}: {e}")
            cache_file.unlink(missing_ok=True)
            content_cache_index.pop(cache_key, None)

    # Fetch
    try:
        headers = {
            "User-Agent": WEB_FETCH_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
        }
        response = _get_public_url(url, headers=headers, timeout=timeout,
                                   max_bytes=effective_cap)

        if response.status_code == 429:
            raise RateLimitError(f"Rate limit hit for {url} (attempt {retry_attempt})")

        response.raise_for_status()
    except BodyTooLargeError as e:
        error_logger.warning(f"Refused oversized body for {url}: {e}")
        return _empty_result(url, f"TooLarge: {e}")
    except httpx.HTTPStatusError as e:
        error_logger.warning(f"HTTP {e.response.status_code} fetching {url}: {e}")
        return _empty_result(url, f"HTTP {e.response.status_code}: {e}")
    except httpx.RequestError as e:
        error_logger.error(f"NetworkError fetching {url} (attempt {retry_attempt}): {e}")
        return _empty_result(url, f"NetworkError: {e}")
    except RateLimitError as e:
        error_logger.error(str(e))
        return _empty_result(url, str(e))

    # Size bookkeeping shared by every content branch below. getattr keeps
    # plain httpx.Response stand-ins (tests) working without the cap fields.
    _size_fields = {
        "truncated": getattr(response, "truncated", False),
        "fetched_bytes": len(response.content),
        "total_bytes": getattr(response, "declared_bytes", None),
    }

    # PDF handling
    content_type = response.headers.get("Content-Type", "").lower()
    if "application/pdf" in content_type or url.lower().endswith(".pdf"):
        if (
            _size_fields["truncated"]
            and effective_cap < WEB_FETCH_HARD_MAX_BYTES
            and (
                _size_fields["total_bytes"] is None
                or _size_fields["total_bytes"] <= WEB_FETCH_HARD_MAX_BYTES
            )
        ):
            try:
                response = _get_public_url(
                    url,
                    headers=headers,
                    timeout=timeout,
                    max_bytes=WEB_FETCH_HARD_MAX_BYTES,
                )
                _size_fields = {
                    "truncated": getattr(response, "truncated", False),
                    "fetched_bytes": len(response.content),
                    "total_bytes": getattr(response, "declared_bytes", None),
                }
                effective_cap = WEB_FETCH_HARD_MAX_BYTES
            except BodyTooLargeError as e:
                error_logger.warning(f"Refused oversized PDF body for {url}: {e}")
                return _empty_result(url, f"TooLarge: {e}")
            except Exception as e:
                logger.warning(f"Full-budget PDF retry failed for {url}: {e}")
        if _size_fields["truncated"]:
            # A PDF cut mid-stream is not parseable; unlike text there is no
            # useful partial result, so report the budget problem instead.
            _declared = _size_fields["total_bytes"]
            error = (
                f"TooLarge: PDF decoded body exceeded the {effective_cap:,}-byte fetch budget"
                + (f" (declared compressed size {_declared:,} bytes)" if _declared else "")
                + "; retry with a larger budget if it fits under the hard cap"
            )
            return {**_empty_result(url, error), **_size_fields}
        pdf_text = _extract_pdf_text(response.content, url)
        result = {
            "url": url,
            "title": os.path.basename(url),
            "content": pdf_text,
            "lists": [],
            "tables": [],
            "code_blocks": [],
            "meta_description": "",
            "meta_keywords": "",
            "js_rendered": False,
            "js_message": "",
            "success": bool(pdf_text),
            "error": "" if pdf_text else "Failed to extract PDF text",
            **_size_fields,
        }
        _cache_result(cache_file, cache_key, result, url)
        return result

    # Plain-text / Markdown / JSON handling. Sources like
    # raw.githubusercontent.com serve Markdown as `text/plain`, JSON APIs and
    # raw config files serve `application/json`, and a lot of code and tool
    # docs live in `.md` / `.txt`. These have no HTML structure, so the HTML
    # branch below would extract nothing and report "no readable text content".
    # Return the body verbatim instead. The `is_html` guard keeps real HTML
    # (including `application/xhtml+xml`) on the parsing path; the `json` check
    # covers `application/json` and `+json` suffixes; the URL-suffix fallback
    # catches servers that mislabel text files as `application/octet-stream`.
    is_html = "html" in content_type
    is_json = "json" in content_type
    # Atom and XML are common public API formats (for example scholarly,
    # release, and government feeds). Parsing them through the HTML content
    # heuristic can yield an empty body even though the response contains
    # complete structured evidence. Preserve the source text so the caller
    # can inspect the fields or process it with workspace tools.
    is_xml = "xml" in content_type
    url_path = url.lower().split("?", 1)[0].split("#", 1)[0]
    looks_like_text_file = url_path.endswith(
        (".md", ".markdown", ".txt", ".text", ".json", ".jsonl")
    )
    if not is_html and (
        content_type.startswith("text/") or is_json or is_xml or looks_like_text_file
    ):
        text_body = (response.text or "").strip()
        result = {
            "url": url,
            "title": os.path.basename(url_path) or url,
            "content": text_body,
            "lists": [],
            "tables": [],
            "code_blocks": [],
            "meta_description": "",
            "meta_keywords": "",
            "js_rendered": False,
            "js_message": "",
            "success": bool(text_body),
            "error": "" if text_body else "Empty response body",
            **_size_fields,
        }
        _cache_result(cache_file, cache_key, result, url)
        return result

    # HTML handling
    try:
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        error_logger.error(f"ParseError parsing HTML from {url} (attempt {retry_attempt}): {e}")
        result = _empty_result(url, f"ParseError: {e}")
        _cache_result(cache_file, cache_key, result, url)
        return result

    title_tag = soup.find("title")
    title_text = title_tag.get_text(strip=True) if title_tag else ""
    meta_info = _extract_meta(soup)
    link_base = str(getattr(response, 'url', None) or url)
    base_tag = soup.find('base', href=True)
    if base_tag:
        candidate_base = urljoin(link_base, str(base_tag['href']))
        if candidate_base.startswith(('https://', 'http://')):
            link_base = candidate_base
    og_image = _extract_og_image(soup)
    js_rendered = _detect_js_frameworks(soup)
    js_message = "Page appears to be rendered by a JavaScript framework; content may be incomplete." if js_rendered else ""

    # Prefer semantic containers even without CSS classes. Work on a copy so
    # lists/tables and metadata extraction below still see the original DOM.
    text_soup = copy.copy(soup)
    for noise in text_soup.select('script, style, noscript, template, nav, footer, aside, [role="navigation"], [role="banner"], [role="contentinfo"]'):
        noise.extract()
    main_content = ""
    semantic_main = text_soup.find('main') or text_soup.find(attrs={'role': 'main'})
    articles = semantic_main.find_all('article') if semantic_main else text_soup.find_all('article')
    # A single substantive article is a more precise content boundary than
    # main, which commonly also contains tags, related links and comment forms.
    # Multiple article cards usually form a listing: keep its main context.
    if semantic_main and len(articles) == 1 and len(articles[0].get_text(strip=True)) >= 200:
        content_areas = articles
    else:
        content_areas = [semantic_main] if semantic_main else articles
    if not content_areas:
        content_areas = text_soup.find_all(
            ["section", "div"],
            class_=re.compile("content|main|body|article|post|entry|text", re.I),
        )
    # Ancestor and child matches contain the same text. Emit each subtree once,
    # while retaining separate sibling articles/cards.
    candidate_ids = {id(area) for area in content_areas}
    content_areas = [area for area in content_areas
                     if not any(id(parent) in candidate_ids for parent in area.parents)]
    if content_areas:
        for area in content_areas:
            main_content += area.get_text(separator=" ", strip=True) + " "
    linked_areas = content_areas
    main_content = re.sub(r"\s+", " ", main_content).strip()

    # If the heuristic finds only a tiny wrapper, fall back to body text with
    # obvious boilerplate stripped so UI/deep-research search results do not
    # look empty for app/landing pages.
    THIN_CONTENT_CHARS = 600
    if len(main_content) < THIN_CONTENT_CHARS and not semantic_main:
        body = text_soup.find("body")
        if body:
            body_copy = copy.copy(body)
            for noise in body_copy.find_all(
                ["script", "style", "noscript", "template", "nav", "header", "footer", "aside"]
            ):
                noise.extract()
            body_text = re.sub(r"\s+", " ", body_copy.get_text(separator=" ", strip=True)).strip()
            if len(body_text) > len(main_content):
                main_content = body_text
                linked_areas = [body_copy]

    # HTTP 200 does not imply an article was retrieved. Classify only short
    # interstitials with both a challenge title and corroborating body text;
    # ordinary articles mentioning CAPTCHA must remain readable evidence.
    challenge_title = title_text.strip().lower().rstrip('.!')
    challenge_titles = {'client challenge', 'just a moment', 'security verification', 'verify you are human'}
    if (challenge_title in challenge_titles and len(main_content) < 2000
            and re.search(r"required part of this site|verify (?:that )?you are human|checking your browser|enable javascript|security verification|performing security", main_content, re.I)):
        return {
            **_empty_result(url, 'Page access challenge: article content was not retrieved. Try private_browser or another authoritative source; do not treat the challenge page as evidence.'),
            'title': title_text,
            'error_kind': 'access_challenge',
            **_size_fields,
        }

    result = {
        "url": url,
        "title": title_text,
        "content": main_content,
        "linked_content": '\n'.join(_linked_text(area, link_base) for area in linked_areas),
        "page_entries": _page_entries(linked_areas, link_base),
        "lists": _extract_lists(soup),
        "tables": _extract_tables(soup),
        "code_blocks": _extract_code_blocks(soup),
        "meta_description": meta_info.get("description", ""),
        "meta_keywords": meta_info.get("keywords", ""),
        "og_image": og_image,
        "js_rendered": js_rendered,
        "js_message": js_message,
        "success": True,
        "error": "",
        **_size_fields,
    }
    _cache_result(cache_file, cache_key, result, url)
    return result


def _cache_result(cache_file, cache_key: str, result: dict, url: str):
    """Write a result to the content cache."""
    try:
        cache_data = {"timestamp": datetime.now().isoformat(), "data": result}
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f)
        content_cache_index[cache_key] = datetime.now()
        cleanup_cache(CONTENT_CACHE_DIR, content_cache_index, timedelta(hours=2))
    except Exception as e:
        logger.warning(f"Failed to write content cache for {url}: {e}")


# ----------------------------------------------------------------------
# Content summarization helpers
# ----------------------------------------------------------------------
def extract_key_points(text: str) -> List[str]:
    """Pull out bullet-style key points from a block of text."""
    points: List[str] = []
    bullet_pat = re.compile(r"^\s*[-*•]\s+(.*)")
    numbered_pat = re.compile(r"^\s*\d+[\.\)]\s+(.*)")
    for line in text.splitlines():
        m = bullet_pat.match(line) or numbered_pat.match(line)
        if m:
            points.append(m.group(1).strip())
    return points


def get_tldr(text: str, max_sentences: int = 3) -> str:
    """Produce a very short TL;DR by taking the first few sentences."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    selected = [s.strip() for s in sentences if s][:max_sentences]
    return " ".join(selected)


def extract_quotes(text: str) -> List[str]:
    """Return quoted excerpts that are at least 15 characters long."""
    # Backreference the opening quote so the closing quote must match it —
    # otherwise `"text'` (open double, close single) is treated as a quote.
    return [m.group(2).strip() for m in re.finditer(r'(["\'])([^"\']{15,}?)\1', text)]


def extract_statistics(text: str) -> List[str]:
    """Find numbers, percentages, dates and simple measurements."""
    # Match a comma-grouped number (1,000,000) OR a plain digit run (50000) —
    # the old `\d{1,3}(?:,\d{3})*` matched only the first 3 digits of a
    # comma-less number, and the trailing `\b` dropped a closing `%`.
    pattern = re.compile(
        r"\b(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(%|percent|‰|per cent|[a-zA-Z]+)?",
        re.IGNORECASE,
    )
    return [m.group(0).strip() for m in pattern.finditer(text)]
