"""HTTP install endpoint, driven end to end in an isolated lab.

The real route function runs with a temp runtime root, a stubbed release API and a local
HTTP server standing in for the release host. No network, no production data.
"""
import hashlib
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import fastapi
import pytest
from starlette.requests import Request

import routes.shell_routes as shell_routes
from src import llama_runtime as lr

TAG = "b9"
MAIN = "llama-b9-bin-win-cuda-13.5-x64.zip"
DEP = "cudart-llama-bin-win-cuda-13.5-x64.zip"
BASE = "https://github.com/ggml-org/llama.cpp/releases/download/b9/"


def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


MAIN_BYTES = _zip({"llama-server.exe": "#!/usr/bin/env bash\necho 'llama-server (fake) b9'\n",
                   "ggml-cuda.dll": b"MZ\0cudart64_13.dll\0cublas64_13.dll\0"})
DEP_BYTES = _zip({"cudart64_13.dll": b"c" * 4096, "cublas64_13.dll": b"b" * 4096})


def _release(main_digest=None, dep_digest=None, with_dep=True):
    sha = lambda b: "sha256:" + hashlib.sha256(b).hexdigest()  # noqa: E731
    assets = [{"name": MAIN, "browser_download_url": BASE + MAIN, "size": len(MAIN_BYTES),
               "digest": main_digest if main_digest is not None else sha(MAIN_BYTES)}]
    if with_dep:
        assets.append({"name": DEP, "browser_download_url": BASE + DEP, "size": len(DEP_BYTES),
                       "digest": dep_digest if dep_digest is not None else sha(DEP_BYTES)})
    return {"tag_name": TAG, "assets": assets}


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    served = {BASE + MAIN: MAIN_BYTES, BASE + DEP: DEP_BYTES}
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            hits.append(self.path)
            body = served[BASE + self.path.lstrip("/")]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            for i in range(0, len(body), 512):  # small writes so a reader can stop mid-stream
                self.wfile.write(body[i:i + 512])

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    real_download = lr.download_verified
    monkeypatch.setattr(lr, "download_verified",
                        lambda url, *a, **k: real_download(f"http://127.0.0.1:{port}/{url.rsplit('/', 1)[1]}", *a, **k))
    monkeypatch.setattr(lr, "llama_runtime_root", lambda: tmp_path / "rt")
    monkeypatch.setattr(shell_routes, "_require_admin", lambda request: None)
    monkeypatch.setattr(shell_routes, "_reject_cross_site", lambda request: None)
    state = {"release": _release(), "hits": hits, "root": tmp_path / "rt"}
    monkeypatch.setattr(lr, "fetch_release_by_tag", lambda tag, timeout=30: state["release"])
    router = shell_routes.setup_shell_routes()
    state["endpoint"] = next(r.endpoint for r in router.routes
                             if getattr(r, "path", None) == "/api/cookbook/llama-runtime/install" and "POST" in r.methods)
    yield state
    httpd.shutdown()


def _request(payload):
    req = Request({"type": "http", "method": "POST", "path": "/x", "headers": [],
                   "app": SimpleNamespace(state=SimpleNamespace(auth_manager=SimpleNamespace()))})

    async def _json():
        return payload
    req.json = _json
    return req


async def _events(lab, *, stop_after=None):
    payload = {"variant": "cuda", "tag": TAG, "url": BASE + MAIN}
    response = await lab["endpoint"](_request(payload), payload)
    events = []
    async for chunk in response.body_iterator:
        events.append(json.loads(chunk.removeprefix("data: ").strip()))
        if stop_after and len(events) >= stop_after:
            await response.body_iterator.aclose()  # client disconnected
            break
    return events


@pytest.mark.asyncio
async def test_install_streams_progress_and_installs_complete_cuda_runtime(lab):
    events = await _events(lab)
    assert events[0]["detail"] == "resolving release"
    progress = [e for e in events if e.get("received")]
    assert {e["part"] for e in progress} == {1, 2} and all(e["parts"] == 2 for e in progress)
    done = events[-1]
    assert done["stream"] == "done" and done["info"]["tag"] == "b9-cuda-13.5"
    target = lab["root"] / "b9-cuda-13.5"
    assert (target / "cudart64_13.dll").is_file() and (target / "cublas64_13.dll").is_file()
    assert (lab["root"] / "_packages" / MAIN).is_file(), "verified archives are kept for offline reinstall"
    assert not list(lab["root"].glob("*.staging"))


@pytest.mark.asyncio
async def test_second_install_is_already_installed_and_downloads_nothing(lab):
    await _events(lab)
    first_hits = len(lab["hits"])
    events = await _events(lab)
    assert events[-1]["stream"] == "done" and events[-1]["info"]["already_installed"]
    assert len(lab["hits"]) == first_hits


@pytest.mark.asyncio
async def test_digest_mismatch_reports_error_and_installs_nothing(lab):
    lab["release"] = _release(main_digest="sha256:" + "0" * 64)
    events = await _events(lab)
    assert events[-1]["stream"] == "error" and "SHA-256 mismatch" in events[-1]["error"]
    assert not (lab["root"] / "b9-cuda-13.5").exists()
    assert not list((lab["root"] / "_packages").glob(MAIN + "*")), "a mismatching archive leaves nothing behind"


@pytest.mark.asyncio
async def test_missing_published_digest_is_refused_before_any_download(lab):
    lab["release"] = _release(main_digest="")
    events = await _events(lab)
    assert events[-1]["stream"] == "error" and "no SHA-256 digest" in events[-1]["error"]
    assert lab["hits"] == [] and not (lab["root"] / "b9-cuda-13.5").exists()


@pytest.mark.asyncio
async def test_missing_companion_is_refused_as_incomplete_not_silent_cpu(lab):
    lab["release"] = _release(with_dep=False)
    events = await _events(lab)
    assert events[-1]["stream"] == "error" and "Incomplete CUDA runtime" in events[-1]["error"]
    assert not (lab["root"] / "b9-cuda-13.5").exists()


@pytest.mark.asyncio
async def test_cancel_mid_download_installs_nothing_and_leaves_a_resumable_partial(lab):
    events = await _events(lab, stop_after=2)
    assert not any(e["stream"] in ("done", "error") for e in events)
    assert not (lab["root"] / "b9-cuda-13.5").exists()
    work = lab["root"] / "_packages"
    assert not list(work.glob("*.zip")), "an unfinished archive must never look complete"
    # A later run completes (resume or restart) and installs.
    events = await _events(lab)
    assert events[-1]["stream"] == "done"


@pytest.mark.asyncio
async def test_url_not_in_release_is_rejected(lab):
    payload = {"variant": "cuda", "tag": TAG, "url": BASE + "other.zip"}
    response = await lab["endpoint"](_request(payload), payload)
    events = [json.loads(c.removeprefix("data: ").strip()) async for c in response.body_iterator]
    assert events[-1]["stream"] == "error" and "not an asset" in events[-1]["error"]
    assert lab["hits"] == []


@pytest.mark.asyncio
async def test_local_archive_install_requires_matching_sha256(lab):
    archive = lab["root"] / "local.zip"
    lab["root"].mkdir(parents=True, exist_ok=True)
    archive.write_bytes(MAIN_BYTES)
    for payload in ({"tag": "b9", "archive_path": str(archive)},
                    {"tag": "b9", "archive_path": str(archive), "sha256": "0" * 64}):
        with pytest.raises(fastapi.HTTPException) as exc:
            await lab["endpoint"](_request(payload), payload)
        assert exc.value.status_code == 400
