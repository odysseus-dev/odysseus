"""Replay the retained Gemma failure through the real Windows status route."""
import json
import sys

import httpx
import pytest
from fastapi import FastAPI

import routes.cookbook_routes as routes

REPO = "google/gemma-3-1b-it-qat-q4_0-gguf"
SID = "cookbook-b167833a"
# Text from the retained 374-byte production log; no model/network required.
GEMMA_LOG = (
    "[odysseus] HF token: NOT SET — gated/private models will be denied. "
    "Add one in Odysseus Cookbook -> Settings -> HuggingFace Token.\n"
    "\rFetching 0 files: 0it [00:00, ?it/s]"
    "\rFetching 0 files: 0it [00:00, ?it/s]\r\n"
    "\x1b[32m✓ Downloaded\x1b[0m\r\n"
    "  path: D:\\odysseus-models\\hub\\models--google--gemma-3-1b-it-qat-q4_0-gguf"
    "\\snapshots\\d1be121d36172a4b0b964657e2ee859d61138593\r\n\nDOWNLOAD_OK\n"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", ["done", "running", "stopped", "cancel_requested"])
@pytest.mark.parametrize("generic_exit", [False, True])
@pytest.mark.parametrize("cached_size", [None, 0, 16])
async def test_windows_gemma_missing_quant_is_error_and_persists(
    monkeypatch, tmp_path, stored, generic_exit, cached_size,
):
    state_path = tmp_path / "cookbook_state.json"
    task = {
        "id": SID, "sessionId": SID, "name": "Gemma · Q4_K_M",
        "type": "download", "status": stored, "output": "", "platform": "windows",
        "payload": {"repo_id": REPO, "include": "*Q4_K_M*", "local_dir": str(tmp_path)},
    }
    snapshot = tmp_path / "hub" / ("models--" + REPO.replace("/", "--")) / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "unrelated-Q8_0.gguf").write_bytes(b"GGUF")
    if cached_size is not None:
        (snapshot / "model-Q4_K_M.gguf").write_bytes(b"x" * cached_size)
    expected = "stopped" if stored in {"stopped", "cancel_requested"} else ("completed" if cached_size else "error")
    state_path.write_text(json.dumps({"tasks": [task], "env": {"servers": []}}), encoding="utf-8")
    (tmp_path / f"{SID}.log").write_text(
        GEMMA_LOG + ("=== process exited with code 0 ===\n" if generic_exit else ""),
        encoding="utf-8",
    )
    monkeypatch.setattr(routes, "_COOKBOOK_STATE_PATH", state_path)
    monkeypatch.setattr(routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(routes, "IS_WINDOWS", True)
    monkeypatch.setattr(routes, "_local_probe_python", lambda: sys.executable)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/cookbook/tasks/status")
        assert response.status_code == 200, response.text
        result = response.json()["tasks"][0]
        assert result["status"] == expected, result
        if expected == "error":
            assert "No matching" in result["diagnosis"]["message"]
        assert "DOWNLOAD_OK" in result["output_tail"]
        assert json.loads(state_path.read_text(encoding="utf-8"))["tasks"][0]["status"] == expected
        # A stale browser must not replace the server's corrected error.
        task["status"] = "done"
        posted = await client.post("/api/cookbook/state", json={"tasks": [task], "env": {"servers": []}})
        assert posted.status_code == 200, posted.text
        assert json.loads(state_path.read_text(encoding="utf-8"))["tasks"][0]["status"] == expected


def test_completion_writer_cannot_overwrite_concurrent_cancel(monkeypatch, tmp_path):
    path = tmp_path / "cookbook_state.json"
    path.write_text(json.dumps({"tasks": [{"sessionId": SID, "status": "cancel_requested"}]}), encoding="utf-8")
    monkeypatch.setattr(routes, "_COOKBOOK_STATE_PATH", path)
    result = routes._set_task_status(SID, "completed", expected_status="running")
    assert not result["changed"]
    assert result["previous"] == "cancel_requested"
    assert json.loads(path.read_text(encoding="utf-8"))["tasks"][0]["status"] == "cancel_requested"

