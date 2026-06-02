"""Tests for the gettext PO authoring layer (locales/) and its converters.

The PO files are the translator-facing source of record; the browser still loads
``static/locales/*.json``. These tests prove the two never diverge — compiling
``locales/<code>.po`` reproduces the shipped JSON byte-for-byte — and that the
seed/update scripts behave.
"""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALES = os.path.join(ROOT, "static", "locales")
PO_DIR = os.path.join(ROOT, "locales")
sys.path.insert(0, os.path.join(ROOT, "scripts", "i18n"))

import po  # noqa: E402
import po_to_json  # noqa: E402
import make_language  # noqa: E402
import update_strings  # noqa: E402
from build_locale import write_json, load  # noqa: E402

TRANSLATED = ["ja", "pt-BR"]
RESERVED = {"_meta", "_overrides"}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("code", TRANSLATED)
def test_compiled_po_matches_shipped_json_byte_for_byte(code, tmp_path):
    _, catalog = po_to_json.compile_po(os.path.join(PO_DIR, f"{code}.po"))
    out = tmp_path / f"{code}.json"
    write_json(str(out), catalog)
    assert _read(str(out)) == _read(os.path.join(LOCALES, f"{code}.json")), (
        f"{code}.po does not compile back to the shipped {code}.json")


def test_index_regenerates_identically(tmp_path):
    po_to_json.main(["--po-dir", PO_DIR, "--out", str(tmp_path)])
    assert _read(str(tmp_path / "index.json")) == _read(os.path.join(LOCALES, "index.json"))


def test_pot_covers_every_english_source_string():
    en = load(os.path.join(LOCALES, "en.json"))
    sources = {k for k, v in en.items() if k not in RESERVED and isinstance(v, str)}
    with open(os.path.join(PO_DIR, "messages.pot"), encoding="utf-8") as f:
        msgids = {e.msgid for e in po.parse(f.read()) if e.msgctxt is None and not e.is_header}
    missing = sources - msgids
    assert not missing, f"messages.pot is missing {len(missing)} source string(s): {sorted(missing)[:5]}"


def test_overrides_become_msgctxt_entries():
    ja = load(os.path.join(LOCALES, "ja.json"))
    with open(os.path.join(PO_DIR, "ja.po"), encoding="utf-8") as f:
        by_ctx = {e.msgctxt: e for e in po.parse(f.read()) if e.msgctxt is not None}
    for ctx, translation in ja.get("_overrides", {}).items():
        assert ctx in by_ctx, f"override {ctx} missing as msgctxt in ja.po"
        assert by_ctx[ctx].msgstr == translation
        assert by_ctx[ctx].msgid, f"override {ctx} has no English msgid"


def test_po_parser_round_trips_tricky_strings():
    entries = [
        po.Entry(msgid=" (default)", msgstr=" （既定）"),          # leading space
        po.Entry(msgid="line1\nline2", msgstr="x\ny"),             # embedded newline
        po.Entry(msgid='say "hi"', msgstr='「hi」'),                # quotes
        po.Entry(msgid="a\\b path", msgstr="c\\d"),                # backslash
        po.Entry(msgid="Hi {name}", msgstr="やあ {name}", flags=["python-brace-format"]),
        po.Entry(msgid="<b>bold</b>", msgstr="<b>太字</b>"),         # html
        po.Entry(msgctxt="menu.open", msgid="Open", msgstr="開く"),  # context
        # Unicode/control chars that str.splitlines() would split on but "\n" won't:
        po.Entry(msgid="line sep", msgstr="a b\x0cc\x0bd"),
    ]
    reparsed = [e for e in po.parse(po.dump(entries)) if not e.is_header]
    assert len(reparsed) == len(entries)
    for a, b in zip(entries, reparsed):
        assert (a.msgctxt, a.msgid, a.msgstr) == (b.msgctxt, b.msgid, b.msgstr)


def test_make_language_seeds_empty_translatable_catalog(tmp_path):
    shutil.copy(os.path.join(PO_DIR, "messages.pot"), tmp_path / "messages.pot")
    make_language.main(["es", "--native", "Español",
                        "--pot", str(tmp_path / "messages.pot"), "--out", str(tmp_path)])
    with open(tmp_path / "es.po", encoding="utf-8") as f:
        entries = [e for e in po.parse(f.read()) if not e.is_header]
    assert entries and all(e.msgstr == "" for e in entries), "seeded catalog should be untranslated"
    # An untranslated catalog compiles to just its _meta (every string falls back to English).
    _, catalog = po_to_json.compile_po(str(tmp_path / "es.po"))
    assert catalog["_meta"] == {"code": "es", "name": "Spanish",
                                "nativeName": "Español", "dir": "ltr"}
    assert [k for k in catalog if k not in RESERVED] == []


def test_update_strings_pure_is_idempotent(tmp_path):
    for name in os.listdir(PO_DIR):
        shutil.copy(os.path.join(PO_DIR, name), tmp_path / name)
    update_strings.main(["--en", os.path.join(LOCALES, "en.json"),
                         "--po-dir", str(tmp_path), "--pure"])
    for code in TRANSLATED:
        _, catalog = po_to_json.compile_po(str(tmp_path / f"{code}.po"))
        out = tmp_path / f"{code}.out.json"
        write_json(str(out), catalog)
        assert _read(str(out)) == _read(os.path.join(LOCALES, f"{code}.json")), (
            f"update_strings --pure changed {code} with no English change")


@pytest.mark.skipif(not shutil.which("msgfmt"), reason="system gettext not installed")
@pytest.mark.parametrize("code", TRANSLATED)
def test_po_passes_system_msgfmt_check(code, tmp_path):
    # Write the compiled .mo to tmp_path, NOT os.devnull — on Windows os.devnull
    # is "nul" and MSYS msgfmt creates a real reserved-name file in the repo.
    r = subprocess.run(["msgfmt", "--check", "-o", str(tmp_path / "out.mo"),
                        os.path.join(PO_DIR, f"{code}.po")],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"msgfmt rejected {code}.po:\n{r.stderr}"
