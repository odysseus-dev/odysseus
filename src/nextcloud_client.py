"""Nextcloud WebDAV client (read-only).

Live browsing of a user's Nextcloud files over WebDAV. Every listing and file
read hits the server on demand — there is no local mirror — mirroring how
``src/caldav_sync.py`` talks to a remote CalDAV server.

Design notes:
- The WebDAV root for a user is ``{base_url}/remote.php/dav/files/{username}/``.
  All paths passed in here are *relative to that home* (e.g. ``"Documents"``,
  ``"Photos/cat.jpg"``); ``..`` segments are rejected so a caller can never
  ascend above the user's own DAV home.
- HTTP uses the core ``httpx`` dependency, synchronously. Callers run these
  methods through ``asyncio.to_thread`` so the FastAPI event loop stays free
  (same pattern as CalDAV).
- The multistatus PROPFIND body is parsed with ``defusedxml`` because it is
  untrusted server output; parsing lives in pure functions (``_parse_propfind``)
  so it can be unit-tested with fixtures and no network.
- URLs are validated through the shared SSRF guard ``src.url_safety.check_outbound_url``
  before any request. Link-local / multicast / reserved / unspecified targets
  are always rejected (cloud metadata SSRF vector); private/loopback targets are
  allowed by default because self-hosted Nextcloud on a LAN/Tailscale is a
  normal setup, and locked down with ``ODYSSEUS_BLOCK_PRIVATE_NEXTCLOUD=true``
  for multi-tenant deployments.
"""

import logging
import os
from email.utils import parsedate_to_datetime
from typing import Callable, List, Optional, Tuple
from urllib.parse import quote, unquote, urlparse, urlunsplit

import httpx
from defusedxml import ElementTree as ET

from src.constants import (
    NEXTCLOUD_DAV_PATH,
    NEXTCLOUD_REQUEST_TIMEOUT,
)
from src.url_safety import check_outbound_url

logger = logging.getLogger(__name__)

_PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:">'
    '<d:prop>'
    '<d:resourcetype/>'
    '<d:getcontentlength/>'
    '<d:getcontenttype/>'
    '<d:getlastmodified/>'
    '<d:displayname/>'
    '</d:prop>'
    '</d:propfind>'
)

_NS = {"d": "DAV:"}


class NextcloudError(Exception):
    """Raised for WebDAV/transport failures. Carries the HTTP status when known."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _block_private_nextcloud() -> bool:
    return os.getenv("ODYSSEUS_BLOCK_PRIVATE_NEXTCLOUD", "false").lower() == "true"


def validate_nextcloud_url(
    raw_url: str,
    *,
    resolver: Optional[Callable[[str], List[str]]] = None,
) -> str:
    """Validate and normalize a user-provided Nextcloud base URL.

    Rejects non-HTTP(S) schemes, credentials embedded in the URL, and any host
    the shared SSRF guard disallows. ``resolver`` is injectable for tests so
    they never touch real DNS.
    """
    url = (raw_url if isinstance(raw_url, str) else "").strip()
    if not url:
        raise ValueError("Nextcloud URL is required")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Nextcloud URL must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("Nextcloud URL must include a host")
    if parsed.username or parsed.password:
        raise ValueError("Put Nextcloud credentials in the username/password fields, not the URL")
    if parsed.fragment:
        raise ValueError("Nextcloud URL fragments are not allowed")
    try:
        parsed.port
    except ValueError:
        raise ValueError("Nextcloud URL has an invalid port")
    ok, reason = check_outbound_url(url, block_private=_block_private_nextcloud(), resolver=resolver)
    if not ok:
        raise ValueError(f"Nextcloud URL rejected: {reason}")
    # Drop any fragment and trailing slash; keep scheme/host/port/path/query as entered.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")).rstrip("/")


def _safe_relative_path(path: str) -> str:
    """Normalize a path relative to the user's Nextcloud DAV home.

    Returns a slash-joined rel path with no leading/trailing slash. ``..`` is
    rejected so a caller can never target a sibling outside the user's home.
    """
    parts: List[str] = []
    for seg in (path or "").split("/"):
        seg = seg.strip()
        if seg in ("", "."):
            continue
        if seg == "..":
            raise ValueError("path may not ascend above the Nextcloud home ('..' is not allowed)")
        parts.append(seg)
    return "/".join(parts)


def _href_rel(href: str, username: str) -> Optional[str]:
    """Map a multistatus ``<d:href>`` to a path relative to the user's DAV home.

    hrefs are absolute server paths like ``/remote.php/dav/files/alice/Docs/f.txt``
    (URL-encoded). Returns the decoded rel path (e.g. ``Docs/f.txt``), or None
    when the href is not under this user's home (a server should not return
    cross-user hrefs, but we never trust that blindly).
    """
    if not href:
        return None
    raw = href.strip()
    if raw.startswith(("http://", "https://")):
        raw = urlparse(raw).path
    raw = unquote(raw)
    prefix = f"{NEXTCLOUD_DAV_PATH}/{username}/"
    if raw.startswith(prefix):
        return raw[len(prefix):].strip("/")
    if raw.rstrip("/") == f"{NEXTCLOUD_DAV_PATH}/{username}":
        return ""
    return None


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _parse_propfind(xml_text: str, username: str, requested_rel: str) -> List[dict]:
    """Parse a Depth:1 PROPFIND multistatus body into child entries.

    The collection itself is returned by the server as the first response; it is
    detected by matching its rel to ``requested_rel`` and excluded so callers get
    only the directory's children. Each entry: name, path (rel), is_dir, size,
    content_type, modified (ISO-8601 or None).
    """
    requested_rel = (requested_rel or "").strip("/")
    root = ET.fromstring(xml_text)
    out: List[dict] = []
    for resp in root.findall("d:response", _NS):
        href_el = resp.find("d:href", _NS)
        rel = _href_rel(_text(href_el), username) if href_el is not None else None
        if rel is None:
            continue
        if rel == requested_rel:
            # The directory being listed itself; skip so only children remain.
            continue
        propstat = resp.find("d:propstat/d:prop", _NS)
        if propstat is None:
            propstat = resp.find(".//d:prop", _NS)
        is_dir = propstat is not None and propstat.find("d:resourcetype/d:collection", _NS) is not None
        name = _text(propstat.find("d:displayname", _NS)) if propstat is not None else ""
        if not name:
            name = rel.rsplit("/", 1)[-1]
        size_raw = _text(propstat.find("d:getcontentlength", _NS)) if propstat is not None else ""
        try:
            size = int(size_raw) if size_raw else None
        except ValueError:
            size = None
        content_type = _text(propstat.find("d:getcontenttype", _NS)) if propstat is not None else ""
        modified_raw = _text(propstat.find("d:getlastmodified", _NS)) if propstat is not None else ""
        modified = _parse_http_date(modified_raw)
        out.append({
            "name": name,
            "path": rel,
            "is_dir": bool(is_dir),
            "size": size,
            "content_type": content_type or None,
            "modified": modified,
        })
    return out


def _parse_http_date(raw: str) -> Optional[str]:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt is None:
            return None
        return dt.astimezone().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


class NextcloudClient:
    """Thin synchronous WebDAV client for a single Nextcloud account."""

    def __init__(
        self,
        base_url: str,
        username: str,
        app_password: str,
        *,
        timeout: int = NEXTCLOUD_REQUEST_TIMEOUT,
        resolver: Optional[Callable[[str], List[str]]] = None,
    ):
        self.base = validate_nextcloud_url(base_url, resolver=resolver)
        self.username = (username or "").strip()
        self.password = app_password or ""
        if not self.username:
            raise ValueError("Nextcloud username is required")
        if not self.password:
            raise ValueError("Nextcloud app password is required")
        self.timeout = timeout
        self._auth = (self.username, self.password)

    def _dav_url(self, path: str) -> str:
        rel = _safe_relative_path(path)
        url = "{base}{root}/{user}".format(
            base=self.base.rstrip("/"),
            root=NEXTCLOUD_DAV_PATH,
            user=quote(self.username, safe=""),
        )
        if rel:
            url += "/" + "/".join(quote(seg, safe="") for seg in rel.split("/"))
        return url

    def _propfind(self, path: str, depth: str) -> str:
        url = self._dav_url(path)
        try:
            r = httpx.request(
                "PROPFIND", url,
                content=_PROPFIND_BODY.encode("utf-8"),
                headers={"Content-Type": "application/xml; charset=utf-8", "Depth": depth},
                auth=self._auth, timeout=self.timeout,
            )
        except httpx.HTTPError as e:
            raise NextcloudError(f"Nextcloud request failed: {e}") from e
        if r.status_code == 401:
            raise NextcloudError("Nextcloud rejected the credentials (401).", status=401)
        if r.status_code == 404:
            raise NextcloudError("That path does not exist on Nextcloud (404).", status=404)
        if r.status_code not in (200, 207):
            raise NextcloudError(f"Nextcloud returned HTTP {r.status_code}.", status=r.status_code)
        return r.text

    def list_dir(self, path: str = "") -> List[dict]:
        """List the immediate children of ``path`` (relative to the user home)."""
        requested_rel = _safe_relative_path(path)
        xml_text = self._propfind(requested_rel, "1")
        return _parse_propfind(xml_text, self.username, requested_rel)

    def ping(self) -> bool:
        """Lightweight auth + reachability check.

        Issues a Depth:0 PROPFIND on the user's DAV home — enough to confirm the
        credentials work (a bad app password comes back 401) and the instance is
        reachable, without pulling a full listing. Raises NextcloudError on failure.
        """
        self._propfind("", "0")
        return True

    def put_file(self, path: str, content: bytes, content_type: Optional[str] = None) -> None:
        """Write a file's bytes back to Nextcloud (WebDAV PUT).

        Used to mirror edits made in the built-in editor back to the remote file.
        Raises NextcloudError on auth/transport failure.
        """
        url = self._dav_url(path)
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        try:
            r = httpx.put(url, content=content, headers=headers, auth=self._auth, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise NextcloudError(f"Nextcloud request failed: {e}") from e
        if r.status_code in (200, 201, 204):
            return
        if r.status_code == 401:
            raise NextcloudError("Nextcloud rejected the credentials (401).", status=401)
        if r.status_code == 404:
            raise NextcloudError("The parent folder does not exist on Nextcloud (404).", status=404)
        raise NextcloudError(f"Nextcloud returned HTTP {r.status_code}.", status=r.status_code)


    def stat(self, path: str) -> dict:
        """Return a single entry describing ``path`` (the self response of a Depth:0 PROPFIND)."""
        requested_rel = _safe_relative_path(path)
        xml_text = self._propfind(requested_rel, "0")
        entries = _parse_propfind(xml_text, self.username, requested_rel)
        # Depth:0 returns only the resource itself, which list parsing excludes as
        # the "self"; reconstruct it from the single response so callers get one row.
        single = _parse_single_propfind(xml_text, self.username, requested_rel)
        if single is None:
            raise NextcloudError("Nextcloud returned no resource for that path.", status=404)
        return single

    def get_file(self, path: str, max_bytes: Optional[int] = None) -> Tuple[bytes, Optional[str]]:
        """Download a file. Returns ``(content_bytes, content_type)``.

        ``max_bytes`` caps how much is read into memory; a larger remote file
        raises NextcloudError so a caller never streams multi-GB into RAM. It is
        positional (not keyword-only) because call sites invoke it through
        ``asyncio.to_thread(client.get_file, path, max_bytes)``.
        """
        url = self._dav_url(path)
        try:
            with httpx.stream("GET", url, auth=self._auth, timeout=self.timeout, follow_redirects=True) as r:
                if r.status_code == 401:
                    raise NextcloudError("Nextcloud rejected the credentials (401).", status=401)
                if r.status_code == 404:
                    raise NextcloudError("That file does not exist on Nextcloud (404).", status=404)
                if r.status_code >= 400:
                    raise NextcloudError(f"Nextcloud returned HTTP {r.status_code}.", status=r.status_code)
                content_type = r.headers.get("content-type")
                length = r.headers.get("content-length")
                if max_bytes is not None and length and length.isdigit() and int(length) > max_bytes:
                    raise NextcloudError(
                        f"File is too large to read ({int(length)} > {max_bytes} bytes).", status=413
                    )
                chunks: List[bytes] = []
                read = 0
                for chunk in r.iter_bytes():
                    read += len(chunk)
                    if max_bytes is not None and read > max_bytes:
                        raise NextcloudError(
                            f"File exceeded the read limit ({max_bytes} bytes).", status=413
                        )
                    chunks.append(chunk)
                return b"".join(chunks), content_type
        except httpx.HTTPError as e:
            raise NextcloudError(f"Nextcloud request failed: {e}") from e


def _parse_single_propfind(xml_text: str, username: str, requested_rel: str) -> Optional[dict]:
    """Parse a Depth:0 PROPFIND into the single requested resource (the 'self')."""
    requested_rel = (requested_rel or "").strip("/")
    root = ET.fromstring(xml_text)
    for resp in root.findall("d:response", _NS):
        href_el = resp.find("d:href", _NS)
        rel = _href_rel(_text(href_el), username) if href_el is not None else None
        if rel is None:
            continue
        # Depth:0: the resource itself. Accept an empty rel (root) or a rel that
        # matches/ends-with the requested path (servers sometimes echo a
        # trailing slash).
        cand = rel.rstrip("/")
        if cand == requested_rel or rel == "":
            propstat = resp.find("d:propstat/d:prop", _NS)
            if propstat is None:
                propstat = resp.find(".//d:prop", _NS)
            is_dir = propstat is not None and propstat.find("d:resourcetype/d:collection", _NS) is not None
            name = _text(propstat.find("d:displayname", _NS)) if propstat is not None else ""
            if not name:
                name = requested_rel.rsplit("/", 1)[-1] or "/"
            size_raw = _text(propstat.find("d:getcontentlength", _NS)) if propstat is not None else ""
            try:
                size = int(size_raw) if size_raw else None
            except ValueError:
                size = None
            content_type = _text(propstat.find("d:getcontenttype", _NS)) if propstat is not None else ""
            modified_raw = _text(propstat.find("d:getlastmodified", _NS)) if propstat is not None else ""
            return {
                "name": name,
                "path": requested_rel,
                "is_dir": bool(is_dir),
                "size": size,
                "content_type": content_type or None,
                "modified": _parse_http_date(modified_raw),
            }
    return None
