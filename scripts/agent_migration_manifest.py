#!/usr/bin/env python3
"""Build a neutral agent migration manifest.

This helper is intentionally read-only. It does not import the Odysseus
application package, write to data/, call an LLM, or apply anything. It turns
common agent export shapes into a portable JSON manifest that Odysseus can
preview or import later.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "agent-migration.v1"
TEXT_EXTENSIONS = {
    ".cfg",
    ".conf",
    ".csv",
    ".json",
    ".log",
    ".md",
    ".markdown",
    ".py",
    ".rst",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class InputWarning:
    path: str
    message: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_id(kind: str, source_name: str, *parts: Any) -> str:
    raw = "\x1f".join([kind, source_name, *[str(part) for part in parts]])
    return f"{kind}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_category(value: Any) -> str:
    category = str(value or "fact").strip().lower()
    return category or "fact"


def normalize_memory_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("text", "content", "memory", "value"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def memory_metadata(item: Any, source_path: Path, index: int) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source_path": str(source_path),
        "source_index": index,
    }
    if isinstance(item, dict):
        for key in ("id", "timestamp", "created_at", "updated_at", "source", "tags", "pinned"):
            if key in item:
                metadata[f"source_{key}"] = item.get(key)
    return metadata


def collect_memory_json(path: Path, source_name: str) -> tuple[list[dict[str, Any]], list[InputWarning]]:
    warnings: list[InputWarning] = []
    try:
        payload = read_json(path)
    except Exception as exc:
        return [], [InputWarning(str(path), f"could not read JSON: {exc}")]

    if isinstance(payload, dict):
        for key in ("memories", "memory", "items", "data"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break

    if not isinstance(payload, list):
        return [], [InputWarning(str(path), "expected a JSON list or an object containing a memory list")]

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(payload):
        text = normalize_memory_text(item)
        if not text:
            warnings.append(InputWarning(str(path), f"skipped memory at index {index}: missing text"))
            continue
        digest = sha256_text(text.strip().lower())
        if digest in seen:
            warnings.append(InputWarning(str(path), f"skipped duplicate memory at index {index}"))
            continue
        seen.add(digest)
        category = normalize_category(item.get("category") if isinstance(item, dict) else "fact")
        source = str(item.get("source") or source_name) if isinstance(item, dict) else source_name
        items.append(
            {
                "id": stable_id("memory", source_name, path, index, digest),
                "kind": "memory",
                "text": text,
                "category": category,
                "source": source,
                "metadata": memory_metadata(item, path, index),
            }
        )
    return items, warnings


def parse_skill_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    frontmatter: dict[str, Any] = {}
    for line in text[3:end].strip().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            frontmatter[key] = value
    return frontmatter


def collect_skill_dir(path: Path, source_name: str) -> tuple[list[dict[str, Any]], list[InputWarning]]:
    warnings: list[InputWarning] = []
    if path.is_symlink():
        return [], [InputWarning(str(path), "skills path is a symlink; skipped")]
    if not path.exists():
        return [], [InputWarning(str(path), "skills directory does not exist")]
    if not path.is_dir():
        return [], [InputWarning(str(path), "skills path is not a directory")]

    items: list[dict[str, Any]] = []
    for skill_path in sorted(path.rglob("SKILL.md")):
        if skill_path.is_symlink():
            warnings.append(InputWarning(str(skill_path), "skipped symlinked skill file"))
            continue
        try:
            text = skill_path.read_text(encoding="utf-8")
        except Exception as exc:
            warnings.append(InputWarning(str(skill_path), f"could not read skill: {exc}"))
            continue
        frontmatter = parse_skill_frontmatter(text)
        name = str(frontmatter.get("name") or skill_path.parent.name).strip() or skill_path.parent.name
        items.append(
            {
                "id": stable_id("skill", source_name, skill_path, sha256_text(text)),
                "kind": "skill",
                "name": name,
                "category": str(frontmatter.get("category") or "general"),
                "source": source_name,
                "format": "SKILL.md",
                "content": text,
                "metadata": {
                    "source_path": str(skill_path),
                    "sha256": sha256_text(text),
                    "frontmatter": frontmatter,
                },
            }
        )
    return items, warnings


def looks_textual(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    guessed, _ = mimetypes.guess_type(str(path))
    return bool(guessed and (guessed.startswith("text/") or guessed in {"application/json"}))


def iter_archive_dir(path: Path) -> Iterable[Path | InputWarning]:
    try:
        children = sorted(path.iterdir())
    except Exception as exc:
        yield InputWarning(str(path), f"could not scan archive directory: {exc}")
        return
    for child in children:
        if child.is_symlink():
            yield InputWarning(str(child), "skipped symlinked archive path")
            continue
        if child.is_file():
            yield child
        elif child.is_dir():
            yield from iter_archive_dir(child)


def iter_archive_files(paths: Iterable[Path]) -> Iterable[Path | InputWarning]:
    for path in paths:
        if path.is_symlink():
            yield InputWarning(str(path), "skipped symlinked archive path")
            continue
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from iter_archive_dir(path)


def collect_archive_paths(
    paths: list[Path],
    source_name: str,
    *,
    include_content: bool = False,
    max_bytes: int = 256_000,
) -> tuple[list[dict[str, Any]], list[InputWarning]]:
    warnings: list[InputWarning] = []
    items: list[dict[str, Any]] = []
    existing_paths: list[Path] = []
    for path in paths:
        if path.is_symlink():
            warnings.append(InputWarning(str(path), "archive path is a symlink; skipped"))
            continue
        if not path.exists():
            warnings.append(InputWarning(str(path), "archive path does not exist"))
            continue
        if not path.is_file() and not path.is_dir():
            warnings.append(InputWarning(str(path), "archive path is not a file or directory"))
            continue
        existing_paths.append(path)

    for entry in iter_archive_files(existing_paths):
        if isinstance(entry, InputWarning):
            warnings.append(entry)
            continue
        path = entry
        if not looks_textual(path):
            warnings.append(InputWarning(str(path), "skipped non-text archive file"))
            continue
        try:
            st = path.stat()
        except Exception as exc:
            warnings.append(InputWarning(str(path), f"could not stat archive file: {exc}"))
            continue
        size = st.st_size
        try:
            file_hash = sha256_path(path)
        except Exception as exc:
            warnings.append(InputWarning(str(path), f"could not hash archive file: {exc}"))
            continue
        if include_content and size > max_bytes:
            warnings.append(InputWarning(str(path), f"skipped archive content over {max_bytes} bytes"))
        archive_item: dict[str, Any] = {
            "id": stable_id("archive", source_name, path, file_hash),
            "kind": "archive_document",
            "title": path.name,
            "source": source_name,
            "metadata": {
                "source_path": str(path),
                "size_bytes": size,
                "sha256": file_hash,
            },
        }
        if include_content and size <= max_bytes:
            try:
                archive_item["content"] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                archive_item["content"] = path.read_text(encoding="utf-8", errors="replace")
                archive_item["metadata"]["decoded_with_replacement"] = True
        items.append(archive_item)
    return items, warnings


def build_manifest(args) -> dict[str, Any]:
    warnings: list[InputWarning] = []
    items: list[dict[str, Any]] = []

    for path in args.memory_json:
        collected, got_warnings = collect_memory_json(path, args.source_name)
        items.extend(collected)
        warnings.extend(got_warnings)

    for path in args.skills_dir:
        collected, got_warnings = collect_skill_dir(path, args.source_name)
        items.extend(collected)
        warnings.extend(got_warnings)

    if args.archive:
        collected, got_warnings = collect_archive_paths(
            args.archive,
            args.source_name,
            include_content=args.include_archive_content,
            max_bytes=args.max_archive_bytes,
        )
        items.extend(collected)
        warnings.extend(got_warnings)

    counts: dict[str, int] = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source": {
            "name": args.source_name,
            "kind": args.source_kind,
        },
        "summary": {
            "item_count": len(items),
            "counts_by_kind": counts,
            "warning_count": len(warnings),
        },
        "items": items,
        "warnings": [{"path": warning.path, "message": warning.message} for warning in warnings],
    }


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Build a neutral Odysseus agent migration manifest.")
    parser.add_argument("--source-name", default="agent-export", help="Human-readable source name.")
    parser.add_argument("--source-kind", default="generic", help="Source adapter kind, e.g. generic, openclaw, hermes.")
    parser.add_argument(
        "--memory-json",
        action="append",
        type=Path,
        default=[],
        help="JSON memory export. May be a list, or an object containing memories/items/data.",
    )
    parser.add_argument(
        "--skills-dir",
        action="append",
        type=Path,
        default=[],
        help="Directory containing SKILL.md files. Scanned recursively.",
    )
    parser.add_argument(
        "--archive",
        action="append",
        type=Path,
        default=[],
        help="Text/Markdown/JSON file or directory to preserve as archive documents.",
    )
    parser.add_argument(
        "--include-archive-content",
        action="store_true",
        help="Embed archive document content in the manifest. By default only metadata is included.",
    )
    parser.add_argument(
        "--max-archive-bytes",
        type=int,
        default=256_000,
        help="Maximum bytes to embed per archive file when --include-archive-content is used.",
    )
    parser.add_argument("--output", type=Path, help="Write manifest JSON to this path instead of stdout.")
    parser.add_argument("--compact", action="store_true", help="Write compact JSON without indentation.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = build_manifest(args)
    text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if args.compact else (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
