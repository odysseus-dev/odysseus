"""Fetch a page locally and return readable text for ``web_extract``.

Select it explicitly with ``web.extract_backend: odysseus-local-extract``; it is never an autodetect pick for search.

Network safety is Hermes' own, not a second implementation: ``tools.url_safety.create_ssrf_safe_client`` resolves, validates and dials the
IP at connect time (a DNS answer that changes between the check and the connection is still refused), ``is_safe_url`` and
``check_website_access`` run on every hop, and redirects are followed by hand so each target is re-checked. The client ignores proxy and
.netrc environment (a proxy would do the DNS lookup and defeat the connect-time check), keeps no cookies between URLs and sends no
credentials: only a User-Agent and an Accept header. TLS verification stays on. The page text is untrusted data.
"""
from __future__ import annotations

import logging
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from plugins.web._common import BaseWebSearchProvider, document, page_error, run_extract, setup_schema

logger = logging.getLogger(__name__)

MAX_BYTES = 2_000_000          # decompressed bytes read per page
MAX_CHARS = 200_000            # readable text returned per page
MAX_REDIRECTS = 5
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 15
DEADLINE_SECONDS = 30          # wall clock per URL, so a slow trickle cannot hold the call
USER_AGENT = 'Mozilla/5.0 (compatible; Hermes-local-extract/0.2)'
_TEXT_TYPES = ('text/html', 'application/xhtml+xml', 'text/plain', 'application/json')
_SKIP = {'script', 'style', 'noscript', 'svg', 'nav', 'header', 'footer', 'aside', 'form', 'template', 'iframe'}
_BLOCK = {'p', 'div', 'section', 'article', 'main', 'br', 'tr', 'table', 'ul', 'ol', 'blockquote', 'figure', 'details'}
_CONTROL = re.compile('[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f\\u200b-\\u200f\\u2028-\\u202e\\u2060-\\u2064\\ufeff]')


def _is_public_url(url: str) -> bool:
    """Pre-flight check by Hermes' SSRF policy; the connect-time guard in the client is the one that counts."""
    from tools.url_safety import is_safe_url
    return bool(is_safe_url(url))


def _policy_blocked(url: str) -> str | None:
    from tools.website_policy import check_website_access
    blocked = check_website_access(url)
    return ((blocked or {}).get('message') or 'blocked') if blocked else None


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
        elif tag in ('td', 'th'):
            self._parts.append(' | ')
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
        # Drop lines that carry no words (empty list items, bare separators and headings left by hidden or icon-only markup).
        lines = [line for line in raw.split('\n') if not line.strip() or '```' in line or re.search(r'\w', line)]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def _fetch(url: str) -> tuple[str, str, str]:
    """Follow redirects by hand so each hop is re-checked. Returns (final_url, content_type, body)."""
    from tools.url_safety import create_ssrf_safe_client, normalize_url_for_request
    started = time.monotonic()
    current = normalize_url_for_request(url)
    timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
    headers = {'User-Agent': USER_AGENT, 'Accept': 'text/html,text/plain,*/*;q=0.5'}
    # trust_env=False: no proxy or .netrc from the environment. A fresh client per URL: no cookies carried between pages.
    with create_ssrf_safe_client(follow_redirects=False, timeout=timeout, headers=headers, trust_env=False, verify=True) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            if urlparse(current).scheme not in ('http', 'https'):
                raise ValueError('only http and https URLs can be fetched')
            if not _is_public_url(current):
                raise ValueError('Blocked: URL targets a private or internal network address')
            policy = _policy_blocked(current)
            if policy:
                raise ValueError(f'Blocked by the website policy: {policy}')
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
                for chunk in response.iter_bytes():      # decoded bytes: the cap applies after decompression
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError('the page is larger than 2 MB')
                    if time.monotonic() - started > DEADLINE_SECONDS:
                        raise ValueError(f'the page took longer than {DEADLINE_SECONDS} s')
                    chunks.append(chunk)
                body = b''.join(chunks).decode(response.encoding or 'utf-8', errors='replace')
                return current, kind or 'text/html', body
    raise ValueError('too many redirects')


def _clean(text: str) -> str:
    text = _CONTROL.sub('', text)
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS].rstrip() + '\n\n[truncated]'


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
                content, title = _clean(content), _clean(title)[:300]
                if not content:
                    results.append(page_error(url, 'The page has no readable text (it may need JavaScript).'))
                    continue
                results.append(document(url, title, content, source_url=final_url))
            return results
        return run_extract(self.NAME, logger, urls, body)

    def get_setup_schema(self) -> dict[str, Any]:
        return setup_schema('Local page extract', 'free · local', 'Reads pages from this machine. No API key, no third-party service. No JavaScript.')
