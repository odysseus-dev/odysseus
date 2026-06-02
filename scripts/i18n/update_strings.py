#!/usr/bin/env python
"""Refresh the PO template and merge new/changed strings into every language.

Run this after English strings change (a feature adds or rewords UI text). It:

1. Rebuilds ``locales/messages.pot`` from ``static/locales/en.json`` (the
   canonical English source), preserving the context/``msgctxt`` slots already in
   the template.
2. Merges the updated template into each ``locales/<code>.po`` — keeping existing
   translations, adding empty entries for new strings, and dropping obsolete ones.

When the system ``msgmerge`` is installed it is used by default, which adds
**fuzzy matching**: a reworded English string keeps its old translation, flagged
``#, fuzzy`` for a translator to confirm, instead of silently reverting to
English. Without gettext (``--pure``), an exact-match merge is used — translations
survive verbatim source strings but a reworded string starts empty.

Usage:
    python scripts/i18n/update_strings.py
    python scripts/i18n/update_strings.py --pure      # force the no-gettext merge
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import po  # noqa: E402
from build_locale import load  # noqa: E402
from json_to_po import BRACE, RESERVED, header_fields  # noqa: E402


def rebuild_pot(en_path, pot_path):
    """messages.pot = English source entries + preserved context slots."""
    en = load(en_path)
    sources = sorted(k for k, v in en.items() if k not in RESERVED and isinstance(v, str))

    preserved_ctx = []
    if os.path.exists(pot_path):
        with open(pot_path, encoding="utf-8-sig") as f:
            for e in po.parse(f.read()):
                if e.msgctxt is not None:
                    preserved_ctx.append(e)

    entries = [po.make_header(header_fields("", "English", include_language=False))]
    for src in sources:
        e = po.Entry(msgid=src, msgstr="")
        if BRACE.search(src):
            e.flags = ["python-brace-format"]
        entries.append(e)
    entries += sorted(preserved_ctx, key=lambda e: e.msgctxt)

    with open(pot_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(po.dump(entries))
    return len(sources), len(preserved_ctx)


def merge_pure(po_path, pot_path):
    """Exact-match merge: template order, old msgstr kept where (ctx,msgid) matches."""
    with open(pot_path, encoding="utf-8-sig") as f:
        template = po.parse(f.read())
    with open(po_path, encoding="utf-8-sig") as f:
        old = po.parse(f.read())
    old_by_key = {e.key: e for e in old if not e.is_header}
    header = next((e for e in old if e.is_header), template[0])

    out = [header]
    kept = new = 0
    for t in template:
        if t.is_header:
            continue
        e = po.Entry(msgctxt=t.msgctxt, msgid=t.msgid, flags=list(t.flags))
        prev = old_by_key.get(t.key)
        if prev and prev.msgstr:
            e.msgstr = prev.msgstr
            kept += 1
        else:
            new += 1
        out.append(e)
    with open(po_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(po.dump(out))
    return kept, new


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--en", default="static/locales/en.json")
    ap.add_argument("--po-dir", default="locales")
    ap.add_argument("--pure", action="store_true", help="force the no-gettext exact merge")
    args = ap.parse_args(argv)

    pot_path = os.path.join(args.po_dir, "messages.pot")
    n_src, n_ctx = rebuild_pot(args.en, pot_path)
    print(f"messages.pot: {n_src} source strings, {n_ctx} context slots")

    use_gettext = (not args.pure) and shutil.which("msgmerge")
    print(f"merge backend: {'gettext msgmerge (fuzzy)' if use_gettext else 'pure exact-match'}")

    for po_path in sorted(glob.glob(os.path.join(args.po_dir, "*.po"))):
        code = os.path.splitext(os.path.basename(po_path))[0]
        if use_gettext:
            subprocess.check_call(["msgmerge", "--update", "--backup=none", "--previous",
                                   po_path, pot_path],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"  {code}.po: merged (msgmerge)")
        else:
            kept, new = merge_pure(po_path, pot_path)
            print(f"  {code}.po: {kept} kept, {new} new/empty")
    print("Next: `python scripts/i18n/po_to_json.py` to rebuild the runtime catalogs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
