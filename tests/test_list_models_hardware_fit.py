import json

import pytest

from src.agent_tools.model_interaction_tools import list_models


@pytest.mark.asyncio
async def test_recommended_model_filter_uses_hardware_fit_backend(monkeypatch):
    observed = {}

    async def fake_app_api(content, owner=None):
        observed.update({"args": json.loads(content), "owner": owner})
        return {
            "json": {
                "system": {
                    "gpu_name": "Test GPU", "gpu_count": 2,
                    "gpu_vram_gb": 48, "backend": "cuda",
                    "cpu_name": "Test CPU", "total_ram_gb": 64,
                },
                "models": [{
                    "name": "org/model", "parameter_count": "30B", "quant": "Q4",
                    "required_gb": 20, "fit_level": "perfect", "run_mode": "gpu",
                    "speed_tps": 50, "score": 99, "context": 32768,
                }],
            },
            "exit_code": 0,
        }

    monkeypatch.setattr("src.tools.system.do_app_api", fake_app_api)

    result = await list_models("recommended", owner="pewds")

    assert "GPU: Test GPU; count=2; total VRAM=48 GB" in result["output"]
    assert "org/model: params=30B, quant=Q4, required=20 GB" in result["output"]
    assert observed == {
        "owner": "pewds",
        "args": {
            "action": "call",
            "method": "GET",
            "path": "/api/hwfit/models",
            "query": {"fit_only": "true", "limit": 5, "sort": "fit"},
        },
    }
