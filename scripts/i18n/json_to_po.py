#!/usr/bin/env python
"""Export the source-keyed JSON catalogs to gettext PO (translator hand-off).

This is the **bootstrap / one-time** direction: it turns the existing
``static/locales/*.json`` runtime catalogs into a central ``locales/`` directory
of ``.po`` files plus a ``messages.pot`` template, so translators can work in the
gettext ecosystem (Poedit, Weblate, Crowdin, …) with Translation Memory,
glossaries, MT and spell-check. After this, ``locales/*.po`` is the source of
record for translations and :mod:`po_to_json` compiles it back to the runtime
JSON — you do not need to run this again unless you are re-importing bulk JSON.

The English catalog (``en.json``) stays the canonical source of *English* strings
and becomes the ``msgid`` set. Context collisions (the JSON ``_overrides`` map,
keyed by dotted UI key) become gettext ``msgctxt`` entries; the English text for
those is recovered from a dotted-key English catalog passed via
``--context-english`` (only needed for this bootstrap).

Usage:
    python scripts/i18n/json_to_po.py \
        --locales-dir static/locales --out locales \
        --context-english path/to/dotted/en.json
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import po  # noqa: E402
from build_locale import LANG_META, flatten, load  # noqa: E402

BRACE = re.compile(r"\{\w+\}")
RESERVED = {"_meta", "_overrides"}

# CLDR plural-form expressions for the languages we ship / are likely to add.
PLURAL_FORMS = {
    "en": "nplurals=2; plural=(n != 1);",
    "ja": "nplurals=1; plural=0;",
    "zh": "nplurals=1; plural=0;",
    "ko": "nplurals=1; plural=0;",
    "es": "nplurals=2; plural=(n != 1);",
    "pt-BR": "nplurals=2; plural=(n > 1);",
    "fr": "nplurals=2; plural=(n > 1);",
    "de": "nplurals=2; plural=(n != 1);",
    "ru": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 "
          "&& (n%100<10 || n%100>=20) ? 1 : 2);",
    "ar": "nplurals=6; plural=(n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : n%100>=3 "
          "&& n%100<=10 ? 3 : n%100>=11 ? 4 : 5);",
    "he": "nplurals=2; plural=(n != 1);",
}


def plural_forms(code: str) -> str:
    return PLURAL_FORMS.get(code, "nplurals=2; plural=(n != 1);")


def header_fields(code: str, native: str, name: str = None, direction: str = None,
                  include_language: bool = True) -> "dict[str, str]":
    default = LANG_META.get(code, (code, code, "ltr"))
    fields = {
        "Project-Id-Version": "Odysseus",
        "Report-Msgid-Bugs-To": "",
        "POT-Creation-Date": "YEAR-MO-DA HO:MI+ZONE",
        "PO-Revision-Date": "YEAR-MO-DA HO:MI+ZONE",
        "Last-Translator": "FULL NAME <EMAIL@ADDRESS>",
        "Language-Team": "LANGUAGE <LL@li.org>",
        "Language": code,
        "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=UTF-8",
        "Content-Transfer-Encoding": "8bit",
        "Plural-Forms": plural_forms(code),
        "X-Language-Name": name or default[0],
        "X-Native-Name": native,
        "X-Direction": direction or default[2],
    }
    if not include_language:  # the .pot template carries no concrete Language
        fields.pop("Language")
    return fields


def source_items(catalog):
    return {k: v for k, v in catalog.items() if k not in RESERVED and isinstance(v, str)}


def template_entries(en_catalog, context_en):
    """Build the ordered template entry list shared by the .pot and every .po."""
    entries = []
    for src in sorted(source_items(en_catalog)):
        e = po.Entry(msgid=src, msgstr="")
        if BRACE.search(src):
            e.flags = ["python-brace-format"]
        entries.append(e)
    # context (collision) slots — msgctxt = dotted UI key, msgid = English text.
    for ctx in sorted(context_en):
        e = po.Entry(msgctxt=ctx, msgid=context_en[ctx], msgstr="")
        if BRACE.search(context_en[ctx]):
            e.flags = ["python-brace-format"]
        entries.append(e)
    return entries


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--locales-dir", default="static/locales")
    ap.add_argument("--out", default="locales")
    ap.add_argument("--context-english",
                    help="dotted-key English catalog, for labelling _overrides msgctxt entries")
    args = ap.parse_args(argv)

    index = load(os.path.join(args.locales_dir, "index.json"))
    codes = [l["code"] for l in index.get("locales", [])]
    base = index.get("default", "en")
    catalogs = {c: load(os.path.join(args.locales_dir, f"{c}.json")) for c in codes}

    # Recover English text for every override (context) key seen in any locale.
    context_en = {}
    dotted = flatten(load(args.context_english)) if args.context_english else {}
    for c in codes:
        for ctx in catalogs[c].get("_overrides", {}):
            if ctx not in context_en:
                context_en[ctx] = dotted.get(ctx, ctx)
    missing_ctx = [k for k, v in context_en.items() if v == k and k not in dotted]
    if missing_ctx:
        print(f"WARN  no English source for {len(missing_ctx)} context key(s); "
              f"using the key as msgid: {', '.join(sorted(missing_ctx)[:5])}")

    template = template_entries(catalogs[base], context_en)
    os.makedirs(args.out, exist_ok=True)

    # messages.pot — the empty template (no concrete Language header).
    pot = [po.make_header(
        header_fields("", LANG_META.get(base, ("", "", "ltr"))[1], include_language=False),
        "Odysseus UI strings — translation template.\n"
        "Generated from static/locales/en.json. Do not edit by hand.")]
    pot += template
    with open(os.path.join(args.out, "messages.pot"), "w", encoding="utf-8", newline="\n") as f:
        f.write(po.dump(pot))
    print(f"messages.pot: {len(template)} entries")

    # one <code>.po per translated locale (skip the source language).
    for code in codes:
        if code == base:
            continue
        cat = catalogs[code]
        src_map = source_items(cat)
        ov = cat.get("_overrides", {})
        native = cat.get("_meta", {}).get("nativeName", code)
        entries = [po.make_header(header_fields(code, native))]
        translated = 0
        for t in template:
            e = po.Entry(msgctxt=t.msgctxt, msgid=t.msgid, flags=list(t.flags))
            e.msgstr = (ov.get(t.msgctxt, "") if t.msgctxt is not None
                        else src_map.get(t.msgid, ""))
            if e.msgstr:
                translated += 1
            entries.append(e)
        with open(os.path.join(args.out, f"{code}.po"), "w", encoding="utf-8", newline="\n") as f:
            f.write(po.dump(entries))
        print(f"{code}.po: {translated}/{len(template)} translated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
