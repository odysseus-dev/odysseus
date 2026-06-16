"""Import SKILL.md bundles from public GitHub (or skills.sh → GitHub) URLs."""
from __future__ import annotations

import logging
import os
import re
import shlex
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


def _gh_headers(accept: str = "application/vnd.github+json") -> Dict[str, str]:
    """Headers for GitHub requests. Adds Authorization from GITHUB_TOKEN (or
    GH_TOKEN) when set, lifting the unauthenticated 60 req/hour API limit to
    5,000/hour. Harmless on raw.githubusercontent.com fetches."""
    headers = {"Accept": accept}
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


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


_SKILL_FLAGS = ("--skill", "-s")
# Tokens that are runner/subcommand noise, never a skill source.
_COMMAND_NOISE = {"npx", "npm", "pnpm", "yarn", "bunx", "skills", "add", "use", "exec"}


def parse_skill_command(text: str) -> Tuple[str, List[str]]:
    """Split an ``npx skills add …`` command into (source_token, skill_names).

    A plain URL or ``owner/repo`` shorthand returns it unchanged with no names.
    Skill selectors are read from ``--skill``/``-s`` (each accepts one or more
    space-separated names, matching the skills CLI's variadic flag); ``*`` is
    dropped since it means "all" and has no single-folder mapping here.
    """
    raw = (text or "").strip()
    if not raw:
        raise SkillImportError("URL or command is required")

    has_flags = any(f in raw.split() for f in _SKILL_FLAGS)
    is_command = "skills add" in raw.lower() or "skills use" in raw.lower()
    if not has_flags and not is_command:
        # Bare URL / shorthand — nothing to extract.
        return raw, []

    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.split()

    names: List[str] = []
    source = ""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _SKILL_FLAGS:
            i += 1
            while i < len(tokens) and not tokens[i].startswith("-"):
                val = tokens[i].strip().strip("'\"")
                if val and val != "*":
                    names.append(val)
                i += 1
            continue
        if tok.startswith("-"):  # unknown flag, ignore
            i += 1
            continue
        if tok.split("@", 1)[0].lower() in _COMMAND_NOISE:
            i += 1
            continue
        if not source:
            source = tok
        i += 1

    if not source:
        raise SkillImportError("Could not find a repo or URL in the command")
    return source, names


def _resolve_source_token(token: str) -> ResolvedSource:
    """Resolve a command source token — a GitHub URL or ``owner/repo[@ref][/path]``
    shorthand — into a ResolvedSource."""
    tok = (token or "").strip()
    if not tok:
        raise SkillImportError("missing skill source")
    if "://" in tok or "skills.sh" in tok or tok.startswith("git@"):
        return parse_skill_source(tok)
    if tok.startswith("github.com/") or tok.startswith("www.github.com/"):
        return parse_skill_source("https://" + tok)
    bits = [p for p in tok.split("/") if p]
    if len(bits) < 2:
        raise SkillImportError(
            f"'{tok}' is not a GitHub repo — use owner/repo or a github.com URL"
        )
    owner, repo = bits[0], bits[1]
    ref = "main"
    if "@" in repo:
        repo, ref = repo.split("@", 1)
    path = "/".join(bits[2:])
    return ResolvedSource(owner=owner, repo=repo, ref=ref or "main", path=path)


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
        r = client.get(url, headers=_gh_headers())
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
        r = client.get(url, headers=_gh_headers())
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


def _fetch_bundle_for_source(src: ResolvedSource) -> Dict[str, str]:
    """Download SKILL.md and sibling text assets for a resolved source.
    Returns relative_path → content."""
    files: Dict[str, str] = {}

    path = _safe_relpath(src.path) if src.path else ""
    if path.lower().endswith("skill.md"):
        files[path] = _fetch_text(_raw_url(src, path))
        parent = "/".join(path.split("/")[:-1])
        if parent:
            try:
                _list_github_dir(src, parent, files)
            except Exception:
                pass
        return files

    if path:
        # Folder containing SKILL.md? Grab it via raw first (free), then list
        # the folder for extras best-effort — so a valid folder still imports
        # even when the GitHub API is rate-limited.
        try:
            files[f"{path}/SKILL.md"] = _fetch_text(_raw_url(src, f"{path}/SKILL.md"))
            try:
                _list_github_dir(src, path, files)
            except Exception:
                pass
            return files
        except SkillImportError:
            files.pop(f"{path}/SKILL.md", None)
        try:
            text = _fetch_text(_raw_url(src, path))
            if path.lower().endswith(".md"):
                files[path] = text
                return files
        except Exception:
            pass
        _list_github_dir(src, path, files)
    else:
        _list_github_dir(src, "", files)

    skill_mds = [p for p in files if p.lower().endswith("skill.md")]
    if not skill_mds:
        # Flat repo root with SKILL.md only
        try:
            files["SKILL.md"] = _fetch_text(_raw_url(src, "SKILL.md"))
            skill_mds = ["SKILL.md"]
        except Exception as e:
            raise SkillImportError(
                "No SKILL.md found — link to a skill folder or SKILL.md on GitHub"
            ) from e

    if len(skill_mds) > 1 and not path:
        folders = sorted({p.rsplit("/", 1)[0] or "(root)" for p in skill_mds})
        listed = ", ".join(folders[:12]) + (" …" if len(folders) > 12 else "")
        raise SkillImportError(
            f"This repo contains multiple skills ({listed}). "
            "Add --skill <name> (e.g. paste the whole `npx skills add` command) "
            "or link the specific skill folder on GitHub."
        )
    return files


def _fetch_named_skill(src: ResolvedSource, name: str) -> Dict[str, str]:
    """Resolve a single named skill to its bundle, raw-first (no GitHub API in
    the common case). Falls back to a SKILL.md-only bundle when the folder
    can't be listed (e.g. API rate limit), so it never imports the wrong skill
    and never hard-fails on the limit."""
    safe_name = _safe_relpath(name)
    base = _safe_relpath(src.path) if src.path else ""
    candidates: List[str] = []
    if base:
        candidates += [f"{base}/{safe_name}", f"{base}/skills/{safe_name}"]
        if base.endswith(f"/{safe_name}") or base == safe_name:
            candidates.append(base)
    candidates += [f"skills/{safe_name}", safe_name]

    folder = ""
    skill_md = ""
    for cand in candidates:
        if not cand:
            continue
        try:
            skill_md = _fetch_text(_raw_url(src, f"{cand}/SKILL.md"))
            folder = cand
            break
        except SkillImportError:
            continue

    if not folder:
        raise SkillImportError(
            f"Could not find skill '{name}' in {src.owner}/{src.repo} "
            f"(looked for skills/{safe_name}/SKILL.md). "
            "Check the name, or paste the skill's GitHub folder URL."
        )

    files: Dict[str, str] = {"SKILL.md": skill_md}
    # Best-effort: pull sibling assets (templates/, references/) via the API.
    # Swallow listing/rate-limit failures — SKILL.md alone is a valid bundle.
    try:
        raw_files: Dict[str, str] = {}
        _list_github_dir(src, folder, raw_files)
        prefix = f"{folder}/"
        for rel, content in raw_files.items():
            rerooted = rel[len(prefix):] if rel.startswith(prefix) else rel
            if rerooted:
                files[rerooted] = content
    except Exception:
        pass
    return files


def fetch_skill_bundle(url: str) -> Tuple[Dict[str, str], ResolvedSource]:
    """Download SKILL.md and sibling text assets for a single URL.
    Returns (relative_path → content, source). Kept for callers that import
    exactly one skill from a URL."""
    src = parse_skill_source(url)
    return _fetch_bundle_for_source(src), src


def fetch_skill_bundles(text: str) -> List[Tuple[Dict[str, str], ResolvedSource]]:
    """Resolve a pasted URL or ``npx skills add`` command into one or more
    installable bundles. ``--skill <name>`` selectors are resolved raw-first;
    a bare URL/repo yields a single bundle (and errors helpfully if the repo
    holds multiple skills)."""
    source_token, names = parse_skill_command(text)
    if names:
        src = _resolve_source_token(source_token)
        return [(_fetch_named_skill(src, nm), src) for nm in names]

    # No explicit names: single bundle. URLs (incl. skills.sh) go through the
    # URL parser; bare owner/repo shorthand resolves directly.
    if (
        "://" in source_token
        or "skills.sh" in source_token
        or source_token.startswith(("git@", "github.com/", "www.github.com/"))
    ):
        src = parse_skill_source(source_token)
    else:
        src = _resolve_source_token(source_token)
    return [(_fetch_bundle_for_source(src), src)]


def pick_skill_md(files: Dict[str, str]) -> Tuple[str, str]:
    for rel, content in files.items():
        if rel.lower().endswith("skill.md"):
            return rel, content
    raise SkillImportError("bundle has no SKILL.md")


def default_category_from_source(src: ResolvedSource) -> str:
    return "imported"
