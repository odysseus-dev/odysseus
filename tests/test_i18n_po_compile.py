import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.i18n.compile_po import (  # noqa: E402
    compile_all,
    load_metadata,
    parse_po,
)


TARGET_LOCALES = {"ar", "ja", "fr", "pt-BR", "sv"}


def test_parse_po_handles_multiline_strings(tmp_path):
    po_path = tmp_path / "messages.po"
    po_path.write_text(
        '''
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "Search "
"memories…"
msgstr "Rechercher "
"dans les souvenirs…"
'''.lstrip(),
        encoding="utf-8",
    )

    assert parse_po(po_path) == {
        "Search memories…": "Rechercher dans les souvenirs…",
    }


def test_supported_locales_are_declared_with_direction():
    metadata = load_metadata(ROOT / "locales" / "locales.json")
    codes = {item["code"] for item in metadata}

    assert TARGET_LOCALES.issubset(codes)
    assert next(item for item in metadata if item["code"] == "ar")["dir"] == "rtl"
    assert next(item for item in metadata if item["code"] == "ja")["dir"] == "ltr"


def test_po_catalogs_compile_to_committed_browser_json():
    compiled = compile_all(ROOT)

    for locale in TARGET_LOCALES:
        generated_path = ROOT / "static" / "locales" / f"{locale}.json"
        assert generated_path.exists(), f"missing generated catalog for {locale}"

        generated = json.loads(generated_path.read_text(encoding="utf-8"))
        assert generated == compiled[locale]
        assert generated["locale"] == locale
        assert generated["messages"]["Language"]
        assert generated["messages"]["Sign In"]
