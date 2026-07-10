#!/usr/bin/env python3
"""Import skills.sh leaderboard entries into Odysseus data/skills.

This is intentionally a local/admin script rather than a web route: bulk
importing third-party skills can clone many repositories and should stay an
explicit maintenance action.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.memory.skill_importer import SkillImportError  # noqa: E402
from services.memory.skills import SkillsManager  # noqa: E402


PUBLIC_SKILLS_API = "https://www.skills.sh/api/skills"
DEFAULT_SKILLS_CLI_VERSION = "1.5.12"


def _npx_executable() -> str:
    for name in ("npx.cmd", "npx.exe", "npx"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("npx was not found on PATH; install Node.js/npm first")


def _is_well_known_source(source: str) -> bool:
    return "/" not in source and "." in source


def _skill_page_url(source: str, skill_id: str) -> str:
    if _is_well_known_source(source):
        return f"https://skills.sh/site/{source}/{skill_id}"
    return f"https://skills.sh/{source}/{skill_id}"


def _well_known_skill_url(source: str, skill_id: str) -> str:
    return f"https://{source}/.well-known/skills/{skill_id}/SKILL.md"


def _flight_payload(page_html: str) -> str:
    chunks: List[str] = []
    for match in re.finditer(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', page_html, re.S):
        try:
            chunks.append(json.loads('"' + match.group(1) + '"'))
        except json.JSONDecodeError:
            continue
    return "".join(chunks)


def _read_flight_field(payload: str, key: str) -> Optional[str]:
    token = f'{key}":"'
    start = payload.find(token)
    if start < 0:
        return None
    i = start + len(token)
    out: List[str] = []
    escaped = False
    while i < len(payload):
        ch = payload[i]
        if escaped:
            out.append("\\" + ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            break
        else:
            out.append(ch)
        i += 1
    raw = "".join(out)
    try:
        return json.loads('"' + raw + '"')
    except json.JSONDecodeError:
        return raw.replace("\\n", "\n").replace('\\"', '"')


def _read_flight_record(payload: str, record_id: str) -> str:
    token = f"{record_id}:T"
    start = payload.find(token)
    if start < 0:
        return ""
    len_start = start + len(token)
    comma = payload.find(",", len_start)
    if comma < 0:
        return ""
    try:
        length = int(payload[len_start:comma], 16)
    except ValueError:
        return ""
    body_start = comma + 1
    return payload[body_start : body_start + length]


def _skill_page_html(page_html: str) -> str:
    payload = _flight_payload(page_html)
    preview = _read_flight_field(payload, "previewHtml")
    if preview is not None:
        if preview.startswith("$"):
            preview = _read_flight_record(payload, preview[1:])
        rest = ""
        rest_match = re.search(r'restHtml":"\$(\w+)"', payload)
        if rest_match:
            rest = _read_flight_record(payload, rest_match.group(1))
        return preview + rest

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""

    soup = BeautifulSoup(page_html, "html.parser")
    label = soup.find("span", string="SKILL.md")
    if not label or not label.parent:
        return ""
    prose = label.parent.find_next_sibling("div")
    if not prose:
        return ""
    return "".join(str(child) for child in prose.contents)


def _html_to_markdown(fragment: str) -> str:
    try:
        from bs4 import BeautifulSoup, NavigableString, Tag
    except ImportError:
        return ""

    def node_to_markdown(node) -> str:
        if isinstance(node, NavigableString):
            return str(node)
        if not isinstance(node, Tag):
            return ""
        name = node.name.lower()

        if name == "pre":
            return "\n```\n" + node.get_text("\n").strip() + "\n```\n\n"
        if name == "br":
            return "\n"
        if name == "input":
            return ""

        children = "".join(node_to_markdown(child) for child in node.children)
        text = children.strip()

        if name in {"h1", "h2", "h3", "h4"}:
            level = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}[name]
            return "\n" + ("#" * level) + " " + text + "\n\n"
        if name == "p":
            return text + "\n\n" if text else ""
        if name in {"strong", "b"}:
            return f"**{text}**"
        if name in {"em", "i"}:
            return f"*{text}*"
        if name == "code":
            return f"`{text}`"
        if name == "a":
            href = node.get("href") or ""
            return f"[{text}]({href})" if href else text
        if name == "blockquote":
            lines = text.splitlines() or [text]
            return "\n".join(("> " + line).rstrip() for line in lines) + "\n\n"
        if name == "li":
            return "- " + text + "\n" if text else ""
        if name in {"ul", "ol"}:
            return "\n" + children + "\n"
        if name == "tr":
            cells = [cell.get_text(" ", strip=True) for cell in node.find_all(["th", "td"], recursive=False)]
            return "| " + " | ".join(cells) + " |\n" if cells else ""
        if name == "table":
            rows = [node_to_markdown(child) for child in node.children]
            return "\n" + "".join(rows) + "\n"
        return children

    soup = BeautifulSoup(fragment, "html.parser")
    markdown = "".join(node_to_markdown(child) for child in soup.contents)
    markdown = re.sub(r"[ \t]+\n", "\n", markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip() + "\n" if markdown.strip() else ""


def _description_from_markdown(markdown: str, fallback: str) -> str:
    in_code = False
    for line in markdown.splitlines():
        text = line.strip()
        if text.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not text or text.startswith("#") or text.startswith("-"):
            continue
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"[*_`]+", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return text[:240]
    return fallback.replace("-", " ").title()


def _page_skill_markdown(source: str, skill_id: str) -> str:
    page_url = _skill_page_url(source, skill_id)
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        res = client.get(page_url)
        res.raise_for_status()
    fragment = _skill_page_html(res.text)
    body = _html_to_markdown(fragment)
    if not body:
        raise SkillImportError(f"{source}@{skill_id}: could not extract SKILL.md from {page_url}")

    description = _description_from_markdown(body, skill_id)
    return "\n".join([
        "---",
        f"name: {skill_id}",
        f"description: {json.dumps(description)}",
        "version: 1.0.0",
        "---",
        "",
        body,
    ])


def _fetch_leaderboard(view: str, limit: Optional[int]) -> List[dict]:
    out: List[dict] = []
    page = 0
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        while True:
            url = f"{PUBLIC_SKILLS_API}/{view}/{page}"
            res = client.get(url)
            res.raise_for_status()
            data = res.json()
            skills = data.get("skills") if isinstance(data, dict) else None
            if not isinstance(skills, list):
                raise RuntimeError(f"Unexpected skills.sh response from {url}")
            out.extend(s for s in skills if isinstance(s, dict))
            if limit is not None and len(out) >= limit:
                return out[:limit]
            if not data.get("hasMore"):
                return out
            page += 1


def _existing_import_markers(manager: SkillsManager, owner: Optional[str]) -> set[str]:
    markers: set[str] = set()
    for path in manager._iter_skill_files():
        skill = manager._read_skill(path)
        if not skill or skill.owner != owner:
            continue
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        for match in re.finditer(r"Imported from (https://skills\.sh/\S+)", text):
            markers.add(match.group(1).strip())
    return markers


def _read_skill_dir(skill_dir: Path) -> Dict[str, str]:
    files: Dict[str, str] = {}
    for path in skill_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir).as_posix()
        if rel.startswith("../") or "/../" in f"/{rel}/":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        files[rel] = text
    return files


def _import_files(
    manager: SkillsManager,
    files: Dict[str, str],
    *,
    owner: str,
    source_url: str,
    category: str,
    status: str,
    confidence: float,
) -> dict:
    entry = manager.import_bundle_from_files(
        files,
        owner=owner,
        source_url=source_url,
        category=category,
    )
    # Third-party imports should be visible for review before they become part
    # of the actively trusted/published skill index. Update through the manager
    # and then stamp the written file directly, because imported bundles can
    # carry source frontmatter like `status: published`, and duplicate slugs can
    # make a name-based update target the wrong sibling during bulk imports.
    manager.update_skill(
        entry["name"],
        {"status": status, "confidence": confidence, "source": "imported"},
        owner=owner,
    )
    path = entry.get("path")
    if path:
        try:
            _stamp_import_frontmatter(Path(path), status=status, confidence=confidence)
        except OSError:
            pass
    return entry


def _stamp_import_frontmatter(path: Path, *, status: str, confidence: float) -> None:
    text = path.read_text(encoding="utf-8")
    replacements = {
        "status": status,
        "confidence": str(confidence),
        "source": "imported",
    }
    for key, value in replacements.items():
        pattern = rf"(?m)^{re.escape(key)}:\s*.*$"
        line = f"{key}: {value}"
        if re.search(pattern, text):
            text = re.sub(pattern, line, text, count=1)
        elif text.startswith("---\n"):
            text = text.replace("---\n", f"---\n{line}\n", 1)
    path.write_text(text, encoding="utf-8")


def _chunked(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _source_workspace(root: Path, source: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "__", source).strip("._-") or "source"
    path = root / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_skills_add(
    source: str,
    skill_ids: List[str],
    *,
    cwd: Path,
    cli_version: str,
) -> Tuple[bool, str]:
    cmd = [
        _npx_executable(),
        "--yes",
        f"skills@{cli_version}",
        "add",
        f"https://github.com/{source}",
        "-a",
        "codex",
        "--copy",
        "-y",
    ]
    for skill_id in skill_ids:
        cmd.extend(["--skill", skill_id])

    env = os.environ.copy()
    env.setdefault("DISABLE_TELEMETRY", "1")
    env.setdefault("NO_COLOR", "1")
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
    )
    return proc.returncode == 0, proc.stdout


def _import_github_group(
    manager: SkillsManager,
    *,
    source: str,
    skill_ids: List[str],
    owner: str,
    category: str,
    status: str,
    confidence: float,
    cli_version: str,
    workspace: Path,
) -> Tuple[int, List[str]]:
    imported = 0
    errors: List[str] = []
    skills_root = workspace / ".agents" / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    for chunk in _chunked(skill_ids, 40):
        before = {p.name for p in skills_root.iterdir() if p.is_dir()}
        ok, output = _run_skills_add(source, chunk, cwd=workspace, cli_version=cli_version)
        if not ok and len(chunk) > 1:
            for skill_id in chunk:
                sub_count, sub_errors = _import_github_group(
                    manager,
                    source=source,
                    skill_ids=[skill_id],
                    owner=owner,
                    category=category,
                    status=status,
                    confidence=confidence,
                    cli_version=cli_version,
                    workspace=workspace,
                )
                imported += sub_count
                errors.extend(sub_errors)
            continue
        if not ok:
            skill_id = chunk[0]
            try:
                skill_md = _page_skill_markdown(source, skill_id)
                _import_files(
                    manager,
                    {"SKILL.md": skill_md},
                    owner=owner,
                    source_url=_skill_page_url(source, skill_id),
                    category=category,
                    status=status,
                    confidence=confidence,
                )
                imported += 1
            except Exception as exc:
                detail = output.strip().splitlines()[-1:] or ["install failed"]
                errors.append(f"{source}@{skill_id}: {detail}; fallback failed: {exc}")
            continue

        after = {p.name for p in skills_root.iterdir() if p.is_dir()}
        created = sorted(after - before)
        # Some CLI reinstalls can replace an existing temp folder. Fall back to
        # the requested names so the import still happens on fresh workspaces.
        if not created:
            created = [s for s in chunk if (skills_root / s).is_dir()]
        installed_names = set(created) | {s for s in chunk if (skills_root / s).is_dir()}
        for dirname in created:
            skill_dir = skills_root / dirname
            files = _read_skill_dir(skill_dir)
            if not any(p.lower().endswith("skill.md") for p in files):
                errors.append(f"{source}@{dirname}: no SKILL.md after install")
                continue
            source_url = _skill_page_url(source, dirname)
            try:
                _import_files(
                    manager,
                    files,
                    owner=owner,
                    source_url=source_url,
                    category=category,
                    status=status,
                    confidence=confidence,
                )
                imported += 1
            except SkillImportError as exc:
                errors.append(f"{source}@{dirname}: {exc}")
        for skill_id in chunk:
            if skill_id in installed_names:
                continue
            try:
                skill_md = _page_skill_markdown(source, skill_id)
                _import_files(
                    manager,
                    {"SKILL.md": skill_md},
                    owner=owner,
                    source_url=_skill_page_url(source, skill_id),
                    category=category,
                    status=status,
                    confidence=confidence,
                )
                imported += 1
            except Exception as exc:
                errors.append(f"{source}@{skill_id}: not installed by CLI and fallback failed: {exc}")
    return imported, errors


def _import_well_known_skill(
    manager: SkillsManager,
    *,
    source: str,
    skill_id: str,
    owner: str,
    category: str,
    status: str,
    confidence: float,
) -> Tuple[bool, str]:
    url = _well_known_skill_url(source, skill_id)
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            res = client.get(url)
            res.raise_for_status()
            text = res.text
        if "<html" in text[:200].lower() or "name:" not in text[:1000]:
            return False, f"{source}@{skill_id}: {url} did not return SKILL.md"
        _import_files(
            manager,
            {"SKILL.md": text},
            owner=owner,
            source_url=_skill_page_url(source, skill_id),
            category=category,
            status=status,
            confidence=confidence,
        )
        return True, ""
    except Exception as exc:
        return False, f"{source}@{skill_id}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import skills.sh leaderboard skills into Odysseus.",
    )
    parser.add_argument("--view", default="all-time", choices=["all-time", "trending", "hot"])
    parser.add_argument("--limit", type=int, default=200, help="Maximum skills to import.")
    parser.add_argument("--all", action="store_true", help="Import the full public leaderboard.")
    parser.add_argument("--owner", default="admin")
    parser.add_argument("--category", default="skills-sh")
    parser.add_argument("--status", default="draft", choices=["draft", "published"])
    parser.add_argument("--confidence", type=float, default=0.5)
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--skills-cli-version", default=DEFAULT_SKILLS_CLI_VERSION)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    limit = None if args.all else max(1, args.limit)
    skills = _fetch_leaderboard(args.view, limit)
    print(f"Fetched {len(skills)} skills from skills.sh view={args.view}")

    manager = SkillsManager(args.data_dir)
    existing = _existing_import_markers(manager, args.owner)

    pending: List[dict] = []
    skipped = 0
    for item in skills:
        source = str(item.get("source") or "").strip()
        skill_id = str(item.get("skillId") or item.get("name") or "").strip()
        if not source or not skill_id:
            continue
        if _skill_page_url(source, skill_id) in existing:
            skipped += 1
            continue
        pending.append({"source": source, "skill_id": skill_id})

    by_source: dict[str, List[str]] = defaultdict(list)
    for item in pending:
        by_source[item["source"]].append(item["skill_id"])

    print(f"Pending import: {len(pending)} skill(s), {len(by_source)} source(s), skipped existing: {skipped}")
    if args.dry_run:
        for source, skill_ids in sorted(by_source.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:20]:
            print(f"  {source}: {len(skill_ids)}")
        return 0

    imported = 0
    errors: List[str] = []
    with tempfile.TemporaryDirectory(prefix="odysseus-skills-sh-") as tmp:
        workspace = Path(tmp)
        for source, skill_ids in sorted(by_source.items()):
            if _is_well_known_source(source):
                for skill_id in skill_ids:
                    ok, err = _import_well_known_skill(
                        manager,
                        source=source,
                        skill_id=skill_id,
                        owner=args.owner,
                        category=args.category,
                        status=args.status,
                        confidence=args.confidence,
                    )
                    if ok:
                        imported += 1
                    elif err:
                        errors.append(err)
                continue

            count, group_errors = _import_github_group(
                manager,
                source=source,
                skill_ids=skill_ids,
                owner=args.owner,
                category=args.category,
                status=args.status,
                confidence=args.confidence,
                cli_version=args.skills_cli_version,
                workspace=_source_workspace(workspace, source),
            )
            imported += count
            errors.extend(group_errors)
            print(f"{source}: imported {count}/{len(skill_ids)}")

    print(f"Imported {imported} skill(s). Skipped existing {skipped}. Errors {len(errors)}.")
    for err in errors[:25]:
        print(f"ERROR: {err}")
    if len(errors) > 25:
        print(f"... {len(errors) - 25} more error(s)")
    return 0 if imported or not pending else 1


if __name__ == "__main__":
    raise SystemExit(main())
