#!/usr/bin/env python3
"""Compile gettext PO catalogs into browser-readable JSON."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOCALES_PATH = Path("locales/locales.json")
OUTPUT_DIR = Path("static/locales")


def _decode_po_string(token: str) -> str:
    try:
        value = ast.literal_eval(token)
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"Invalid PO string literal: {token}") from exc
    if not isinstance(value, str):
        raise ValueError(f"Invalid PO string literal: {token}")
    return value


def parse_po(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    current: dict[str, list[str]] | None = None
    active: str | None = None
    fuzzy = False

    def flush() -> None:
        nonlocal current, fuzzy
        if not current:
            return
        msgid = "".join(current["msgid"])
        msgstr = "".join(current["msgstr"])
        if msgid and msgstr and not fuzzy:
            entries[msgid] = msgstr
        current = None
        fuzzy = False

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            active = None
            continue
        if line.startswith("#"):
            if line.startswith("#,") and "fuzzy" in line:
                fuzzy = True
            continue
        if line.startswith("msgid "):
            flush()
            current = {"msgid": [_decode_po_string(line[6:].strip())], "msgstr": []}
            active = "msgid"
            continue
        if line.startswith("msgstr "):
            if current is None:
                raise ValueError(f"msgstr before msgid in {path}")
            current["msgstr"] = [_decode_po_string(line[7:].strip())]
            active = "msgstr"
            continue
        if line.startswith('"') and active and current is not None:
            current[active].append(_decode_po_string(line))
            continue
        raise ValueError(f"Unsupported PO line in {path}: {raw_line}")

    flush()
    return entries


def load_metadata(path: Path) -> list[dict[str, str]]:
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(metadata, list):
        raise ValueError("locales metadata must be a list")
    for item in metadata:
        for key in ("code", "name", "nativeName", "dir"):
            if key not in item:
                raise ValueError(f"Locale metadata entry missing {key}: {item}")
        if item["dir"] not in {"ltr", "rtl"}:
            raise ValueError(f"Locale {item['code']} has invalid dir {item['dir']}")
    return metadata


def _po_path(root: Path, locale: str) -> Path:
    return root / "locales" / locale.replace("-", "_") / "LC_MESSAGES" / "messages.po"


def compile_all(root: Path = ROOT) -> dict[str, dict[str, object]]:
    metadata = load_metadata(root / LOCALES_PATH)
    compiled: dict[str, dict[str, object]] = {}

    for item in metadata:
        locale = item["code"]
        messages: dict[str, str] = {}
        path = _po_path(root, locale)
        if path.exists():
            messages = parse_po(path)
        elif locale != "en":
            raise FileNotFoundError(path)

        compiled[locale] = {
            "locale": locale,
            "name": item["name"],
            "nativeName": item["nativeName"],
            "dir": item["dir"],
            "messages": messages,
        }

    return compiled


def _json_text(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def write_catalogs(root: Path = ROOT, check: bool = False) -> list[Path]:
    metadata = load_metadata(root / LOCALES_PATH)
    compiled = compile_all(root)
    out_dir = root / OUTPUT_DIR
    written: list[Path] = []
    pending: list[tuple[Path, str]] = [(out_dir / "index.json", _json_text(metadata))]

    for locale, catalog in compiled.items():
        pending.append((out_dir / f"{locale}.json", _json_text(catalog)))

    if check:
        stale = [
            path
            for path, text in pending
            if not path.exists() or path.read_text(encoding="utf-8") != text
        ]
        if stale:
            names = ", ".join(str(path.relative_to(root)) for path in stale)
            raise SystemExit(f"i18n catalogs are stale: {names}")
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    for path, text in pending:
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated JSON is stale")
    args = parser.parse_args()

    written = write_catalogs(ROOT, check=args.check)
    if not args.check:
        for path in written:
            print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
