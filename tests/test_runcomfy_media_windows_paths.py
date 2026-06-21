import json
import subprocess
from pathlib import Path

import pytest

from src import runcomfy_media


@pytest.mark.asyncio
async def test_runcomfy_media_uses_relative_input_file_with_cwd(monkeypatch, tmp_path):
    captured = {}

    def fake_run_checked(cmd, timeout, cwd=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        assert cwd
        assert (Path(cwd) / "input.json").exists()
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(runcomfy_media, "GENERATED_IMAGES_DIR", str(tmp_path))
    monkeypatch.setattr(runcomfy_media, "_runcomfy_executable", lambda: "runcomfy.cmd")
    monkeypatch.setattr(runcomfy_media, "_check_runcomfy_ready", lambda exe: None)
    monkeypatch.setattr(runcomfy_media, "_run_checked", fake_run_checked)
    monkeypatch.setattr(
        runcomfy_media,
        "_collect_outputs",
        lambda *args, **kwargs: [{"url": "/generated_images/test.png", "type": "image", "id": "img_1"}],
    )

    result = await runcomfy_media.generate_runcomfy_media(
        "image",
        '{"prompt":"create an image of a dog"}',
    )

    assert result["exit_code"] == 0
    assert captured["cmd"][captured["cmd"].index("--input-file") + 1] == "input.json"
    assert captured["cmd"][captured["cmd"].index("--output-dir") + 1] == "."
    assert ":\\" not in " ".join(captured["cmd"])


@pytest.mark.asyncio
async def test_default_video_uses_current_kling_schema(monkeypatch, tmp_path):
    captured = {}

    def fake_run_checked(cmd, timeout, cwd=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["body"] = (Path(cwd) / "input.json").read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(runcomfy_media, "GENERATED_IMAGES_DIR", str(tmp_path))
    monkeypatch.setattr(runcomfy_media, "_runcomfy_executable", lambda: "runcomfy.cmd")
    monkeypatch.setattr(runcomfy_media, "_check_runcomfy_ready", lambda exe: None)
    monkeypatch.setattr(runcomfy_media, "_run_checked", fake_run_checked)
    monkeypatch.setattr(
        runcomfy_media,
        "_collect_outputs",
        lambda *args, **kwargs: [{"url": "/generated_images/test.mp4", "type": "video", "id": "vid_1"}],
    )

    result = await runcomfy_media.generate_runcomfy_media(
        "video",
        '{"prompt":"create a video of a dog","cfg_scale":0.7}',
    )

    body = json.loads(captured["body"])
    assert result["exit_code"] == 0
    assert captured["cmd"][2] == "kling/kling-3.0/standard/text-to-video"
    assert body["duration"] == 5
    assert body["aspect_ratio"] == "16:9"
    assert body["sound"] is False
    assert body["cfg_scale"] == 0.7
    assert "resolution" not in body
    assert "generate_audio" not in body


def test_wants_runcomfy_media_detects_explicit_comfy_provider():
    assert runcomfy_media.wants_runcomfy_media("use RunComfy to create an image of a dog") is True
    assert runcomfy_media.wants_runcomfy_media('{"provider":"comfyui","prompt":"dog"}') is True
    assert runcomfy_media.wants_runcomfy_media("create an image of a dog") is False


@pytest.mark.asyncio
async def test_runcomfy_fallback_strips_local_model_name(monkeypatch, tmp_path):
    captured = {}

    def fake_run_checked(cmd, timeout, cwd=None):
        captured["cmd"] = cmd
        captured["body"] = (Path(cwd) / "input.json").read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(runcomfy_media, "GENERATED_IMAGES_DIR", str(tmp_path))
    monkeypatch.setattr(runcomfy_media, "_runcomfy_executable", lambda: "runcomfy.cmd")
    monkeypatch.setattr(runcomfy_media, "_check_runcomfy_ready", lambda exe: None)
    monkeypatch.setattr(runcomfy_media, "_run_checked", fake_run_checked)
    monkeypatch.setattr(
        runcomfy_media,
        "_collect_outputs",
        lambda *args, **kwargs: [{"url": "/generated_images/test.png", "type": "image", "id": "img_1"}],
    )

    content = runcomfy_media.runcomfy_fallback_content("image", "create an image of a cat\nflux.1-dev")
    result = await runcomfy_media.generate_runcomfy_media("image", content)

    body = json.loads(captured["body"])
    assert result["exit_code"] == 0
    assert captured["cmd"][2] == "blackforestlabs/flux-2-klein/9b/text-to-image"
    assert "flux.1-dev" not in " ".join(captured["cmd"])
    assert body["prompt"].startswith("create an image of a cat")


@pytest.mark.parametrize(
    ("kind", "content", "expected_model", "expected_type"),
    [
        ("video", "create a video of a cat\nwan-local-video", "kling/kling-3.0/standard/text-to-video", "video"),
        ("music", "create a synthwave loop\nmusicgen-small", "acestep-ai/ace-step-1.5/text-to-audio", "audio"),
    ],
)
@pytest.mark.asyncio
async def test_runcomfy_fallback_strips_local_media_model_names(
    kind, content, expected_model, expected_type, monkeypatch, tmp_path
):
    captured = {}

    def fake_run_checked(cmd, timeout, cwd=None):
        captured["cmd"] = cmd
        captured["body"] = (Path(cwd) / "input.json").read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(runcomfy_media, "GENERATED_IMAGES_DIR", str(tmp_path))
    monkeypatch.setattr(runcomfy_media, "_runcomfy_executable", lambda: "runcomfy.cmd")
    monkeypatch.setattr(runcomfy_media, "_check_runcomfy_ready", lambda exe: None)
    monkeypatch.setattr(runcomfy_media, "_run_checked", fake_run_checked)
    monkeypatch.setattr(
        runcomfy_media,
        "_collect_outputs",
        lambda *args, **kwargs: [{"url": f"/generated_images/test.{expected_type}", "type": expected_type, "id": "media_1"}],
    )

    fallback_content = runcomfy_media.runcomfy_fallback_content(kind, content)
    result = await runcomfy_media.generate_runcomfy_media(kind, fallback_content)

    assert result["exit_code"] == 0
    assert captured["cmd"][2] == expected_model
    assert content.splitlines()[1] not in " ".join(captured["cmd"])
    body = json.loads(captured["body"])
    assert "prompt" in body or "tags" in body
    assert content.splitlines()[0] in (body.get("prompt") or body.get("tags") or "")
