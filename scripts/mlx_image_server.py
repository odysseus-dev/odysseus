#!/usr/bin/env python3
"""OpenAI-compatible image API wrapper for MLX image models.

This is intentionally small: it exposes the same `/v1/images/generations`
shape Odysseus already uses for local image endpoints, then delegates to the
MLX image CLI for the actual generation. Text MLX models still use
`mlx_lm.server`; image MLX models should use this wrapper.
"""

from __future__ import annotations

import argparse
import base64
import os
import shutil
import subprocess
import sys
import tempfile
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mlx_image_server")


class ImageRequest(BaseModel):
    model: str = ""
    prompt: str
    n: int = 1
    size: str = "1024x1024"
    quality: str = "medium"
    response_format: str = "b64_json"


class HarmonizeRequest(BaseModel):
    image: str
    prompt: str = ""
    mask: str | None = None
    body_mask: str | None = None
    seam_mask: str | None = None
    strength: float = 0.35


app = FastAPI(title="Odysseus MLX Image Server")
_args: argparse.Namespace


def _steps(quality: str) -> int:
    if _args.steps:
        return int(_args.steps)
    return {"low": 8, "medium": 20, "high": 32, "auto": 20}.get((quality or "medium").lower(), 20)


def _size(size: str) -> tuple[int, int]:
    try:
        w, h = str(size or "").lower().split("x", 1)
        return max(64, int(w)), max(64, int(h))
    except Exception:
        return int(_args.width), int(_args.height)


def _cli_for_model(model: str) -> str:
    lower = model.lower()
    if "qwen" in lower:
        return "mflux-generate-qwen"
    if "flux" in lower:
        return "mflux-generate"
    return "mflux-generate"


def _resolve_cli(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    local = Path(sys.executable).resolve().parent / name
    if local.exists():
        return str(local)
    prefix_local = Path(sys.prefix).resolve() / "bin" / name
    if prefix_local.exists():
        return str(prefix_local)
    return ""


def _valid_numbers(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values or []:
        s = str(value).strip()
        if not s:
            continue
        try:
            float(s)
        except Exception:
            continue
        out.append(s)
    return out


def _is_hidream(model: str) -> bool:
    return "hidream" in (model or "").lower()


def _is_boogu(model: str) -> bool:
    return "boogu" in (model or "").lower()


def _is_lama_inpaint(model: str) -> bool:
    lower = (model or "").lower()
    return "mi-gan" in lower or "migan" in lower or "lama" in lower


def _is_ddcolor(model: str) -> bool:
    return "ddcolor" in (model or "").lower()


def _unsupported_swift_mlx_runtime(model: str) -> HTTPException:
    if _is_ddcolor(model):
        return HTTPException(
            503,
            "DDColor MLX models require an Odysseus-compatible mlx-ddcolor-swift bridge. "
            "Build/install a bridge binary named odysseus-mlx-colorize or mlx-ddcolor-serve "
            "on the Apple Silicon host PATH. Upstream currently ships Swift libraries and "
            "smoke executables, not a stable colorize CLI.",
        )
    return HTTPException(
        503,
        "LaMa / MI-GAN MLX inpainting models require an Odysseus-compatible mlx-lama-swift bridge. "
        "Build/install a bridge binary named odysseus-mlx-inpaint or mlx-lama-serve "
        "on the Apple Silicon host PATH. Upstream currently ships Swift libraries and "
        "smoke executables, not a stable image-edit CLI.",
    )


def _resolve_bridge(names: list[str]) -> str:
    for name in names:
        found = _resolve_cli(name)
        if found:
            return found
    return ""


def _snapshot_path(model: str) -> Path:
    p = Path(model).expanduser()
    if p.exists():
        return p
    try:
        from huggingface_hub import snapshot_download
    except Exception as e:
        raise HTTPException(
            503,
            "huggingface_hub is required to download MLX image model snapshots. "
            "Install the model requirements in the selected Python environment.",
        ) from e
    return Path(snapshot_download(model))


def _weights_path(model: str) -> Path:
    p = Path(model).expanduser()
    if p.is_file():
        return p
    snap = _snapshot_path(model)
    if snap.is_file():
        return snap
    candidates = sorted(snap.rglob("*.safetensors"))
    if not candidates:
        raise HTTPException(500, f"No safetensors weights found for {model} in {snap}")
    return candidates[0]


def _write_bridge_input_image(raw: bytes, out_path: Path) -> None:
    try:
        from PIL import Image
        import io
    except Exception as e:
        raise HTTPException(503, "Pillow is required for MLX image edit bridge inputs.") from e
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        img.save(out_path, format="PNG")
    except Exception as e:
        raise HTTPException(400, f"Invalid input image: {e}") from e


def _write_bridge_mask(raw: bytes, out_path: Path) -> None:
    try:
        from PIL import Image
        import io
    except Exception as e:
        raise HTTPException(503, "Pillow is required for MLX image edit bridge masks.") from e
    try:
        img = Image.open(io.BytesIO(raw))
        if img.mode == "RGBA":
            # OpenAI edits mask convention: transparent = regenerate.
            alpha = img.getchannel("A")
            mask = alpha.point(lambda p: 255 if p < 128 else 0)
        else:
            mask = img.convert("L")
        mask.save(out_path, format="PNG")
    except Exception as e:
        raise HTTPException(400, f"Invalid mask image: {e}") from e


def _run_bridge(cmd: list[str]) -> None:
    env = os.environ.copy()
    proc = subprocess.run(cmd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "MLX Swift bridge failed").strip()
        logger.error("MLX Swift bridge failed (%s): %s\n%s", proc.returncode, " ".join(cmd), detail[-4000:])
        raise HTTPException(500, detail[-4000:])


def _run_ddcolor_bridge(model: str, image_raw: bytes, out_path: Path) -> None:
    bridge = _resolve_bridge(["odysseus-mlx-colorize", "mlx-ddcolor-serve"])
    if not bridge:
        raise _unsupported_swift_mlx_runtime(model)
    with tempfile.TemporaryDirectory(prefix="odysseus-ddcolor-") as td:
        inp = Path(td) / "input.png"
        _write_bridge_input_image(image_raw, inp)
        weights = _weights_path(model)
        tier = "tiny" if "tiny" in model.lower() else "large"
        _run_bridge([
            bridge,
            "--model", str(weights),
            "--image", str(inp),
            "--output", str(out_path),
            "--tier", tier,
        ])


def _run_inpaint_bridge(model: str, image_raw: bytes, mask_raw: bytes | None, out_path: Path) -> None:
    if not mask_raw:
        raise HTTPException(
            422,
            "LaMa / MI-GAN inpainting requires an image mask. Use the editor inpaint/object-removal tool so Odysseus can send the mask.",
        )
    bridge = _resolve_bridge(["odysseus-mlx-inpaint", "mlx-lama-serve"])
    if not bridge:
        raise _unsupported_swift_mlx_runtime(model)
    with tempfile.TemporaryDirectory(prefix="odysseus-mlx-inpaint-") as td:
        inp = Path(td) / "input.png"
        mask = Path(td) / "mask.png"
        _write_bridge_input_image(image_raw, inp)
        _write_bridge_mask(mask_raw, mask)
        weights = _weights_path(model)
        mode = "fast" if ("mi-gan" in model.lower() or "migan" in model.lower()) else "best"
        _run_bridge([
            bridge,
            "--model", str(weights),
            "--image", str(inp),
            "--mask", str(mask),
            "--output", str(out_path),
            "--mode", mode,
        ])


def _generate_hidream(model: str, prompt: str, out_path: Path, width: int, height: int, steps: int) -> None:
    model_path = _snapshot_path(model)
    script = model_path / "scripts" / "hidream_o1" / "generate_hidream_o1_mlx.py"
    if not script.exists():
        raise HTTPException(500, f"HiDream generator script not found in snapshot: {script}")
    cmd = [
        sys.executable,
        str(script),
        "--model-path",
        str(model_path),
        "--prompt",
        prompt,
        "--output",
        str(out_path),
        "--width",
        str(width),
        "--height",
        str(height),
        "--num-inference-steps",
        str(steps),
        "--no-snap-resolution",
    ]
    env = os.environ.copy()
    proc = subprocess.run(cmd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "HiDream generator failed").strip()
        raise HTTPException(500, detail[-4000:])


def _generate_boogu(model: str, prompt: str, out_path: Path, width: int, height: int, steps: int) -> None:
    try:
        from boogu_image_mlx.pipeline_mlx import BooguImagePipeline
        from PIL import Image
    except Exception as e:
        raise HTTPException(
            503,
            "Boogu MLX serving requires boogu-image-mlx in the launch Python. "
            "Install with: python -m pip install -U git+https://github.com/xocialize/boogu-image-mlx.git",
        ) from e

    model_path = _snapshot_path(model)
    vlm_model = (_args.vlm_model or os.environ.get("ODYSSEUS_MLX_IMAGE_VLM_MODEL") or "").strip()
    if not vlm_model:
        raise HTTPException(
            422,
            "This MLX image pipeline requires a companion vision-language model. "
            "Relaunch with --vlm-model <repo_or_path> or set ODYSSEUS_MLX_IMAGE_VLM_MODEL.",
        )
    try:
        pipe = BooguImagePipeline.from_pretrained(
            str(model_path),
            vlm_model,
        )
        img = pipe.generate(
            prompt,
            height=height,
            width=width,
            steps=steps,
            guidance=3.5,
        )
        Image.fromarray(img).save(out_path)
    except Exception as e:
        raise HTTPException(500, f"Boogu MLX generation failed: {e}") from e


@app.get("/v1/models")
def list_models():
    return {"data": [{"id": _args.model, "object": "model", "owned_by": "local"}]}


@app.post("/v1/images/generations")
def generate(req: ImageRequest):
    # The served model is the one this process was launched with. `req.model`
    # is accepted for OpenAI wire compatibility and ignored, matching
    # scripts/diffusion_server.py: honouring it would let a caller point the
    # generator at any local directory or Hugging Face repo, and the HiDream
    # branch runs a python script from inside that directory.
    model = _args.model
    width, height = _size(req.size)
    out_images = []
    count = max(1, min(int(req.n or 1), 4))
    for _ in range(count):
        with tempfile.TemporaryDirectory(prefix="odysseus-mlx-image-") as td:
            out_path = Path(td) / "image.png"
            if _is_hidream(model):
                _generate_hidream(model, req.prompt, out_path, width, height, _steps(req.quality))
            elif _is_boogu(model):
                _generate_boogu(model, req.prompt, out_path, width, height, _steps(req.quality))
            elif _is_lama_inpaint(model) or _is_ddcolor(model):
                raise _unsupported_swift_mlx_runtime(model)
            else:
                cli = _cli_for_model(model)
                cli_path = _resolve_cli(cli)
                if not cli_path:
                    raise HTTPException(
                        503,
                        f"{cli} not found in PATH or next to {sys.executable}. Install the MLX image runtime with: python3 -m pip install -U mflux",
                    )
                cmd = [
                    cli_path,
                    "--model",
                    model,
                    "--prompt",
                    req.prompt,
                    "--steps",
                    str(_steps(req.quality)),
                    "--output",
                    str(out_path),
                ]
                if _args.base_model:
                    cmd += ["--base-model", _args.base_model]
                if _args.lora_style:
                    cmd += ["--lora-style", _args.lora_style]
                if _args.lora_paths:
                    cmd += ["--lora-paths", *_args.lora_paths]
                lora_scales = _valid_numbers(_args.lora_scales)
                if lora_scales:
                    cmd += ["--lora-scales", *lora_scales]
                if "qwen" not in model.lower():
                    cmd += ["--width", str(width), "--height", str(height)]
                env = os.environ.copy()
                proc = subprocess.run(cmd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if proc.returncode != 0:
                    detail = (proc.stderr or proc.stdout or f"{cli} failed").strip()
                    logger.error("MLX image command failed (%s): %s\n%s", proc.returncode, " ".join(cmd), detail[-4000:])
                    raise HTTPException(500, detail[-4000:])
            if not out_path.exists():
                raise HTTPException(500, f"MLX image generator completed but did not write {out_path}")
            b64 = base64.b64encode(out_path.read_bytes()).decode("ascii")
            out_images.append({"b64_json": b64})
    return {"created": 0, "data": out_images}


@app.post("/v1/images/edits")
async def edit_image(
    image: UploadFile = File(...),
    mask: UploadFile | None = File(None),
    prompt: str = Form(""),
    model: str = Form(""),
    n: int = Form(1),
    size: str = Form("1024x1024"),
    response_format: str = Form("b64_json"),
):
    active_model = _args.model  # pinned; see generate()
    if _is_lama_inpaint(active_model) or _is_ddcolor(active_model):
        image_raw = await image.read()
        mask_raw = await mask.read() if mask is not None else None
        out_images = []
        count = max(1, min(int(n or 1), 4))
        for _ in range(count):
            with tempfile.TemporaryDirectory(prefix="odysseus-mlx-edit-") as td:
                out_path = Path(td) / "image.png"
                if _is_ddcolor(active_model):
                    _run_ddcolor_bridge(active_model, image_raw, out_path)
                else:
                    _run_inpaint_bridge(active_model, image_raw, mask_raw, out_path)
                if not out_path.exists():
                    raise HTTPException(500, f"MLX Swift bridge completed but did not write {out_path}")
                out_images.append({"b64_json": base64.b64encode(out_path.read_bytes()).decode("ascii")})
        return {"created": 0, "data": out_images}
    raise HTTPException(
        422,
        "This MLX image endpoint supports text-to-image generation only. "
        "Use /v1/images/generations, or serve an edit/img2img-capable model.",
    )


@app.post("/v1/images/harmonize")
def harmonize_image(req: HarmonizeRequest):
    active_model = _args.model
    if _is_lama_inpaint(active_model) or _is_ddcolor(active_model):
        try:
            image_raw = base64.b64decode(req.image.split(",", 1)[-1])
            mask_b64 = req.body_mask or req.mask
            mask_raw = base64.b64decode(mask_b64.split(",", 1)[-1]) if mask_b64 else None
        except Exception as e:
            raise HTTPException(400, f"Invalid base64 image payload: {e}") from e
        with tempfile.TemporaryDirectory(prefix="odysseus-mlx-harmonize-") as td:
            out_path = Path(td) / "image.png"
            if _is_ddcolor(active_model):
                _run_ddcolor_bridge(active_model, image_raw, out_path)
            else:
                _run_inpaint_bridge(active_model, image_raw, mask_raw, out_path)
            if not out_path.exists():
                raise HTTPException(500, f"MLX Swift bridge completed but did not write {out_path}")
            return {"image": base64.b64encode(out_path.read_bytes()).decode("ascii")}
    raise HTTPException(
        422,
        "This MLX image endpoint supports text-to-image generation only. "
        "Use /v1/images/generations, or serve an edit/img2img-capable model.",
    )


def main() -> None:
    global _args
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--steps", type=int, default=0)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--base-model", default="")
    parser.add_argument("--lora-style", default="")
    parser.add_argument("--lora-paths", nargs="*", default=[])
    parser.add_argument("--lora-scales", nargs="*", default=[])
    parser.add_argument("--vlm-model", default="")
    _args = parser.parse_args()
    uvicorn.run(app, host=_args.host, port=_args.port)


if __name__ == "__main__":
    main()
