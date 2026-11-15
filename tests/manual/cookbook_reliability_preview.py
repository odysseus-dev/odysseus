"""Loopback-only Cookbook replay lab; never starts an inference runtime.

Run with the production venv interpreter from the independent checkout.
Uses real Cookbook routes/JS. The transfer writer is a labelled fixture, not
HuggingFace or a real model; the Gemma terminal log is replayed byte-for-byte.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / ".odysseus-dev" / "cookbook-preview"


def writer(sid, success):
    log = LAB / "logs" / f"{sid}.log"
    for pct in (5, 35, 70):
        log.write_text(f"[lab fixture — not a network download]\nDownloading fixture.gguf: {pct}% | {pct}/100 B\n", encoding="utf-8")
        time.sleep(7)
    if success:
        dest = LAB / "hf" / "hub" / "models--lab--cached-GGUF" / "snapshots" / "fixture"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "fixture-Q4_K_M.gguf").write_bytes(b"GGUF" + b"\0" * 12)
        log.write_text("[lab fixture — not a usable model]\nFetching 0 files: 0it\nDOWNLOAD_OK\n", encoding="utf-8")
    else:
        log.write_bytes((LAB / "gemma-retained.log").read_bytes())


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--writer":
    writer(sys.argv[2], sys.argv[3] == "success")
    raise SystemExit

LAB.mkdir(parents=True, exist_ok=True)
(LAB / "logs").mkdir(exist_ok=True)
os.environ.update({
    "AUTH_ENABLED": "false", "DATABASE_URL": "sqlite:///:memory:",
    "ODYSSEUS_DATA_DIR": str(LAB / "data"), "HF_HOME": str(LAB / "hf"),
    "HUGGINGFACE_HUB_CACHE": str(LAB / "hf" / "hub"),
    "HF_HUB_CACHE": str(LAB / "hf" / "hub"),
})
sys.path.insert(0, str(ROOT))
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import routes.cookbook_routes as routes

routes._COOKBOOK_STATE_PATH = LAB / "cookbook_state.json"
routes.TMUX_LOG_DIR = LAB / "logs"
if not routes._COOKBOOK_STATE_PATH.exists():
    routes._COOKBOOK_STATE_PATH.write_text(json.dumps({"tasks": [], "env": {
        "env": "none", "remoteHost": "", "hostPlatform": "windows", "modelPaths": [],
        "servers": [{"name": "Local", "host": "", "platform": "windows"}],
    }}), encoding="utf-8")
# Read only the named retained diagnostic log, never runner scripts or tokens.
retained = Path("C:/Users/Protagonist/AppData/Local/Temp/odysseus-tmux/cookbook-b167833a.log")
if not retained.is_file():
    raise RuntimeError("Retained Gemma log missing; do not substitute invented evidence")
(LAB / "gemma-retained.log").write_bytes(retained.read_bytes())
app = FastAPI()
router = routes.setup_cookbook_routes()
# Only read/state routes: installing, stopping or serving is impossible here.
allowed = {"/api/cookbook/state", "/api/cookbook/tasks/status", "/api/model/cached"}
router.routes[:] = [r for r in router.routes if r.path in allowed]
app.include_router(router)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(Path(__file__).with_suffix(".html"))


@app.post("/lab/replay/{kind}")
def replay(kind: str):
    if kind not in {"gemma", "success"}:
        raise HTTPException(400, "Unknown fixture")
    sid = "cookbook-b167833a" if kind == "gemma" else "cookbook-lab-success"
    log = LAB / "logs" / f"{sid}.log"
    log.write_text("[lab fixture — not a network download]\nStarting replay\n", encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(Path(__file__)), "--writer", sid, kind], cwd=ROOT)
    (LAB / "logs" / f"{sid}.pid").write_text(str(child.pid), encoding="utf-8")
    routes._record_local_pid_identity(sid, child.pid)
    repo = "google/gemma-3-1b-it-qat-q4_0-gguf" if kind == "gemma" else "lab/cached-GGUF"
    return {"session_id": sid, "repo_id": repo, "include": "*Q4_K_M*", "backend": "llamacpp", "local_dir": str(LAB / "hf"), "platform": "windows"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=7012)
