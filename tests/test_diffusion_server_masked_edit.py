"""A masked /v1/images/edits request is an inpaint, run off the event loop.

The gallery editor posts masked edits to the local diffusion server's
/v1/images/edits. The server used to ignore the mask and run the pipeline on
the whole image. A request with a mask now goes to inpaint_image with the
editor's strength and feather, on a worker thread, and its progress entry
ends as done or error.

Like test_diffusion_server_security.py, these tests AST-extract the route and
its helpers from diffusion_server.py instead of importing it, so torch and
diffusers stay out. Inference is mocked, so no GPU is needed.
"""

import ast
import asyncio
import base64
import io
import logging
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.testclient import TestClient
from pydantic import BaseModel

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "diffusion_server.py"

_NAMES = (
    "_PROGRESS",
    "_start_progress",
    "_update_progress",
    "_finish_progress",
    "_parse_size",
    "InpaintRequest",
    "_masked_edit",
    "edit_image",
)


class _Result:
    def __init__(self, image):
        self.images = [image]


@pytest.fixture()
def server():
    """A fresh app holding the real edit route, with inference replaced."""
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        name = getattr(node, "name", None)
        if name is None and isinstance(node, ast.Assign):
            name = getattr(node.targets[0], "id", None)
        if name in _NAMES:
            nodes.append(node)
    found = {getattr(n, "name", None) or n.targets[0].id for n in nodes}
    assert found == set(_NAMES), f"missing from diffusion_server.py: {set(_NAMES) - found}"

    calls = {"inpaint": [], "pipeline": []}
    ns = {
        "app": FastAPI(),
        "asyncio": asyncio,
        "base64": base64,
        "io": io,
        "time": time,
        "uuid": __import__("uuid"),
        "logger": logging.getLogger("test-diffusion"),
        "BaseModel": BaseModel,
        "File": File,
        "Form": Form,
        "HTTPException": HTTPException,
        "UploadFile": UploadFile,
        "_pipe": object(),
        "calls": calls,
    }

    def inpaint_image(req):
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        calls["inpaint"].append({"req": req, "on_loop": on_loop})
        return ns["inpaint_result"](req)

    def run_pipeline(pipe, request_id, total_steps, **kwargs):
        calls["pipeline"].append(kwargs)
        return _Result(kwargs["image"])

    ns.update(
        inpaint_image=inpaint_image,
        inpaint_result=lambda req: {"image": "SU5QQUlOVEVE", "elapsed": 0.1},
        _pipeline_accepts_arg=lambda name: name == "image",
        _quality_steps=lambda quality: 4,
        _default_negative_prompt=lambda: "",
        _pipeline_call_kwargs=lambda **kwargs: kwargs,
        _run_pipeline_with_progress=run_pipeline,
        _image_response=lambda images: {"data": [{"b64_json": "UNMASKED"}]},
    )
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(_SCRIPT), "exec"), ns)
    ns["client"] = TestClient(ns["app"], raise_server_exceptions=False)
    return ns


def _png():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (90, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _post(server, *, mask=True, **form):
    files = {"image": ("source.png", _png(), "image/png")}
    if mask:
        files["mask"] = ("mask.png", b"MASKBYTES", "image/png")
    data = {"prompt": "remove the lamp", "size": "512x384", "request_id": "edit-1", **form}
    return server["client"].post("/v1/images/edits", data=data, files=files)


def test_a_masked_edit_goes_to_inpaint_with_the_mask(server):
    resp = _post(server)

    assert resp.status_code == 200
    assert resp.json()["data"] == [{"b64_json": "SU5QQUlOVEVE"}]
    assert server["calls"]["pipeline"] == []
    (call,) = server["calls"]["inpaint"]
    req = call["req"]
    assert base64.b64decode(req.mask) == b"MASKBYTES"
    assert base64.b64decode(req.image) == _png()
    assert (req.prompt, req.width, req.height) == ("remove the lamp", 512, 384)


def test_the_editor_strength_and_feather_reach_inpaint(server):
    _post(server, strength="0.99", feather="20")

    req = server["calls"]["inpaint"][0]["req"]
    assert (req.strength, req.feather) == (0.99, 20)


def test_strength_and_feather_default_when_the_client_sends_none(server):
    _post(server)

    req = server["calls"]["inpaint"][0]["req"]
    assert (req.strength, req.feather) == (0.75, 8)


def test_inpaint_runs_off_the_event_loop(server):
    _post(server)

    # Called straight from the async handler, inpaint_image would find the
    # running event loop; on a worker thread there is none.
    assert server["calls"]["inpaint"][0]["on_loop"] is False


def test_a_successful_masked_edit_marks_progress_done(server):
    _post(server)

    assert server["_PROGRESS"]["edit-1"]["status"] == "done"


def test_an_unmasked_edit_still_uses_the_pipeline(server):
    resp = _post(server, mask=False)

    assert resp.status_code == 200
    assert resp.json()["data"] == [{"b64_json": "UNMASKED"}]
    assert server["calls"]["inpaint"] == []
    assert len(server["calls"]["pipeline"]) == 1


def test_an_inpaint_that_returns_no_image_is_a_500(server):
    server["inpaint_result"] = lambda req: {"error": "Model not loaded"}

    resp = _post(server)

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Model not loaded"
    assert server["_PROGRESS"]["edit-1"]["status"] == "error"
    assert server["_PROGRESS"]["edit-1"]["error"] == "Model not loaded"


def test_an_inpaint_that_raises_marks_progress_as_an_error(server):
    def boom(req):
        raise RuntimeError("CUDA out of memory")

    server["inpaint_result"] = boom

    resp = _post(server)

    assert resp.status_code == 500
    assert server["_PROGRESS"]["edit-1"]["status"] == "error"
    assert server["_PROGRESS"]["edit-1"]["error"] == "CUDA out of memory"
