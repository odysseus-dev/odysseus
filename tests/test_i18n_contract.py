import json
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
I18N_DIR = ROOT / "static" / "i18n"
STEAM_LOCALES = [
    "ar",
    "bg",
    "zh-CN",
    "zh-TW",
    "cs",
    "da",
    "nl",
    "en",
    "fi",
    "fr",
    "de",
    "el",
    "hu",
    "id",
    "it",
    "ja",
    "ko",
    "ms",
    "no",
    "pl",
    "pt",
    "pt-BR",
    "ro",
    "ru",
    "es",
    "es-419",
    "sv",
    "th",
    "tr",
    "uk",
    "vi",
]
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*|\d+)\}")
BIDI_CONTROL = re.compile(r"[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
HTML_TAG = re.compile(r"</?[a-z][^>]*>", re.IGNORECASE)
MACHINE_MARKER = re.compile(r"ZXQ|QXZ|ZXXZ|ZXZ|QLOCK", re.IGNORECASE)
HTML_ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);", re.IGNORECASE)
SCRIPT_RANGES = {
    "Arabic": ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF)),
    "Cyrillic": ((0x0400, 0x052F),),
    "Greek": ((0x0370, 0x03FF), (0x1F00, 0x1FFF)),
    "Han": ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF)),
    "Hangul": ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF)),
    "Hiragana": ((0x3040, 0x309F),),
    "Katakana": ((0x30A0, 0x30FF), (0x31F0, 0x31FF)),
    "Thai": ((0x0E00, 0x0E7F),),
}
SCRIPT_PATTERN = re.compile(
    "|".join(
        (
            f"(?P<{script}>["
            + "".join(
                f"\\U{start:08x}-\\U{end:08x}" for start, end in ranges
            )
            + "]+)"
        )
        for script, ranges in SCRIPT_RANGES.items()
    )
)
EXPECTED_NON_LATIN_SCRIPTS = {
    "ar": {"Arabic"},
    "bg": {"Cyrillic"},
    "el": {"Greek"},
    "ja": {"Han", "Hiragana", "Katakana"},
    "ko": {"Han", "Hangul"},
    "ru": {"Cyrillic"},
    "th": {"Thai"},
    "uk": {"Cyrillic"},
    "zh-CN": {"Han"},
    "zh-TW": {"Han"},
}


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _unexpected_script_runs(locale: str, target: str, source: str):
    expected = EXPECTED_NON_LATIN_SCRIPTS.get(locale, set())
    findings = []
    for match in SCRIPT_PATTERN.finditer(target):
        script = match.lastgroup
        if script in expected:
            continue
        raw_run = match.group()
        run = "".join(
            char
            for char in raw_run
            if unicodedata.category(char).startswith("L")
        )
        if run and raw_run not in source and run not in source:
            findings.append((script, run))
    return findings


def test_registry_is_the_steam_full_platform_contract():
    registry = _json(I18N_DIR / "registry.json")

    assert registry["support_level"] == "full-platform"
    assert registry["source"] == (
        "https://partner.steamgames.com/doc/store/localization/languages"
    )
    assert list(registry["locales"]) == STEAM_LOCALES
    assert registry["default_locale"] == "en"
    assert registry["locales"]["ar"]["dir"] == "rtl"
    assert all(
        metadata["dir"] == "ltr"
        for locale, metadata in registry["locales"].items()
        if locale != "ar"
    )


def test_catalog_entries_are_safe_and_complete():
    english = _json(I18N_DIR / "en.json")
    ledger = _json(I18N_DIR / "ledger.json")
    expected_keys = set(english)
    assert len(expected_keys) == ledger["source_count"]
    assert not any(HTML_ENTITY.search(value) for value in english.values())

    for locale in STEAM_LOCALES:
        catalog = _json(I18N_DIR / f"{locale}.json")
        assert set(catalog) == expected_keys, locale
        for key, target in catalog.items():
            source = english[key]
            assert isinstance(target, str) and target.strip(), (locale, key)
            assert not BIDI_CONTROL.search(target), (locale, key)
            assert not HTML_TAG.search(target), (locale, key)
            assert not MACHINE_MARKER.search(target), (locale, key)
            assert not any(unicodedata.category(char) == "Cf" for char in target), (
                locale,
                key,
            )
            assert sorted(PLACEHOLDER.findall(target)) == sorted(
                PLACEHOLDER.findall(source)
            ), (locale, key)


def test_catalog_tool_never_translates_or_generates_runtime_text():
    source = (ROOT / "scripts" / "i18n-catalog.mjs").read_text(encoding="utf-8")
    assert "translate.googleapis.com" not in source
    assert "translate-all" not in source
    assert "fetch(" not in source
    runtime = (ROOT / "static" / "js" / "i18n.js").read_text(encoding="utf-8")
    assert "fetchCatalog" in runtime
    assert "translate.googleapis.com" not in runtime


def test_catalog_ledger_preserves_source_line_locations():
    source = (
        "Choose the language used by Odysseus. "
        "Your choice is saved in this browser."
    )
    index_lines = (ROOT / "static" / "index.html").read_text(
        encoding="utf-8"
    ).splitlines()
    source_line = next(
        line_number
        for line_number, line in enumerate(index_lines, start=1)
        if source in line
    )
    ledger = _json(I18N_DIR / "ledger.json")
    record = next(entry for entry in ledger["entries"] if entry["source"] == source)

    assert f"static/index.html:{source_line}" in record["locations"]


def test_executable_catalog_fragments_remain_byte_identical():
    english = _json(I18N_DIR / "en.json")
    opaque_sources = {
        "capture-pane -t {0} -p -S -500",
        "has-session -t {0}",
        "kill-session -t {0}",
        "tmux kill-session -t {0} 2>/dev/null",
        "pkill -f vllm",
        "@font-face { font-family: '{0}'; src: url('{1}') format('{2}'); "
        "font-display: swap; }",
        "ms)",
    }
    protected_fragments = {
        "ui.ollama.is.not.installed.on.this.server.run.curl.fssl":
            "curl -fsSL https://ollama.com/install.sh | sh",
        "ui.llama.cpp.python.server.is.not.installed.run.pip.install":
            'pip install "llama-cpp-python[server]"',
        "ui.no.background.removal.model.available.install.rembg.pip.install.rembg":
            "pip install rembg",
        "ui.fix.properly.pip.install.matching.version": "pip install",
        "ui.unknown.action.value.use.list.search.view.add.update.delete":
            "list/search/view/add/update/delete/toggle_item",
    }

    assert opaque_sources.isdisjoint(english.values())

    for locale in STEAM_LOCALES:
        catalog = _json(I18N_DIR / f"{locale}.json")
        for key, fragment in protected_fragments.items():
            assert fragment in catalog[key], (locale, key, catalog[key])


@pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")
def test_canonical_catalog_validator_and_source_snapshot():
    result = subprocess.run(
        ["node", "scripts/i18n-catalog.mjs", "validate"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_non_english_catalogs_contain_real_localized_content():
    english = _json(I18N_DIR / "en.json")
    meaningful = [
        key
        for key, value in english.items()
        if re.search(r"[A-Za-z]{3}", value) and len(value) >= 4
    ]

    for locale in STEAM_LOCALES:
        if locale == "en":
            continue
        catalog = _json(I18N_DIR / f"{locale}.json")
        assert len(catalog) / len(english) >= 0.90, (
            locale,
            len(catalog),
            len(english),
        )
        changed = sum(
            catalog.get(key, english[key]) != english[key] for key in meaningful
        )
        assert changed / len(meaningful) >= 0.90, (locale, changed, len(meaningful))


def test_catalogs_have_no_unexpected_script_contamination():
    english = _json(I18N_DIR / "en.json")
    findings = []

    for locale in STEAM_LOCALES:
        if locale == "en":
            continue
        catalog = _json(I18N_DIR / f"{locale}.json")
        for key, source in english.items():
            if key not in catalog:
                continue
            for script, run in _unexpected_script_runs(
                locale, catalog[key], source
            ):
                findings.append((locale, key, script, run, catalog[key]))

    assert not findings, findings


def test_semantic_email_folder_labels_match_their_legacy_catalog_entries():
    duplicate_pairs = {
        "ui.email.folder.junk": "ui.junk.86c7d94c",
        "ui.email.folder.flagged": "ui.starred.e61561a8",
        "ui.email.folder.trash": "ui.trash",
    }

    for locale in STEAM_LOCALES:
        catalog = _json(I18N_DIR / f"{locale}.json")
        for semantic_key, legacy_key in duplicate_pairs.items():
            assert catalog.get(semantic_key) == catalog.get(legacy_key), (
                locale,
                semantic_key,
                legacy_key,
            )


def test_unexpected_script_guard_handles_leaks_and_source_literals():
    assert _unexpected_script_runs(
        "bg", "Инсталирайте الأوامر", "Install the command"
    ) == [("Arabic", "الأوامر")]
    assert _unexpected_script_runs(
        "vi", "Xóa tác vụ 已完成", "Clear completed task"
    ) == [("Han", "已完成")]
    assert _unexpected_script_runs(
        "vi", "Mở 日本語 README", "Open 日本語 README"
    ) == []


def test_auth_app_pwa_and_offline_shell_are_wired():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    login = (ROOT / "static" / "login.html").read_text(encoding="utf-8")
    worker = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")

    assert index.index("/static/js/i18n.js") < index.index("/static/js/storage.js")
    assert 'id="set-interface-language"' in index
    assert 'data-language-select' in index
    assert "/static/js/i18n.js" in login
    assert 'id="login-interface-language"' in login
    assert "/static/i18n/registry.json" in worker
    assert "/static/i18n/en.json" in worker
    assert "/static/i18n/fr.json" not in worker
    assert "setI18nText(setupNote, 'auth.first_time_setup'" in login
    assert "auth.two_factor_code" in login

    init = (ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")
    settings = (ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")
    assert "'odysseus.locale'" in init
    assert "'odysseus.locale'" in settings

    for locale in STEAM_LOCALES:
        manifest = _json(ROOT / "static" / f"manifest.{locale}.json")
        assert manifest["lang"] == locale
        assert manifest["name"] == "Odysseus"


@pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")
def test_runtime_locale_matching_and_interpolation_execute_in_node():
    script = """
      const { interpolate, matchLocale } = await import('./static/js/i18n.js');
      const { isCodeLiteral, structurallyValid } = await import(
        './scripts/i18n-catalog.mjs'
      );
      const registry = JSON.parse(
        await (await import('node:fs/promises')).readFile(
          './static/i18n/registry.json', 'utf8'
        )
      );
      console.log(JSON.stringify({
        traditional: matchLocale(['zh-Hant-HK'], registry),
        latam: matchLocale(['es-MX'], registry),
        portugal: matchLocale(['pt-PT'], registry),
        norwegian: matchLocale(['nb-NO'], registry),
        fallback: matchLocale(['xx-ZZ'], registry),
        interpolation: interpolate('Saved {count} files for {name}.', {
          count: 3,
          name: 'Ada',
        }),
        commandLiteral: isCodeLiteral('capture-pane -t {0} -p -S -500'),
        corruptPipRejected: structurallyValid(
          'Fix properly: pip install matching version',
          'Corriger correctement : pip installer la version correspondante',
        ),
        preservedPipAccepted: structurallyValid(
          'Fix properly: pip install matching version',
          'Corriger correctement : pip install la version correspondante',
        ),
      }));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert json.loads(result.stdout) == {
        "traditional": "zh-TW",
        "latam": "es-419",
        "portugal": "pt",
        "norwegian": "no",
        "fallback": "en",
        "interpolation": "Saved 3 files for Ada.",
        "commandLiteral": True,
        "corruptPipRejected": False,
        "preservedPipAccepted": True,
    }


@pytest.mark.skipif(
    not shutil.which("node") or not shutil.which("chromium"),
    reason="node and chromium are required",
)
def test_login_runtime_in_real_browser():
    result = subprocess.run(
        ["node", "tests/i18n_browser_acceptance.mjs"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    payload = json.loads(result.stdout)
    assert payload["french"]["options"] == len(STEAM_LOCALES)
    assert payload["arabic"]["dir"] == "rtl"
    assert payload["index"]["selected"] == "fr"
    assert len(payload["allLocales"]["states"]) == len(STEAM_LOCALES)
    assert payload["allLocales"]["payload"]["initialFiles"] == [
        "en.json",
        "registry.json",
    ]
    assert len(payload["allLocales"]["payload"]["loadedFiles"]) == (
        len(STEAM_LOCALES) + 1
    )
    assert payload["allLocales"]["payload"]["requestCount"] == (
        len(STEAM_LOCALES) + 1
    )
    assert payload["allLocales"]["payload"]["cachedRepeatRequests"] == 0
    assert payload["allLocales"]["payload"]["decodedBodyBytes"] > 0
    assert payload["allLocales"]["performance"]["samples"] == len(STEAM_LOCALES)
    assert payload["allLocales"]["performance"]["totalMs"] >= 0
