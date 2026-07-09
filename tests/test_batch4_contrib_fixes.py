"""Regression tests for fourth contribution batch."""
import pytest

from routes.cookbook_helpers import _serve_gpu_pin_var, _serve_gpu_export_line
from routes.model_routes import _is_discovery_only_provider, _explicit_model_list_timeout
from services.hwfit.fit import _lookup_bandwidth
from core.session_manager import _content_for_db_storage


def test_hwfit_laptop_4090_bandwidth():
    bw = _lookup_bandwidth("NVIDIA GeForce RTX 4090 Laptop GPU")
    assert bw == 504


def test_openrouter_discovery_only():
    assert _is_discovery_only_provider("openrouter") is True
    assert _is_discovery_only_provider("anthropic") is False


def test_ollama_private_lan_timeout():
    t = _explicit_model_list_timeout("http://192.168.0.5:11434/v1", "auto", None)
    assert t >= 20.0


def test_serve_gpu_hip_when_env_prefix_has_hip():
    assert _serve_gpu_pin_var("HIP_VISIBLE_DEVICES=1", "llama-server --model x") == "HIP_VISIBLE_DEVICES"
    line = _serve_gpu_export_line("1", "HIP_VISIBLE_DEVICES=1", "llama-server", windows=False)
    assert line and "HIP_VISIBLE_DEVICES" in line and "1" in line


def test_storage_strips_inline_image_base64():
    blocks = [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    out = _content_for_db_storage(blocks)
    assert isinstance(out, list)
    assert out[1]["type"] == "text"
    assert "attachment" in out[1]["text"]