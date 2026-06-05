"""Import SKILL.md bundles from public GitHub (or skills.sh → GitHub) URLs."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import httpx

from src.url_safety import check_outbound_url

logger = logging.getLogger(__name__)

MAX_FILES = 64
MAX_TOTAL_BYTES = 2_000_000
MAX_FILE_BYTES = 400_000
ALLOWED_SUFFIXES = (
    ".md", ".txt", ".json", ".yaml", ".yml", ".py", ".sh", ".toml",
    ".js", ".ts", ".css", ".html", ".xml", ".csv",
)
TEXT_NAMES = {"skill.md", "license", "license.md", "readme.md"}
_GITHUB_HOSTS = frozenset({
    "github.com", "www.github.com", "api.github.com", "raw.githubusercontent.com",
})


def _github_host(url: str) -> str:
    return (urlparse(str(url)).hostname or "").lower()


def _assert_github_url(url: str, *, context: str = "URL") -> None:
    host = _github_host(url)
    if host not in _GITHUB_HOSTS:
        raise SkillImportError(
            f"{context} must stay on GitHub (got {host or 'unknown host'})"
        )


@dataclass
class ResolvedSource:
    owner: str
    repo: str
    ref: str
    path: str  # directory or file path inside repo (no leading slash)


class SkillImportError(ValueError):
    pass


def _safe_relpath(rel: str) -> str:
    rel = (rel or "").replace("\\", "/").strip().lstrip("/")
    if not rel or rel.startswith("..") or "/../" in f"/{rel}/":
        raise SkillImportError(f"unsafe path: {rel!r}")
    parts = [p for p in rel.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise SkillImportError(f"unsafe path: {rel!r}")
    return "/".join(parts)


def _is_text_file(name: str) -> bool:
    low = name.lower()
    if low in TEXT_NAMES:
        return True
    return any(low.endswith(s) for s in ALLOWED_SUFFIXES)


def parse_skill_source(url: str) -> ResolvedSource:
    """Normalize skills.sh / GitHub web URLs into owner/repo/ref/path."""
    raw = (url or "").strip()
    if not raw:
        raise SkillImportError("URL is required")

    # skills.sh often links to GitHub; try to unwrap ?url= or redirect target later.
    if "skills.sh" in raw and "github.com" not in raw:
        ok, reason = check_outbound_url(raw)
        if not ok:
            raise SkillImportError(reason)
        with httpx.Client(follow_redirects=True, timeout=20.0) as client:
            r = client.get(raw)
            if r.status_code >= 400:
                raise _github_response_error(r)
            final = str(r.url)
            _assert_github_url(final, context="redirect target")
            # Page may embed a github link; prefer final URL if redirected.
            if "github.com" in final:
                raw = final
            else:
                m = re.search(r"https?://github\.com/[^\s\"')]+", r.text or "")
                if m:
                    raw = m.group(0).rstrip(".,)")

    parsed = urlparse(raw)
    host = _github_host(raw)
    if host not in _GITHUB_HOSTS:
        raise SkillImportError(
            "Only GitHub URLs are supported (https://github.com/... or raw.githubusercontent.com/...)"
        )

    if host == "raw.githubusercontent.com":
        # /owner/repo/ref/path/to/file
        bits = [p for p in parsed.path.split("/") if p]
        if len(bits) < 4:
            raise SkillImportError("Invalid raw GitHub URL")
        owner, repo, ref = bits[0], bits[1], bits[2]
        path = "/".join(bits[3:])
        return ResolvedSource(owner=owner, repo=repo, ref=ref, path=path)

    bits = [p for p in parsed.path.split("/") if p]
    if len(bits) < 2:
        raise SkillImportError("Invalid GitHub URL")
    owner, repo = bits[0], bits[1]
    ref = "main"
    path = ""

    if len(bits) >= 4 and bits[2] in ("tree", "blob"):
        ref = bits[3]
        path = "/".join(bits[4:])
    elif len(bits) == 2:
        path = ""
    else:
        raise SkillImportError("GitHub URL must include /tree/<branch>/... or /blob/<branch>/...")

    return ResolvedSource(owner=owner, repo=repo, ref=ref, path=path)


def _raw_url(src: ResolvedSource, rel_path: str) -> str:
    rel = _safe_relpath(rel_path)
    return f"https://raw.githubusercontent.com/{src.owner}/{src.repo}/{quote(src.ref, safe='')}/{quote(rel, safe='/')}"


def _api_contents_url(src: ResolvedSource, rel_path: str = "") -> str:
    rel = _safe_relpath(rel_path) if rel_path else ""
    base = f"https://api.github.com/repos/{src.owner}/{src.repo}/contents"
    if rel:
        base += f"/{quote(rel, safe='/')}"
    return f"{base}?ref={quote(src.ref, safe='')}"


def _github_response_error(response: httpx.Response) -> SkillImportError:
    """Turn a failed GitHub HTTP response into a user-visible import error."""
    status = response.status_code
    detail = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = str(body.get("message") or "").strip()
    except Exception:
        detail = (response.text or "").strip()[:200]

    low = detail.lower()
    if status == 403 and "rate limit" in low:
        return SkillImportError(
            "GitHub API rate limit exceeded — try again in a bit"
            + (f" ({detail})" if detail else "")
        )
    if status == 404:
        return SkillImportError("path not found on GitHub")
    if detail:
        return SkillImportError(f"GitHub request failed ({status}): {detail}")
    return SkillImportError(f"GitHub request failed ({status})")


def _fetch_bytes(url: str) -> bytes:
    ok, reason = check_outbound_url(url)
    if not ok:
        raise SkillImportError(reason)
    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        r = client.get(url, headers={"Accept": "application/vnd.github+json"})
        if r.status_code >= 400:
            raise _github_response_error(r)
        _assert_github_url(str(r.url), context="redirect target")
        if len(r.content) > MAX_FILE_BYTES:
            raise SkillImportError(f"file too large: {url}")
        return r.content


def _fetch_text(url: str) -> str:
    data = _fetch_bytes(url)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise SkillImportError(f"non-text file: {url}") from e


def _list_github_dir(src: ResolvedSource, rel_dir: str, out: Dict[str, str], *, depth: int = 0) -> None:
    if depth > 4 or len(out) >= MAX_FILES:
        return
    url = _api_contents_url(src, rel_dir)
    ok, reason = check_outbound_url(url)
    if not ok:
        raise SkillImportError(reason)
    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        r = client.get(url, headers={"Accept": "application/vnd.github+json"})
        if r.status_code >= 400:
            raise _github_response_error(r)
        _assert_github_url(str(r.url), context="redirect target")
        entries = r.json()
    if not isinstance(entries, list):
        raise SkillImportError("expected a directory on GitHub")
    total = sum(len(v.encode("utf-8")) for v in out.values())
    for ent in entries:
        if len(out) >= MAX_FILES or total >= MAX_TOTAL_BYTES:
            break
        if not isinstance(ent, dict):
            continue
        name = ent.get("name") or ""
        ent_type = ent.get("type")
        rel = _safe_relpath(f"{rel_dir}/{name}" if rel_dir else name)
        if ent_type == "dir":
            _list_github_dir(src, rel, out, depth=depth + 1)
            total = sum(len(v.encode("utf-8")) for v in out.values())
            continue
        if ent_type != "file" or not _is_text_file(name):
            continue
        dl = ent.get("download_url")
        if not dl:
            continue
        _assert_github_url(dl, context="download URL")
        text = _fetch_text(dl)
        total += len(text.encode("utf-8"))
        if total > MAX_TOTAL_BYTES:
            raise SkillImportError("skill bundle exceeds size limit")
        out[rel] = text


def fetch_skill_bundle(url: str) -> Tuple[Dict[str, str], ResolvedSource]:
    """Download SKILL.md and sibling text assets. Returns relative_path → content."""
    src = parse_skill_source(url)
    files: Dict[str, str] = {}

    path = _safe_relpath(src.path) if src.path else ""
    if path.lower().endswith("skill.md"):
        files[path] = _fetch_text(_raw_url(src, path))
        parent = "/".join(path.split("/")[:-1])
        if parent:
            try:
                _list_github_dir(src, parent, files)
            except SkillImportError:
                pass
        return files, src

    if path:
        try:
            _fetch_text(_raw_url(src, f"{path}/SKILL.md"))
            _list_github_dir(src, path, files)
            return files, src
        except Exception:
            pass
        try:
            text = _fetch_text(_raw_url(src, path))
            if path.lower().endswith(".md"):
                files[path] = text
                return files, src
        except Exception:
            pass
        _list_github_dir(src, path, files)
    else:
        _list_github_dir(src, "", files)

    if not any(p.lower().endswith("skill.md") for p in files):
        # Flat repo root with SKILL.md only
        try:
            files["SKILL.md"] = _fetch_text(_raw_url(src, "SKILL.md"))
        except Exception as e:
            raise SkillImportError(
                "No SKILL.md found — link to a skill folder or SKILL.md on GitHub"
            ) from e
    return files, src


def pick_skill_md(files: Dict[str, str]) -> Tuple[str, str]:
    for rel, content in files.items():
        if rel.lower().endswith("skill.md"):
            return rel, content
    raise SkillImportError("bundle has no SKILL.md")


def default_category_from_source(src: ResolvedSource) -> str:
    return "imported"
