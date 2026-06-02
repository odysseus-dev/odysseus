"""Smoke-test the UI i18n helper in static/js/i18n.js."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_I18N = _REPO / "static" / "js" / "i18n.js"
_HAS_NODE = shutil.which("node") is not None


def _run_i18n(js_snippet: str) -> str:
    bootstrap = f"""
import {{ t, setLocale, getLocale, SUPPORTED_LOCALES }} from './static/js/i18n.js';
{js_snippet}
"""
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=bootstrap,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_i18n_default_is_english():
    out = _run_i18n("""
await setLocale('en');
console.log(JSON.stringify({
  locale: getLocale(),
  title: t('settings.title'),
  missing: t('missing.key.example'),
}));
""")
    data = json.loads(out)
    assert data["locale"] == "en"
    assert data["title"] == "Settings"
    assert data["missing"] == "missing.key.example"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_i18n_spanish_settings_title():
    out = _run_i18n("""
await setLocale('es');
console.log(JSON.stringify({
  locale: getLocale(),
  title: t('settings.title'),
  wipe: t('settings.system.danger.wipeBtn'),
}));
""")
    data = json.loads(out)
    assert data["locale"] == "es"
    assert data["title"] == "Configuración"
    assert data["wipe"] == "Borrar"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_i18n_module_loads():
    assert _I18N.is_file()
    out = _run_i18n("console.log(JSON.stringify(SUPPORTED_LOCALES));")
    assert json.loads(out) == ["en", "es"]
