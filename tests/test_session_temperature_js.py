"""Node test for static/js/sessionTemperature.js (popup display rules)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MODULE = _REPO / "static" / "js" / "sessionTemperature.js"
_SRC = _MODULE.read_text(encoding="utf-8") if _MODULE.exists() else ""
_HAS_NODE = shutil.which("node") is not None
needs_node = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def _run(body):
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=_SRC + "\n" + body, capture_output=True, text=True,
        encoding="utf-8", cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@needs_node
def test_the_session_override_wins_over_the_model_value():
    body = """
    console.log(JSON.stringify(effectiveTemperature({
      temperature_override: 0.9, effective_temperature: 0.6,
      temperature_source: 'session' })));
    """
    assert _run(body) == 0.9


@needs_node
def test_the_model_value_is_used_without_an_override():
    body = """
    console.log(JSON.stringify(effectiveTemperature({
      temperature_override: null, effective_temperature: 0.6,
      temperature_source: 'model' })));
    """
    assert _run(body) == 0.6


@needs_node
def test_the_default_is_one_without_a_model_value():
    assert _run("console.log(JSON.stringify(effectiveTemperature({})));") == 1


@needs_node
def test_the_hint_only_appears_for_a_model_value():
    body = """
    console.log(JSON.stringify([
      temperatureHint({ temperature_source: 'model' }),
      temperatureHint({ temperature_source: 'session' }),
      temperatureHint({}),
    ]));
    """
    assert _run(body) == ["From model settings", "", ""]


@needs_node
def test_load_session_temperature_uses_the_session_payload():
    body = """
    const urls = [];
    const fakeFetch = async (url) => { urls.push(url);
      return { ok: true, json: async () => ({
        temperature_override: null, effective_temperature: 0.6,
        temperature_source: 'model' }) };
    };
    const info = await loadSessionTemperature(fakeFetch, '', 's 1');
    console.log(JSON.stringify({ info, urls }));
    """
    result = _run(body)
    assert result["urls"] == ["/api/session/s%201/context"]
    assert result["info"] == {
        "value": 0.6, "hint": "From model settings",
        "source": "model", "override": None,
    }


@needs_node
def test_load_session_temperature_returns_null_on_failure_or_no_session():
    body = """
    let calls = 0;
    const badFetch = async () => { calls += 1; return { ok: false }; };
    const failed = await loadSessionTemperature(badFetch, '', 's1');
    const absent = await loadSessionTemperature(badFetch, '', '');
    console.log(JSON.stringify({ failed, absent, calls }));
    """
    assert _run(body) == {"failed": None, "absent": None, "calls": 1}
