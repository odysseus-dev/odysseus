#!/usr/bin/env python
"""Compile the gettext PO catalogs into the runtime source-keyed JSON.

This is the **normal / build** direction: translators edit ``locales/*.po`` (the
source of record) and this regenerates ``static/locales/<code>.json`` — the exact
flat ``{English: translation}`` catalogs the browser runtime already loads — plus
``index.json``. The runtime is untouched; only its input data is regenerated, so
the output is byte-for-byte what the app shipped before.

``msgctxt`` entries become the JSON ``_overrides`` map (keyed by the dotted UI
key); empty ``msgstr`` entries are skipped so untranslated strings fall back to
English. ``en.json`` stays the canonical English source and is not rewritten.

Usage:
    python scripts/i18n/po_to_json.py --po-dir locales --out static/locales
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import po  # noqa: E402
from build_locale import LANG_META, meta_for, write_json  # noqa: E402


def meta_for_code(code: str, entries) -> dict:
    if code in LANG_META:
        return meta_for(code)
    # New language not in LANG_META: recover from the PO header X- fields.
    hdr = next((po.parse_header(e) for e in entries if e.is_header), {})
    return {
        "code": code,
        "name": hdr.get("X-Language-Name", code.upper()),
        "nativeName": hdr.get("X-Native-Name", code),
        "dir": hdr.get("X-Direction", "ltr"),
    }


def compile_po(path: str):
    code = os.path.splitext(os.path.basename(path))[0]
    with open(path, encoding="utf-8-sig") as f:  # tolerate a BOM from translator tooling
        entries = po.parse(f.read())
    top, overrides = {}, {}
    for e in entries:
        # Skip header, untranslated, and fuzzy (unconfirmed) entries — matching
        # msgfmt's default, so an msgmerge guess never ships until a translator
        # confirms it (the string falls back to English in the meantime).
        if e.is_header or not e.msgstr or "fuzzy" in e.flags:
            continue
        if e.msgctxt is not None:
            overrides[e.msgctxt] = e.msgstr
        else:
            top[e.msgid] = e.msgstr
    catalog = {"_meta": meta_for_code(code, entries)}
    if overrides:
        catalog["_overrides"] = dict(sorted(overrides.items()))
    for k in sorted(top):
        catalog[k] = top[k]
    return code, catalog


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--po-dir", default="locales")
    ap.add_argument("--out", default="static/locales")
    ap.add_argument("--base", default="en", help="source language (kept canonical, not rewritten)")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    codes, metas = [], []
    for path in sorted(glob.glob(os.path.join(args.po_dir, "*.po"))):
        code, catalog = compile_po(path)
        write_json(os.path.join(args.out, f"{code}.json"), catalog)
        codes.append(code)
        metas.append(catalog["_meta"])
        n = sum(1 for k in catalog if not k.startswith("_"))
        print(f"{code}.json: {n} strings"
              + (f", {len(catalog['_overrides'])} overrides" if "_overrides" in catalog else ""))

    registry = {
        "default": args.base,
        "fallback": args.base,
        "locales": [meta_for(args.base)] + metas,
    }
    write_json(os.path.join(args.out, "index.json"), registry)
    print(f"index.json: {', '.join([args.base] + codes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
