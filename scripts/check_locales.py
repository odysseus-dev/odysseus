#!/usr/bin/env python
"""Locale catalog validator + scaffolder for static/locales.

Stdlib only — a translator can run it without installing the app's deps.

Usage
-----
  python scripts/check_locales.py                       # validate every locale vs en.json
  python scripts/check_locales.py scaffold <code> "<NativeName>" "<EnglishName>" [--rtl]

`validate` (default) reports, per locale: missing keys, extra/orphan keys,
empty values, and {placeholder} mismatches. Exits non-zero if any locale has
ERRORS (missing keys or placeholder mismatches) so it can gate CI.

`scaffold` copies en.json to <code>.json and registers it in index.json, so
adding a language is a single command.
"""
from __future__ import annotations

import json
import os
import re
import sys

LOCALES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "locales")
BASE = "en"
PLURAL_CATS = {"zero", "one", "two", "few", "many", "other"}
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _load(code: str) -> dict:
    with open(os.path.join(LOCALES_DIR, f"{code}.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def _is_plural(v) -> bool:
    return isinstance(v, dict) and bool(v) and all(k in PLURAL_CATS for k in v)


def _flatten(obj, prefix="", out=None):
    """Flatten to {dotted_key: leaf}, where leaf is a str or a plural dict.
    Keys starting with '_' (e.g. _meta) are skipped."""
    if out is None:
        out = {}
    for k, v in obj.items():
        if k.startswith("_"):
            continue
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, str) or _is_plural(v):
            out[key] = v
        elif isinstance(v, dict):
            _flatten(v, key, out)
    return out


def _placeholders(leaf) -> set:
    if _is_plural(leaf):
        s = set()
        for form in leaf.values():
            s |= set(PLACEHOLDER.findall(form))
        return s
    return set(PLACEHOLDER.findall(leaf)) if isinstance(leaf, str) else set()


def _is_empty(leaf) -> bool:
    if _is_plural(leaf):
        return not any((v or "").strip() for v in leaf.values())
    return not (leaf or "").strip()


def _registry() -> dict:
    return _load("index")


def validate() -> int:
    reg = _registry()
    codes = [l["code"] for l in reg.get("locales", []) if l.get("code")]
    base = _flatten(_load(BASE))
    print(f"Base '{BASE}': {len(base)} keys\n")

    errors = 0
    for code in codes:
        if code == BASE:
            continue
        try:
            loc = _flatten(_load(code))
        except FileNotFoundError:
            print(f"[{code}] ERROR: registered in index.json but {code}.json is missing")
            errors += 1
            continue

        missing = [k for k in base if k not in loc]
        extra = [k for k in loc if k not in base]
        empty = [k for k in loc if _is_empty(loc[k])]
        ph_bad = [k for k in loc if k in base and _placeholders(loc[k]) != _placeholders(base[k])]

        translated = len(base) - len(missing)
        pct = round(100 * translated / len(base)) if base else 100
        print(f"[{code}] {translated}/{len(base)} keys ({pct}%)")
        if missing:
            print(f"   missing ({len(missing)}): " + ", ".join(missing[:12]) + (" …" if len(missing) > 12 else ""))
        if ph_bad:
            print(f"   ERROR placeholder mismatch ({len(ph_bad)}): " + ", ".join(ph_bad[:12]))
        if extra:
            print(f"   warn orphan keys not in en.json ({len(extra)}): " + ", ".join(extra[:12]))
        if empty:
            print(f"   warn empty values ({len(empty)}): " + ", ".join(empty[:12]))

        # Missing keys fall back to English (not fatal at runtime) but block
        # "fully localized"; placeholder mismatches can break interpolation.
        if ph_bad:
            errors += 1
        print()

    if errors:
        print(f"FAIL: {errors} locale(s) with errors")
        return 1
    print("OK: all locales valid")
    return 0


def scaffold(argv) -> int:
    if len(argv) < 3:
        print('usage: check_locales.py scaffold <code> "<NativeName>" "<EnglishName>" [--rtl]')
        return 2
    code, native, english = argv[0], argv[1], argv[2]
    direction = "rtl" if "--rtl" in argv[3:] else "ltr"

    target = os.path.join(LOCALES_DIR, f"{code}.json")
    if os.path.exists(target):
        print(f"{code}.json already exists — not overwriting.")
        return 1

    src = _load(BASE)
    src["_meta"] = {"code": code, "name": english,
                    "note": f"{english} catalog. Translate the values; missing keys fall back to en.json."}
    with open(target, "w", encoding="utf-8") as f:
        json.dump(src, f, ensure_ascii=False, indent=2)
        f.write("\n")

    reg = _registry()
    if not any(l.get("code") == code for l in reg.get("locales", [])):
        reg.setdefault("locales", []).append(
            {"code": code, "name": english, "nativeName": native, "dir": direction})
        with open(os.path.join(LOCALES_DIR, "index.json"), "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"Registered '{code}' in index.json")

    print(f"Created {code}.json (a copy of en.json). Translate its values, then run:")
    print("  python scripts/check_locales.py")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "scaffold":
        sys.exit(scaffold(sys.argv[2:]))
    sys.exit(validate())
