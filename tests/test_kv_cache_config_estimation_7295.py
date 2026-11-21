"""Regression tests for architecture-derived KV-cache estimation (#7295, #7286)."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import struct
import subprocess

import pytest

from routes.hwfit_routes import _extract_arch_from_config, _inspect_model_path, _parse_gguf_kv_header
from services.hwfit.models import estimate_memory_gb, kv_bytes_per_token, kv_gb_per_token
from services.hwfit.profiles import _kv_gb


ROOT = Path(__file__).resolve().parents[1]
SERVE_JS = (ROOT / "static/js/cookbookServe.js").read_text(encoding="utf-8")


def test_kv_bytes_per_token_matches_config_for_gqa_and_moe() -> None:
    # Qwen3-8B: 36 layers, 8 KV heads, head_dim=128 -> 2 * 36 * 8 * 128 * 2 = 147,456 B/tok at f16
    qwen3_8b = {
        "name": "Qwen/Qwen3-8B",
        "parameter_count": "8.19B",
        "num_hidden_layers": 36,
        "num_key_value_heads": 8,
        "head_dim": 128,
    }
    assert kv_bytes_per_token(qwen3_8b, "f16") == pytest.approx(147_456.0)
    assert kv_bytes_per_token(qwen3_8b, "q8_0") == pytest.approx(73_728.0)
    assert kv_bytes_per_token(qwen3_8b, "q4_0") == pytest.approx(36_864.0)

    # Qwen2.5-Coder-7B: 28 layers, 4 KV heads, head_dim=128 -> 2 * 28 * 4 * 128 * 2 = 57,344 B/tok (56 KiB) at f16
    qwen25_7b = {
        "name": "Qwen/Qwen2.5-Coder-7B-Instruct",
        "parameter_count": "7.6B",
        "num_hidden_layers": 28,
        "num_key_value_heads": 4,
        "head_dim": 128,
    }
    assert kv_bytes_per_token(qwen25_7b, "f16") == pytest.approx(57_344.0)
    # 32,768 tokens at f16 = 1.75 GiB
    assert _kv_gb(qwen25_7b, 32768, "f16") == pytest.approx(1.75)

    # MoE model with explicit attention fields uses attention layout rather than active_parameters proxy
    moe_model = {
        "name": "Qwen/Qwen3-30B-A3B",
        "parameter_count": "30.5B",
        "active_parameters": 3_300_000_000,
        "is_moe": True,
        "num_hidden_layers": 48,
        "num_key_value_heads": 4,
        "head_dim": 128,
    }
    assert kv_bytes_per_token(moe_model, "f16") == pytest.approx(2 * 48 * 4 * 128 * 2)

    # Fallback when architecture fields are missing preserves legacy active-params formula
    legacy_model = {"name": "unknown/7b-model", "parameter_count": "7B"}
    assert _kv_gb(legacy_model, 1000, "q8_0") == pytest.approx(0.000008 * 7.0 * 1000 * 1.0)
    assert estimate_memory_gb(legacy_model, "Q4_K_M", 1000) == pytest.approx(7.0 * 0.58 + 0.000008 * 7.0 * 1000 + 0.5)


def test_parse_gguf_kv_header_and_inspect_model_path(tmp_path: Path) -> None:
    def _gguf_str(s: str) -> bytes:
        b = s.encode("utf-8")
        return struct.pack("<Q", len(b)) + b

    kvs = [
        ("general.architecture", 8, _gguf_str("qwen2")),
        ("qwen2.block_count", 4, struct.pack("<I", 28)),
        ("qwen2.attention.head_count", 4, struct.pack("<I", 28)),
        ("qwen2.attention.head_count_kv", 4, struct.pack("<I", 4)),
        ("qwen2.attention.key_length", 4, struct.pack("<I", 128)),
        ("qwen2.context_length", 4, struct.pack("<I", 32768)),
    ]
    buf = bytearray()
    buf += b"GGUF"
    buf += struct.pack("<I", 3)
    buf += struct.pack("<QQ", 0, len(kvs))
    for k, vtype, raw_val in kvs:
        buf += _gguf_str(k)
        buf += struct.pack("<I", vtype)
        buf += raw_val

    gguf_file = tmp_path / "Qwen2.5-Coder-7B-Instruct-Q6_K.gguf"
    buf += b"\x00" * 4096
    gguf_file.write_bytes(bytes(buf))

    parsed = _parse_gguf_kv_header(str(gguf_file))
    assert parsed["num_hidden_layers"] == 28
    assert parsed["num_key_value_heads"] == 4
    assert parsed["head_dim"] == 128
    assert parsed["model_ctx_max"] == 32768

    inspected = _inspect_model_path(str(gguf_file))
    assert inspected["num_hidden_layers"] == 28
    assert inspected["num_key_value_heads"] == 4
    assert inspected["head_dim"] == 128
    assert inspected["model_weights_gb"] > 0

    cfg_arch = _extract_arch_from_config({"num_hidden_layers": 36, "num_key_value_heads": 8, "head_dim": 128})
    assert cfg_arch == {"num_hidden_layers": 36, "num_key_value_heads": 8, "head_dim": 128}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js runtime not available")
def test_cookbook_serve_llama_auto_context_gqa_and_oversized_in_node_vm() -> None:
    start = SERVE_JS.index("function _archKvGbPerTokenF16(")
    end = SERVE_JS.index("function _estimateMlxContextFit(")
    fn_src = SERVE_JS[start:end]

    harness = f"""
const assert = require('assert');
const vm = require('vm');

const sandbox = {{
  Math,
  Number,
  String,
  parseInt,
  parseFloat,
  Array,
  Object,
  _hwfitCache: null,
  _modelContextMaxForServe: (_m, max) => Number(max) || 32768,
  _modelSizeGb: (_m, w) => Number(w) || 0,
}};

vm.createContext(sandbox);
vm.runInContext({json.dumps(fn_src)}, sandbox);

const sys2080Ti = {{
  gpu_vram_gb: 11,
  gpu_groups: [{{ vram_each: 11, count: 1 }}],
}};

// Case 1: Qwen2.5-Coder-7B Q6_K (~6.25 GB) on 11 GB GPU with 28 layers, 4 KV heads, head_dim=128
// 32,768 ctx at f16 KV needs only 1.75 GiB -> fits full 32,768 context (not 7,168)
const profile7B = {{
  profiles: [],
  arch: {{ num_hidden_layers: 28, num_key_value_heads: 4, head_dim: 128 }},
}};
const fit7B = sandbox._estimateLlamaContextFit(
  {{ name: 'bartowski/Qwen2.5-Coder-7B-Instruct-GGUF' }},
  {{ backend: 'llamacpp', llama_mode: 'gpu', cache_type: 'f16' }},
  32768,
  6.25,
  sys2080Ti,
  profile7B
);
assert.strictEqual(fit7B.ctx, 32768, 'Expected full 32768 ctx for 7B GQA model on 11 GB GPU');

// Case 2: Oversized 14B Q4_K_M (~10.6 GB > 10.0 GB usable VRAM) -> doesNotFit with ctx=0, NOT 8192
const fit14B = sandbox._estimateLlamaContextFit(
  {{ name: 'bartowski/Qwen2.5-Coder-14B-Instruct-GGUF' }},
  {{ backend: 'llamacpp', llama_mode: 'gpu', cache_type: 'f16' }},
  32768,
  10.6,
  sys2080Ti,
  {{ profiles: [] }}
);
assert.strictEqual(fit14B.doesNotFit, true);
assert.strictEqual(fit14B.ctx, 0);
"""
    proc = subprocess.run(
        ["node", "-e", harness],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"Node VM failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
