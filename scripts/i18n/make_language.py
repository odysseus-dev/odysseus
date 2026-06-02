#!/usr/bin/env python
"""Seed a new-language PO catalog ("mockup") from messages.pot.

The gettext equivalent of ``msginit``: copy the template to a fresh
``locales/<code>.po`` with an empty translation for every string and a correctly
filled header (Language, Plural-Forms, …), ready to hand to a translator. If the
system ``msginit`` is installed you may prefer it (``--use-gettext``); the pure
fallback needs nothing but Python and works the same on Windows and Linux.

After translating the new file, run ``po_to_json.py`` to produce the runtime
``static/locales/<code>.json`` and register it in ``index.json``.

Usage:
    python scripts/i18n/make_language.py es --native "Español"
    python scripts/i18n/make_language.py fil --name "Filipino" --native "Filipino" --dir ltr
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import po  # noqa: E402
from build_locale import LANG_META  # noqa: E402
from json_to_po import header_fields  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("code", help="locale code, e.g. es, fr, pt-BR")
    ap.add_argument("--pot", default="locales/messages.pot")
    ap.add_argument("--out", default="locales")
    ap.add_argument("--name", help="English language name (defaults to the built-in table)")
    ap.add_argument("--native", help="endonym shown in the picker, e.g. Español")
    ap.add_argument("--dir", choices=["ltr", "rtl"], help="text direction")
    ap.add_argument("--use-gettext", action="store_true",
                    help="shell out to system msginit instead of the pure-Python copy")
    ap.add_argument("--force", action="store_true", help="overwrite an existing .po")
    args = ap.parse_args(argv)

    out_path = os.path.join(args.out, f"{args.code}.po")
    if os.path.exists(out_path) and not args.force:
        ap.error(f"{out_path} already exists (use --force to overwrite)")

    default = LANG_META.get(args.code, (args.code, args.code, "ltr"))
    native = args.native or default[1]
    name = args.name or default[0]
    direction = args.dir or default[2]

    if args.use_gettext:
        subprocess.check_call(["msginit", "--no-translator", "-i", args.pot,
                               "-o", out_path, "-l", args.code])
        print(f"{out_path}: created via msginit")
        return 0

    with open(args.pot, encoding="utf-8-sig") as f:
        template = po.parse(f.read())

    entries = [po.make_header(header_fields(args.code, native, name, direction))]
    count = 0
    for t in template:
        if t.is_header:
            continue
        entries.append(po.Entry(msgctxt=t.msgctxt, msgid=t.msgid,
                                msgstr="", flags=list(t.flags)))
        count += 1
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(po.dump(entries))

    print(f"{out_path}: {count} strings to translate ({name} / {native}, {direction})")
    print("Next: translate it (Poedit/Weblate/…), then "
          "`python scripts/i18n/po_to_json.py` to build the runtime catalog.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
