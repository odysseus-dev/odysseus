import json
import subprocess
from pathlib import Path

import pytest

from src import runcomfy_media


def _enable_runcomfy(monkeypatch):
    async def fake_comfyui_available(integration=None):
        return False

    monkeypatch.setattr(
        runcomfy_media,
        "_runcomfy_integration",
        lambda args=None: {"id": "runcomfy", "name": "RunComfy Cloud", "preset": "runcomfy_cloud"},
    )
    monkeypatch.setattr(runcomfy_media, "_comfyui_server_available", fake_comfyui_available)
    monkeypatch.setattr(runcomfy_media, "_comfyui_can_auto_launch", lambda integration=None: False)
    monkeypatch.setattr(runcomfy_media, "_comfyui_can_auto_bootstrap", lambda integration=None: False)


@pytest.mark.asyncio
async def test_runcomfy_media_uses_relative_input_file_with_cwd(monkeypatch, tmp_path):
    captured = {}

    def fake_run_checked(cmd, timeout, cwd=None, env=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        assert cwd
        assert (Path(cwd) / "input.json").exists()
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(runcomfy_media, "GENERATED_IMAGES_DIR", str(tmp_path))
    _enable_runcomfy(monkeypatch)
    monkeypatch.setattr(runcomfy_media, "_runcomfy_executable", lambda: "runcomfy.cmd")
    monkeypatch.setattr(runcomfy_media, "_check_runcomfy_ready", lambda exe, integration=None: None)
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

    def fake_run_checked(cmd, timeout, cwd=None, env=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["body"] = (Path(cwd) / "input.json").read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(runcomfy_media, "GENERATED_IMAGES_DIR", str(tmp_path))
    _enable_runcomfy(monkeypatch)
    monkeypatch.setattr(runcomfy_media, "_runcomfy_executable", lambda: "runcomfy.cmd")
    monkeypatch.setattr(runcomfy_media, "_check_runcomfy_ready", lambda exe, integration=None: None)
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

    def fake_run_checked(cmd, timeout, cwd=None, env=None):
        captured["cmd"] = cmd
        captured["body"] = (Path(cwd) / "input.json").read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(runcomfy_media, "GENERATED_IMAGES_DIR", str(tmp_path))
    _enable_runcomfy(monkeypatch)
    monkeypatch.setattr(runcomfy_media, "_runcomfy_executable", lambda: "runcomfy.cmd")
    monkeypatch.setattr(runcomfy_media, "_check_runcomfy_ready", lambda exe, integration=None: None)
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

    def fake_run_checked(cmd, timeout, cwd=None, env=None):
        captured["cmd"] = cmd
        captured["body"] = (Path(cwd) / "input.json").read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(runcomfy_media, "GENERATED_IMAGES_DIR", str(tmp_path))
    _enable_runcomfy(monkeypatch)
    monkeypatch.setattr(runcomfy_media, "_runcomfy_executable", lambda: "runcomfy.cmd")
    monkeypatch.setattr(runcomfy_media, "_check_runcomfy_ready", lambda exe, integration=None: None)
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


@pytest.mark.asyncio
async def test_runcomfy_cloud_requires_enabled_integration(monkeypatch, tmp_path):
    called = False

    def fake_executable():
        nonlocal called
        called = True
        return "runcomfy.cmd"

    monkeypatch.setattr(runcomfy_media, "GENERATED_IMAGES_DIR", str(tmp_path))
    monkeypatch.setattr(runcomfy_media, "_runcomfy_integration", lambda args=None: None)
    monkeypatch.setattr(runcomfy_media, "_runcomfy_executable", fake_executable)

    result = await runcomfy_media.generate_runcomfy_media(
        "image",
        '{"provider":"runcomfy","prompt":"create an image of a dog"}',
    )

    assert result["exit_code"] == 1
    assert "RunComfy Cloud is disabled" in result["error"]
    assert called is False


@pytest.mark.asyncio
async def test_explicit_local_comfyui_routes_to_local_backend(monkeypatch, tmp_path):
    captured = {}

    async def fake_local(kind, args, **kwargs):
        captured["kind"] = kind
        captured["args"] = args
        return {"exit_code": 0, "media_provider": "comfyui_local"}

    monkeypatch.setattr(runcomfy_media, "GENERATED_IMAGES_DIR", str(tmp_path))
    monkeypatch.setattr(runcomfy_media, "_generate_local_comfyui_media", fake_local)
    monkeypatch.setattr(runcomfy_media, "_runcomfy_executable", lambda: (_ for _ in ()).throw(AssertionError("RunComfy should not run")))

    result = await runcomfy_media.generate_runcomfy_media(
        "image",
        '{"provider":"comfyui","prompt":"create an image of a dog"}',
    )

    assert result["exit_code"] == 0
    assert result["media_provider"] == "comfyui_local"
    assert captured["kind"] == "image"
    assert captured["args"]["prompt"] == "create an image of a dog"


@pytest.mark.asyncio
async def test_explicit_integration_infers_runcomfy_provider(monkeypatch, tmp_path):
    captured = {}

    def fake_run_checked(cmd, timeout, cwd=None, env=None):
        captured["cmd"] = cmd
        captured["body"] = (Path(cwd) / "input.json").read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    async def fake_comfyui_available(integration=None):
        raise AssertionError("local ComfyUI should not be probed when RunComfy integration is explicit")

    monkeypatch.setattr(runcomfy_media, "GENERATED_IMAGES_DIR", str(tmp_path))
    monkeypatch.setattr(
        runcomfy_media,
        "_runcomfy_integration",
        lambda args=None: {"id": "paid", "name": "RunComfy Cloud", "preset": "runcomfy_cloud"},
    )
    monkeypatch.setattr(runcomfy_media, "_comfyui_integration", lambda args=None: None)
    monkeypatch.setattr(runcomfy_media, "_comfyui_server_available", fake_comfyui_available)
    monkeypatch.setattr(runcomfy_media, "_runcomfy_executable", lambda: "runcomfy.cmd")
    monkeypatch.setattr(runcomfy_media, "_check_runcomfy_ready", lambda exe, integration=None: None)
    monkeypatch.setattr(runcomfy_media, "_run_checked", fake_run_checked)
    monkeypatch.setattr(
        runcomfy_media,
        "_collect_outputs",
        lambda *args, **kwargs: [{"url": "/generated_images/test.png", "type": "image", "id": "img_1"}],
    )

    result = await runcomfy_media.generate_runcomfy_media(
        "image",
        '{"integration":"paid","prompt":"create an image of a dog"}',
    )

    assert result["exit_code"] == 0
    assert result["media_provider"] == "runcomfy"
    assert captured["cmd"][2] == "blackforestlabs/flux-2-klein/9b/text-to-image"


def test_default_comfyui_workflow_uses_first_checkpoint():
    workflow, prompt, model_id = runcomfy_media._build_default_comfyui_image_workflow(
        {"prompt": "a red chair", "size": "1024x1024", "steps": 3},
        {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["dream.safetensors"]]}}
            },
            "KSampler": {
                "input": {
                    "required": {
                        "sampler_name": [["euler"]],
                        "scheduler": [["normal"]],
                    }
                }
            },
        },
    )

    assert workflow["4"]["inputs"]["ckpt_name"] == "dream.safetensors"
    assert workflow["3"]["inputs"]["steps"] == 3
    assert workflow["5"]["inputs"]["width"] == 1024
    assert prompt.startswith("a red chair")
    assert model_id == "comfyui-local:dream.safetensors"


def test_comfyui_auto_launch_prefers_amd_directml_script(monkeypatch, tmp_path):
    comfy_dir = tmp_path / "ComfyUI"
    comfy_dir.mkdir()
    (comfy_dir / "run_directml.bat").write_text("@echo off\n", encoding="utf-8")

    monkeypatch.setenv("COMFYUI_DIR", str(comfy_dir))
    monkeypatch.setenv("COMFYUI_ACCELERATOR", "amd")
    monkeypatch.delenv("COMFYUI_LAUNCH_COMMAND", raising=False)

    spec = runcomfy_media._comfyui_launch_spec({"base_url": "http://127.0.0.1:8188"})

    assert spec
    assert spec["accelerator"] == "directml"
    assert spec["cwd"] == str(comfy_dir.resolve())
    assert spec["source"] == "run_directml.bat"


def test_comfyui_auto_launch_adds_directml_for_main_py(monkeypatch, tmp_path):
    comfy_dir = tmp_path / "ComfyUI"
    comfy_dir.mkdir()
    (comfy_dir / "main.py").write_text("print('comfy')\n", encoding="utf-8")
    python_dir = comfy_dir / "python_embeded"
    python_dir.mkdir()
    python_exe = python_dir / "python.exe"
    python_exe.write_text("", encoding="utf-8")

    monkeypatch.setenv("COMFYUI_DIR", str(comfy_dir))
    monkeypatch.setenv("COMFYUI_ACCELERATOR", "directml")
    monkeypatch.delenv("COMFYUI_LAUNCH_COMMAND", raising=False)

    spec = runcomfy_media._comfyui_launch_spec({"base_url": "http://127.0.0.1:8188"})

    assert spec
    assert spec["source"] == "main.py"
    assert spec["argv"][0] == str(python_exe)
    assert "--directml" in spec["argv"]


def test_comfyui_auto_launch_finds_bootstrap_data_dir(monkeypatch, tmp_path):
    comfy_dir = tmp_path / "comfyui" / "ComfyUI"
    comfy_dir.mkdir(parents=True)
    (comfy_dir / "main.py").write_text("print('comfy')\n", encoding="utf-8")
    python_dir = comfy_dir / ".venv" / "Scripts"
    python_dir.mkdir(parents=True)
    python_exe = python_dir / "python.exe"
    python_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(runcomfy_media, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COMFYUI_ACCELERATOR", "cpu")
    monkeypatch.delenv("COMFYUI_DIR", raising=False)
    monkeypatch.delenv("COMFYUI_LAUNCH_COMMAND", raising=False)

    spec = runcomfy_media._comfyui_launch_spec({"base_url": "http://127.0.0.1:8188"})

    assert spec
    assert spec["cwd"] == str(comfy_dir.resolve())
    assert spec["argv"][0] == str(python_exe)
    assert "--cpu" in spec["argv"]


def test_comfyui_auto_launch_does_not_start_remote_urls(monkeypatch, tmp_path):
    comfy_dir = tmp_path / "ComfyUI"
    comfy_dir.mkdir()
    (comfy_dir / "run_directml.bat").write_text("@echo off\n", encoding="utf-8")

    monkeypatch.setenv("COMFYUI_DIR", str(comfy_dir))
    monkeypatch.setenv("COMFYUI_ACCELERATOR", "amd")

    assert runcomfy_media._comfyui_launch_spec({"base_url": "http://comfy.local:8188"}) is None


@pytest.mark.asyncio
async def test_local_comfyui_generation_downloads_output(monkeypatch, tmp_path):
    captured = {}

    class FakeResponse:
        def __init__(self, status_code=200, payload=None, content=b"", headers=None, text=""):
            self.status_code = status_code
            self._payload = payload or {}
            self.content = content
            self.headers = headers or {}
            self.text = text
            self.is_success = 200 <= status_code < 300

        def json(self):
            return self._payload

        def raise_for_status(self):
            if not self.is_success:
                raise AssertionError(f"HTTP {self.status_code}")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None):
            captured.setdefault("get_urls", []).append(url)
            if url.endswith("/system_stats"):
                return FakeResponse(payload={"system": {}})
            if url.endswith("/object_info"):
                return FakeResponse(payload={
                    "CheckpointLoaderSimple": {
                        "input": {"required": {"ckpt_name": [["dream.safetensors"]]}}
                    },
                    "KSampler": {
                        "input": {
                            "required": {
                                "sampler_name": [["euler"]],
                                "scheduler": [["normal"]],
                            }
                        }
                    },
                })
            if "/history/" in url:
                return FakeResponse(payload={
                    "prompt-1": {
                        "status": {"status_str": "success"},
                        "outputs": {
                            "9": {
                                "images": [
                                    {"filename": "odysseus_00001_.png", "subfolder": "", "type": "output"}
                                ]
                            }
                        },
                    }
                })
            if url.endswith("/view"):
                captured["view_params"] = params
                return FakeResponse(content=b"fake-png", headers={"content-type": "image/png"})
            raise AssertionError(f"unexpected GET {url}")

        async def post(self, url, json=None, params=None):
            captured["post_url"] = url
            captured["workflow"] = json["prompt"]
            return FakeResponse(payload={"prompt_id": "prompt-1"})

    monkeypatch.setattr(runcomfy_media, "GENERATED_IMAGES_DIR", str(tmp_path))
    monkeypatch.setattr(runcomfy_media, "_save_gallery_row", lambda **kwargs: "img_1")
    monkeypatch.setattr(runcomfy_media.httpx, "AsyncClient", FakeClient)

    result = await runcomfy_media._generate_local_comfyui_media(
        "image",
        {"prompt": "a red chair", "timeout": 30},
        integration={"name": "ComfyUI Local", "preset": "comfyui_local", "base_url": "http://comfy.local"},
    )

    assert result["exit_code"] == 0
    assert result["media_provider"] == "comfyui_local"
    assert result["image_model"] == "comfyui-local:dream.safetensors"
    assert captured["post_url"] == "http://comfy.local/prompt"
    assert captured["workflow"]["4"]["inputs"]["ckpt_name"] == "dream.safetensors"
    saved_name = result["media_files"][0]["filename"]
    assert (tmp_path / saved_name).read_bytes() == b"fake-png"
