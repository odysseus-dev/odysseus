"""Fetch a page locally and return readable text for ``web_extract``.

Select it explicitly with ``web.extract_backend: odysseus-local-extract``; it is never an autodetect pick for search.
"""
from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from plugins.web._common import BaseWebSearchProvider, document, page_error, run_extract, setup_schema

logger = logging.getLogger(__name__)

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 20
USER_AGENT = 'Mozilla/5.0 (compatible; Hermes-local-extract/0.1)'
_TEXT_TYPES = ('text/html', 'application/xhtml+xml', 'text/plain', 'application/json')
_SKIP = {'script', 'style', 'noscript', 'svg', 'nav', 'header', 'footer', 'aside', 'form', 'template', 'iframe'}
_BLOCK = {'p', 'div', 'section', 'article', 'main', 'br', 'tr', 'table', 'ul', 'ol', 'blockquote', 'figure', 'details'}


def _is_public_url(url: str) -> bool:
    """Hermes' own SSRF check (private, loopback, link-local and metadata addresses are refused)."""
    from tools.url_safety import is_safe_url
    return bool(is_safe_url(url))


class _Readable(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ''
        self._parts: list[str] = []
        self._skip = 0
        self._in_title = False
        self._pre = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP:
            self._skip += 1
        elif tag == 'title':
            self._in_title = True
        elif tag == 'pre':
            self._pre += 1
            self._parts.append('\n```\n')
        elif re.fullmatch(r'h[1-6]', tag):
            self._parts.append('\n\n' + '#' * int(tag[1]) + ' ')
        elif tag == 'li':
            self._parts.append('\n- ')
        elif tag in _BLOCK:
            self._parts.append('\n')

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == 'title':
            self._in_title = False
        elif tag == 'pre':
            self._pre = max(0, self._pre - 1)
            self._parts.append('\n```\n')
        elif re.fullmatch(r'h[1-6]', tag) or tag in _BLOCK:
            self._parts.append('\n')

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._parts.append(data if self._pre else re.sub(r'\s+', ' ', data))

    def text(self) -> str:
        raw = ''.join(self._parts)
        raw = re.sub(r'[ \t]+\n', '\n', raw)
        return re.sub(r'\n{3,}', '\n\n', raw).strip()


def _fetch(url: str) -> tuple[str, str, str]:
    """Follow redirects by hand so each hop is re-checked. Returns (final_url, content_type, body)."""
    current = url
    with httpx.Client(follow_redirects=False, timeout=TIMEOUT_SECONDS, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,text/plain,*/*;q=0.5'}) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            if urlparse(current).scheme not in ('http', 'https'):
                raise ValueError('only http and https URLs can be fetched')
            if not _is_public_url(current):
                raise ValueError('Blocked: URL targets a private or internal network address')
            with client.stream('GET', current) as response:
                if response.is_redirect:
                    location = response.headers.get('location')
                    if not location:
                        raise ValueError('redirect without a location')
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise ValueError(f'the page answered HTTP {response.status_code}')
                kind = response.headers.get('content-type', '').split(';')[0].strip().lower()
                if kind and kind not in _TEXT_TYPES:
                    raise ValueError(f'unsupported content type {kind}')
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError('the page is larger than 2 MB')
                    chunks.append(chunk)
                body = b''.join(chunks).decode(response.encoding or 'utf-8', errors='replace')
                return current, kind or 'text/html', body
    raise ValueError('too many redirects')


class LocalExtractProvider(BaseWebSearchProvider):
    NAME = 'odysseus-local-extract'
    DISPLAY_NAME = 'Local page extract (no third party)'
    KEY_ENV = ''
    EXTRACT = True

    def is_available(self) -> bool:
        return True

    def supports_search(self) -> bool:
        return False

    def extract(self, urls: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        def body() -> list[dict[str, Any]]:
            results = []
            for url in urls:
                try:
                    final_url, kind, text = _fetch(url)
                except (ValueError, httpx.HTTPError) as exc:
                    results.append(page_error(url, f'Could not read the page: {exc}'))
                    continue
                if kind in ('text/plain', 'application/json'):
                    title, content = '', text.strip()
                else:
                    parser = _Readable()
                    parser.feed(text)
                    parser.close()
                    title, content = parser.title.strip(), parser.text()
                if not content:
                    results.append(page_error(url, 'The page has no readable text (it may need JavaScript).'))
                    continue
                results.append(document(url, title, content, source_url=final_url))
            return results
        return run_extract(self.NAME, logger, urls, body)

    def get_setup_schema(self) -> dict[str, Any]:
        return setup_schema('Local page extract', 'free · local', 'Reads pages from this machine. No API key, no third-party service. No JavaScript.')
