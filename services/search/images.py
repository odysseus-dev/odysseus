"""SearXNG-backed image search.

Image results are intentionally separate from ``comprehensive_web_search``:
the text pipeline ranks and fetches page content, which is both unnecessary
and slow for a gallery.  This module only normalizes SearXNG's ``images``
category into a small, provider-agnostic row shape.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from src.constants import WEB_FETCH_USER_AGENT

from .providers import _get_search_instance, _safesearch_for

logger = logging.getLogger(__name__)

_MAX_RESULTS = 50
_DEFAULT_RESULTS = 24
_RESOLUTION_RE = re.compile(r"(\d+)\s*[x\u00d7]\s*(\d+)")


def _clean_url(value: Any) -> str:
    """Return an absolute http(s) URL, or '' for anything unsafe/useless."""
    url = str(value or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return url


def _parse_resolution(value: Any) -> tuple:
    match = _RESOLUTION_RE.search(str(value or ""))
    if not match:
        return None, None
    try:
        return int(match.group(1)), int(match.group(2))
    except (TypeError, ValueError):
        return None, None


def _source_label(page_url: str, image_url: str) -> str:
    host = urlparse(page_url or image_url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def image_search(
    query: str,
    count: Optional[int] = None,
    time_filter: Optional[str] = None,
    timeout: float = 15,
) -> List[Dict[str, Any]]:
    """Search images through the configured SearXNG instance.

    Returns normalized rows: title, image, thumbnail, page_url, width, height,
    source, format.  Fails open to an empty list so callers can show a normal
    "No results" state instead of breaking the page.
    """
    query = str(query or "").strip()
    if not query:
        return []

    try:
        limit = int(count) if count is not None else _DEFAULT_RESULTS
    except (TypeError, ValueError):
        limit = _DEFAULT_RESULTS
    limit = max(1, min(limit, _MAX_RESULTS))

    instance = (_get_search_instance() or "").rstrip("/")
    if not instance:
        logger.warning("Image search skipped: no SearXNG instance configured")
        return []

    params = {
        "q": query,
        "format": "json",
        "language": "en",
        "categories": "images",
        "safesearch": _safesearch_for("searxng"),
    }
    if time_filter in ("day", "week", "month", "year"):
        params["time_range"] = time_filter

    try:
        response = httpx.get(
            f"{instance}/search",
            params=params,
            headers={"User-Agent": WEB_FETCH_USER_AGENT},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("SearXNG image search failed: %s", exc)
        return []

    results: List[Dict[str, Any]] = []
    seen = set()
    for item in payload.get("results", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        image = _clean_url(item.get("img_src"))
        thumbnail = _clean_url(item.get("thumbnail_src")) or _clean_url(item.get("thumbnail")) or image
        if not image and not thumbnail:
            continue
        page_url = _clean_url(item.get("url"))
        key = image or thumbnail
        if key in seen:
            continue
        seen.add(key)
        width, height = _parse_resolution(item.get("resolution"))
        title = str(item.get("title") or "").strip() or "Untitled image"
        results.append({
            "title": title,
            "image": image or thumbnail,
            "thumbnail": thumbnail or image,
            "page_url": page_url,
            "width": width,
            "height": height,
            "source": _source_label(page_url, image or thumbnail),
            "format": str(item.get("img_format") or "").strip(),
            "provider": "searxng",
        })
        if len(results) >= limit:
            break

    logger.info("SearXNG image search returned %s results for: %s", len(results), query)
    return results
