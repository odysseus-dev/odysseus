"""Catalog pick must not pin openai/auto or gpt-6-astra."""

from pathlib import Path
import importlib.util


def _load_bootstrap():
    path = Path("deploy/openhands/bootstrap_native_llm.py")
    spec = importlib.util.spec_from_file_location("bootstrap_native_llm", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pick_prefers_cx_gpt_55_over_astra():
    mod = _load_bootstrap()
    ids = ["cx/gpt-6-astra", "cx/gpt-5.5", "cx/gpt-5.5-review"]
    assert mod._litellm_id_from_catalog(ids) == "openai/cx/gpt-5.5"


def test_pick_skips_review_and_astra():
    mod = _load_bootstrap()
    ids = ["cx/gpt-6-astra", "cx/gpt-5.5-review", "cx/gpt-5.4"]
    assert mod._litellm_id_from_catalog(ids) == "openai/cx/gpt-5.4"


def test_pick_empty_catalog_uses_pinned_default():
    mod = _load_bootstrap()
    assert mod._litellm_id_from_catalog([]) == "openai/cx/gpt-5.5"


def test_overlay_native_chat_probe_script_exists():
    script = Path("scripts/overlay_native_chat_probe.py")
    text = script.read_text(encoding="utf-8")
    assert script.is_file()
    assert "cx/gpt-5.5" in text
    assert "gpt-6-astra" in text
    assert "openhands-agent-server:8000" in text
    assert "9router:20128" in text
    assert "native-llm-api-key" in text
    wrapper = Path("scripts/run_overlay_native_chat_probe.sh")
    wrap = wrapper.read_text(encoding="utf-8")
    assert wrapper.is_file()
    assert "orchestration-vm" in wrap
    assert "overlay_native_chat_probe.py" in wrap
