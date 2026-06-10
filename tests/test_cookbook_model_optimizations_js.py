import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _detect(model):
    source = (
        "import { _detectModelOptimizations } from "
        "'./static/js/cookbook/modelOptimizations.js';\n"
        "console.log(JSON.stringify(_detectModelOptimizations(%r)));" % model
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_dense_qwen35_gets_no_moe_expert_parallel():
    # A dense Qwen3.5 model (no a10b/a22b/a3b MoE suffix) must NOT get the MoE
    # expert-parallel flag. The old `||`/`&&` precedence let any name merely
    # containing "qwen3.5" enter the MoE branch, breaking vLLM launch on dense models.
    opts = _detect("Qwen3.5-4B-Instruct")
    assert "--enable-expert-parallel" not in opts["flags"]
    assert opts["envVars"] == []


def test_qwen35_moe_still_gets_expert_parallel():
    opts = _detect("Qwen3.5-30B-A3B")
    assert "--enable-expert-parallel" in opts["flags"]
    assert "VLLM_USE_FLASHINFER_SAMPLER=0" in opts["envVars"]


def test_qwen35_a17b_moe_gets_expert_parallel():
    # Catalog-covered variant (services/hwfit/data/hf_models.json has
    # Qwen3.5-397B-A17B rows). The MoE suffix gate is a generic A<number>B match,
    # so a real A17B MoE model still reaches the expert-parallel path even though
    # 17 is not in the old a10b/a22b/a3b list.
    opts = _detect("Qwen/Qwen3.5-397B-A17B")
    assert "--enable-expert-parallel" in opts["flags"]
    assert "VLLM_USE_FLASHINFER_SAMPLER=0" in opts["envVars"]


def test_qwen35_a17b_moe_gets_expert_parallel():
    # Catalog-covered variant (services/hwfit/data/hf_models.json has
    # Qwen3.5-397B-A17B rows). The MoE suffix gate is a generic A<number>B match,
    # so a real A17B MoE model still reaches the expert-parallel path even though
    # 17 is not in the old a10b/a22b/a3b list.
    opts = _detect("Qwen/Qwen3.5-397B-A17B")
    assert "--enable-expert-parallel" in opts["flags"]
    assert "VLLM_USE_FLASHINFER_SAMPLER=0" in opts["envVars"]


def test_qwen3_moe_unaffected():
    # Regression guard: the non-3.5 qwen3 MoE path must keep working.
    opts = _detect("Qwen3-235B-A22B")
    assert "--enable-expert-parallel" in opts["flags"]


def test_dense_qwen3_gets_no_moe_flags():
    opts = _detect("Qwen3-8B")
    assert "--enable-expert-parallel" not in opts["flags"]
