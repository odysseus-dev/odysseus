"""Static checks for the overlay stability probe. No live 9router."""

import os
from pathlib import Path


def test_stability_probe_script_matches_contract():
    script = Path("scripts/overlay_stability_probe.py")
    text = script.read_text(encoding="utf-8")
    assert script.is_file()
    assert "docker-compose.observability.yml" in text
    assert "docker-compose.relay.yml" in text
    assert "openai/cx/" in text
    assert "openai/auto" in text
    assert 'REJECT_MODEL = "openai/auto"' in text
    assert "gpt-6-astra" in text
    assert "overlay.stability" in text
    assert "odysseus.synthetic" in text
    assert "get_current_span" in text
    assert "/api/overlay/native-probe" in text
    # Native turn must go through Odysseus HTTP so uvicorn emits overlay.bind.
    assert "run_odysseus_native_pipe" in text
    assert "cloud_rows" in text
    assert "jaeger" not in text.lower()
    # openai/auto is the rejected model, not the pin.
    assert 'PINNED_MODEL_PREFIX = "openai/auto"' not in text
    assert "= \"openai/auto\"" not in text.replace('REJECT_MODEL = "openai/auto"', "")
    # Do not call the Agent-Server-direct pipe from the stability probe.
    assert "from overlay_native_chat_probe import" in text
    assert "run_native_pipe," not in text.replace("run_odysseus_native_pipe", "")
    assert "_native_pipe_step" in text
    # native_pipe step body must call Odysseus pipe
    assert "run_odysseus_native_pipe()" in text


def test_stability_probe_wrapper_sshs_guest_with_three_compose_files():
    wrapper = Path("scripts/run_overlay_stability_probe.sh")
    text = wrapper.read_text(encoding="utf-8")
    assert wrapper.is_file()
    assert os.access(wrapper, os.X_OK)
    assert "orchestration-vm" in text
    assert "overlay_stability_probe.py" in text
    assert "docker-compose.yml" in text
    assert "docker-compose.openhands.yml" in text
    assert "docker-compose.observability.yml" in text
    assert "docker-compose.relay.yml" not in text
    assert "jaeger" not in text.lower()


def test_native_probe_exposes_run_native_pipe():
    text = Path("scripts/overlay_native_chat_probe.py").read_text(encoding="utf-8")
    assert "def run_native_pipe" in text
    assert "def _pick_catalog_id" in text
    assert "gpt-6-astra" in text
    assert "cx/gpt-5.5" in text
    assert "Hello! Reply with the single word Hi." in text


def test_overlay_doc_documents_stability_probe():
    doc = Path("docs/operations/linux-vm-openhands-overlay.md").read_text(encoding="utf-8")
    assert "./scripts/run_overlay_stability_probe.sh" in doc
    assert "docker-compose.observability.yml" in doc
    assert "jaeger" not in doc.lower()
