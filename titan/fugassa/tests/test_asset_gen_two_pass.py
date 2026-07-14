"""Two-pass scene generation (pass1 txt2img → pass2 ControlNet)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from titan.fugassa import asset_gen


@pytest.mark.asyncio
async def test_generate_scene_two_pass_runs_both_passes(tmp_path):
    dest = tmp_path / "scenes" / "other_1_v2.png"
    dest.parent.mkdir(parents=True)

    calls: list[dict] = []

    async def fake_generate(**kwargs):
        calls.append(dict(kwargs))
        path = kwargs["dest_path"]
        with open(path, "wb") as f:
            f.write(b"png")
        return {"success": True, "path": path, "style": "anime"}

    with patch.object(asset_gen, "generate_image", side_effect=fake_generate):
        result = await asset_gen.generate_scene_two_pass(
            positive_prompt="1boy, forest",
            negative_prompt="bad",
            theme="anime",
            dest_path=str(dest),
        )

    assert result["success"] is True
    assert result.get("two_pass") is True
    assert len(calls) == 2
    assert calls[0].get("control") is None
    assert calls[0].get("shutdown_after") is False
    assert calls[0]["positive_prompt"] == "1boy, forest"
    assert calls[1].get("control") is not None
    assert calls[1].get("shutdown_after") is True
    assert calls[1]["control"]["type"] == "canny"
    assert calls[1]["positive_prompt"] != calls[0]["positive_prompt"]
    assert "highly detailed" in calls[1]["positive_prompt"]
    assert calls[1].get("init_image_path") is not None
    assert calls[1].get("init_strength") == pytest.approx(0.35)
    assert calls[1].get("steps") is not None
    assert calls[1].get("cfg_scale") is not None
    assert dest.is_file()


@pytest.mark.asyncio
async def test_generate_scene_two_pass_fallback_to_pass1(tmp_path):
    dest = tmp_path / "scenes" / "other_1_v3.png"
    dest.parent.mkdir(parents=True)
    n = 0

    async def fake_generate(**kwargs):
        nonlocal n
        n += 1
        path = kwargs["dest_path"]
        with open(path, "wb") as f:
            f.write(b"png")
        if n == 1:
            return {"success": True, "path": path}
        return {"success": False, "error": "controlnet missing"}

    with patch.object(asset_gen, "generate_image", side_effect=fake_generate):
        result = await asset_gen.generate_scene_two_pass(
            positive_prompt="scene",
            dest_path=str(dest),
        )

    assert result["success"] is True
    assert result.get("two_pass") is False
    assert dest.is_file()
