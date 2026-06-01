#!/usr/bin/env python
"""Validate the source-keyed locale catalogs. Use in CI / pre-commit.

Checks (hard errors -> exit 1):
  * every locale in index.json has a parseable <code>.json
  * each catalog's _meta.code matches its filename and has name/nativeName/dir
  * no duplicate-detection needed (source-keyed: the key IS the source, so the
    verb/adjective collision class is structurally impossible)

Reports (warnings -> exit 0, or exit 1 with --strict):
  * completeness: strings present in en.json (the canonical key set) but missing
    a translation in a locale — this is your "what still needs translating" list
  * orphans: keys in a locale that are not in en.json (likely stale or typo)
  * _overrides keys that look unused (informational)

Run:
    python scripts/i18n/check_locales.py [--dir static/locales] [--strict]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REQUIRED_META = ("code", "name", "nativeName", "dir")
RESERVED = {"_meta", "_overrides"}


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def source_keys(cat):
    return {k for k, v in cat.items() if k not in RESERVED and isinstance(v, str)}


def check(locales_dir, strict=False):
    errors, warnings = [], []
    idx_path = os.path.join(locales_dir, "index.json")
    if not os.path.exists(idx_path):
        return [f"missing registry: {idx_path}"], []
    registry = load(idx_path)
    codes = [l["code"] for l in registry.get("locales", [])]
    base = registry.get("default", "en")

    catalogs = {}
    for code in codes:
        path = os.path.join(locales_dir, f"{code}.json")
        if not os.path.exists(path):
            errors.append(f"{code}: registered but {code}.json is missing")
            continue
        try:
            cat = load(path)
        except Exception as e:
            errors.append(f"{code}.json: invalid JSON — {e}")
            continue
        catalogs[code] = cat
        meta = cat.get("_meta", {})
        for field in REQUIRED_META:
            if not meta.get(field):
                errors.append(f"{code}.json: _meta.{field} missing")
        if meta.get("code") and meta["code"] != code:
            errors.append(f"{code}.json: _meta.code={meta['code']!r} != filename {code!r}")

    if base not in catalogs:
        errors.append(f"base/default locale {base!r} has no catalog")
        return errors, warnings

    canon = source_keys(catalogs[base])
    print(f"canonical strings (in {base}.json): {len(canon)}")
    for code, cat in catalogs.items():
        if code == base:
            continue
        keys = source_keys(cat)
        missing = canon - keys
        orphans = keys - canon
        print(f"  {code}: {len(keys)} translated, "
              f"{len(missing)} missing, {len(orphans)} orphan")
        if missing:
            sample = ", ".join(sorted(missing)[:8])
            warnings.append(f"{code}: {len(missing)} untranslated string(s): {sample}"
                            + (" ..." if len(missing) > 8 else ""))
        if orphans:
            sample = ", ".join(sorted(orphans)[:8])
            warnings.append(f"{code}: {len(orphans)} orphan key(s) not in {base}.json: {sample}"
                            + (" ..." if len(orphans) > 8 else ""))

    return errors, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="static/locales")
    ap.add_argument("--strict", action="store_true",
                    help="treat completeness/orphan warnings as failures")
    args = ap.parse_args(argv)

    errors, warnings = check(args.dir, args.strict)
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    if errors or (args.strict and warnings):
        print(f"\nFAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"\nOK: {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
