#!/usr/bin/env python
"""Build source-keyed locale catalogs from the (legacy) dotted-key catalogs.

This is a ONE-TIME / occasional migration + reuse tool. The runtime catalog
format is *source-keyed*: the English string itself is the key, the value is the
translation. That keeps adding strings trivial (no key invention) and lets the
runtime translate the DOM by matching rendered English text.

    en.json : { "_meta": {...}, "<English>": "<English>", ... }   # canonical key set
    ja.json : { "_meta": {...}, "_overrides": {...}, "<English>": "<日本語>", ... }

Input is the dotted-key pair the localization branch already produced
(en.json = English values, <lang>.json = translations under the SAME keys), so
all existing translation work is reused. When the same English string maps to
two or more *different* translations (a genuine context collision), the majority
translation becomes the default and the minority become `_overrides` keyed by
the original dotted key — apply those with `data-i18n="<dotted.key>"` on the few
elements that need them (see static/locales/README.md).

Usage:
    python scripts/i18n/build_locale.py \
        --en  path/to/dotted/en.json \
        --target ja=path/to/dotted/ja.json \
        --out static/locales

After the first build, edit static/locales/*.json directly — you do not need the
dotted catalogs again unless you are importing another bulk translation.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

# code -> (English name, native name, text direction). Extend as languages are added.
LANG_META = {
    "en": ("English", "English", "ltr"),
    "ja": ("Japanese", "日本語", "ltr"),
    "es": ("Spanish", "Español", "ltr"),
    "fr": ("French", "Français", "ltr"),
    "de": ("German", "Deutsch", "ltr"),
    "zh": ("Chinese", "中文", "ltr"),
    "ar": ("Arabic", "العربية", "rtl"),
    "he": ("Hebrew", "עברית", "rtl"),
}

PLURAL_CATS = {"zero", "one", "two", "few", "many", "other"}


def _is_plural(v) -> bool:
    return (
        isinstance(v, dict)
        and v
        and all(k in PLURAL_CATS for k in v)
    )


def flatten(obj, prefix="", out=None):
    """Dotted-key flatten. Plural objects collapse to their 'other' form (best
    effort for the source-keyed model); keys starting with '_' are ignored."""
    if out is None:
        out = {}
    for k, v in obj.items():
        if k.startswith("_"):
            continue
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, str):
            out[key] = v
        elif _is_plural(v):
            out[key] = v.get("other") or next(iter(v.values()))
        elif isinstance(v, dict):
            flatten(v, key, out)
    return out


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def meta_for(code):
    name, native, direction = LANG_META.get(code, (code.upper(), code, "ltr"))
    return {"code": code, "name": name, "nativeName": native, "dir": direction}


def build_target(en_flat, tgt_flat, code):
    """Return (catalog_dict, report_dict) for one target language."""
    # english value -> { translation -> [dotted keys] }
    groups = defaultdict(lambda: defaultdict(list))
    missing = []
    for key, en_val in en_flat.items():
        tv = tgt_flat.get(key)
        if tv is None or tv == "":
            missing.append(key)
            continue
        groups[en_val][tv].append(key)

    source_map = {}
    overrides = {}
    collisions = []
    for en_val, by_tr in groups.items():
        if len(by_tr) == 1:
            source_map[en_val] = next(iter(by_tr))
            continue
        # Collision: pick the translation backing the most keys (tie -> sorted).
        ranked = sorted(by_tr.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        default_tr, _ = ranked[0]
        source_map[en_val] = default_tr
        for tr, keys in ranked[1:]:
            for dk in keys:
                overrides[dk] = tr
        collisions.append(
            {"en": en_val, "default": default_tr,
             "overrides": {dk: tr for tr, keys in ranked[1:] for dk in keys}}
        )

    catalog = {"_meta": meta_for(code)}
    if overrides:
        catalog["_overrides"] = dict(sorted(overrides.items()))
    for en_val in sorted(source_map):
        catalog[en_val] = source_map[en_val]

    report = {
        "code": code,
        "entries": len(source_map),
        "missing_translations": len(missing),
        "collisions": collisions,
        "override_elements": len(overrides),
    }
    return catalog, report


def build_en(en_flat):
    """Canonical source catalog: every distinct English string keyed to itself."""
    catalog = {"_meta": meta_for("en")}
    for v in sorted(set(en_flat.values())):
        catalog[v] = v
    return catalog


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--en", required=True, help="dotted-key English catalog (source values)")
    ap.add_argument("--target", action="append", default=[],
                    metavar="code=path", help="dotted-key translation, e.g. ja=path/ja.json")
    ap.add_argument("--out", default="static/locales", help="output dir for source-keyed catalogs")
    args = ap.parse_args(argv)

    en_flat = flatten(load(args.en))
    os.makedirs(args.out, exist_ok=True)

    write_json(os.path.join(args.out, "en.json"), build_en(en_flat))
    print(f"en.json: {len(set(en_flat.values()))} canonical source strings")

    codes = ["en"]
    for spec in args.target:
        if "=" not in spec:
            ap.error(f"--target must be code=path, got {spec!r}")
        code, path = spec.split("=", 1)
        tgt_flat = flatten(load(path))
        catalog, report = build_target(en_flat, tgt_flat, code)
        write_json(os.path.join(args.out, f"{code}.json"), catalog)
        codes.append(code)
        print(f"{code}.json: {report['entries']} strings, "
              f"{report['missing_translations']} untranslated, "
              f"{len(report['collisions'])} collisions "
              f"-> {report['override_elements']} override element(s)")
        for c in report["collisions"]:
            print(f"    collision {c['en']!r}: default={c['default']!r} "
                  f"overrides={c['overrides']}")

    # registry
    registry = {
        "default": "en",
        "fallback": "en",
        "locales": [meta_for(c) for c in codes],
    }
    write_json(os.path.join(args.out, "index.json"), registry)
    print(f"index.json: {', '.join(codes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
