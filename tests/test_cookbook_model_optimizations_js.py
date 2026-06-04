"""Regression tests for Cookbook vLLM model optimization detection."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _detect(model_name: str) -> dict:
    source = f"""
        import {{ _detectModelOptimizations }} from './static/js/cookbookModelOptimizations.js';
        console.log(JSON.stringify(_detectModelOptimizations({json.dumps(model_name)})));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_dense_qwen35_does_not_get_moe_expert_parallel_flags():
    opts = _detect("Qwen/Qwen3.5-4B")

    assert "--enable-expert-parallel" not in opts["flags"]
    assert "--reasoning-parser qwen3" not in opts["flags"]
    assert opts["envVars"] == []


def test_qwen35_moe_gets_expert_parallel_flags():
    opts = _detect("Qwen/Qwen3.5-35B-A3B")

    assert "--enable-expert-parallel" in opts["flags"]
    assert "--reasoning-parser qwen3" in opts["flags"]
    assert "VLLM_USE_FLASHINFER_MOE_FP16=1" in opts["envVars"]


def test_qwen35_a17b_moe_suffix_is_detected():
    opts = _detect("Qwen/Qwen3.5-397B-A17B")

    assert "--enable-expert-parallel" in opts["flags"]
    assert "--reasoning-parser qwen3" in opts["flags"]


def test_qwen3_moe_still_gets_expert_parallel_flags():
    opts = _detect("Qwen/Qwen3-235B-A22B")

    assert "--enable-expert-parallel" in opts["flags"]
    assert "--reasoning-parser qwen3" in opts["flags"]


def test_qwen35_a10b_keeps_speculative_default():
    opts = _detect("Qwen/Qwen3.5-122B-A10B")

    assert opts["spec"] == {"method": "qwen3_next_mtp", "tokens": 2}
    assert any("--speculative-config" in flag for flag in opts["flags"])
