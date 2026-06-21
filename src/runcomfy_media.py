"""RunComfy-backed media generation helpers.

The skills.sh RunComfy skills describe the model routing. This module gives
Odysseus a small executable bridge: run a selected RunComfy endpoint, copy the
downloaded artifact into the local generated-media folder, and return fields
the chat renderer can display inline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from src.constants import GENERATED_IMAGES_DIR


IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}
VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "m4v"}
AUDIO_EXTS = {"mp3", "wav", "ogg", "m4a", "flac", "aac", "webm"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS

DEFAULT_MODELS = {
    "image": "blackforestlabs/flux-2-klein/9b/text-to-image",
    "video": "kling/kling-3.0/standard/text-to-video",
    "music": "acestep-ai/ace-step-1.5/text-to-audio",
}

MODEL_ALIASES = {
    "happyhorse/happyhorse-1-0/text-to-video": "happyhorse/happyhorse-1.0/text-to-video",
    "happyhorse/happyhorse-1-0/image-to-video": "happyhorse/happyhorse-1.0/image-to-video",
    "wan-ai/wan-2-7/text-to-video": "wan-ai/wan-2.7/text-to-video",
}

PROFESSIONAL_IMAGE_SUFFIX = (
    "professional art direction, strong composition, controlled lighting, "
    "premium color grade, high-detail materials, production-ready finish"
)
PROFESSIONAL_VIDEO_SUFFIX = (
    "Clear subject motion, intentional camera direction, stable framing, "
    "cinematic lighting, polished edit rhythm, professional color grade."
)
PROFESSIONAL_AUDIO_TAGS = (
    "professionally arranged, professionally mixed and mastered, clear stereo image, "
    "balanced dynamics, polished production"
)

PREMIUM_WORDS = {
    "premium", "commercial", "campaign", "brand", "hero", "broadcast",
    "final", "polished", "professional", "studio", "advertising",
}
TYPOGRAPHY_WORDS = {"poster", "headline", "logo", "typography", "text", "sign", "ad", "label", "banner"}
PHOTO_WORDS = {"photo", "photoreal", "portrait", "product", "fashion", "lifestyle", "food", "interior", "realistic"}
LOOP_WORDS = {"loop", "seamless", "game loop", "background loop"}


def _parse_args(content: str, kind: str = "") -> Dict[str, Any]:
    raw = (content or "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {"prompt": raw}
    lines = raw.splitlines()
    args: Dict[str, Any] = {"prompt": lines[0].strip() if lines else ""}
    if len(lines) > 1 and lines[1].strip():
        args["model"] = lines[1].strip()
    if kind == "image":
        if len(lines) > 2 and lines[2].strip():
            args["size"] = lines[2].strip()
        if len(lines) > 3 and lines[3].strip():
            args["quality"] = lines[3].strip()
    elif kind == "music":
        if len(lines) > 2 and lines[2].strip():
            args["duration"] = lines[2].strip()
        if len(lines) > 3 and lines[3].strip():
            args["lyrics"] = "\n".join(lines[3:]).strip()
    elif len(lines) > 2 and lines[2].strip():
        args["duration"] = lines[2].strip()
    if kind not in {"image", "music"} and len(lines) > 3 and lines[3].strip():
        args["aspect_ratio"] = lines[3].strip()
    return args


def wants_runcomfy_media(content: str) -> bool:
    args = _parse_args(content)
    provider = str(args.get("provider") or args.get("backend") or args.get("service") or "").lower()
    model = str(args.get("model_id") or args.get("model") or "").lower()
    text = str(content or "").lower()
    if provider in {"runcomfy", "comfy", "comfyui", "comfyui-cloud", "comfyui cloud"}:
        return True
    if any(phrase in text for phrase in ("runcomfy", "run comfy", "comfyui", "comfy ui", "comfy cloud")):
        return True
    return any(model.startswith(prefix) for prefix in (
        "blackforestlabs/",
        "kling/",
        "acestep-ai/",
        "elevenlabs/",
        "wan-ai/",
        "happyhorse/",
        "openai/gpt-image",
    ))


def runcomfy_fallback_content(kind: str, content: str) -> str:
    """Drop local/provider-specific model hints before falling back to RunComfy."""
    if wants_runcomfy_media(content):
        return content
    args = _parse_args(content, kind=kind)
    if not args:
        return content
    keep_by_kind = {
        "image": {
            "prompt", "description", "size", "aspect_ratio", "quality",
            "style", "enhance_prompt", "raw_prompt", "seed", "steps",
            "width", "height",
        },
        "video": {
            "prompt", "description", "duration", "aspect_ratio", "quality",
            "camera", "camera_motion", "image_url", "image", "audio_url",
            "seed", "negative_prompt", "enhance_prompt", "raw_prompt", "timeout",
        },
        "music": {
            "prompt", "description", "tags", "lyrics", "duration", "seconds",
            "quality", "bpm", "mood", "loop", "force_instrumental", "audio",
            "start_time", "end_time", "extend_before_duration",
            "extend_after_duration", "enhance_prompt", "raw_prompt", "timeout",
        },
    }
    allowed = keep_by_kind.get(kind, {"prompt", "description", "quality"})
    cleaned = {key: value for key, value in args.items() if key in allowed and value not in (None, "")}
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else content


def _coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        iv = int(value)
    except (TypeError, ValueError):
        iv = default
    return max(minimum, min(maximum, iv))


def _coerce_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        fv = float(value)
    except (TypeError, ValueError):
        fv = default
    return max(minimum, min(maximum, fv))


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _quality(args: Dict[str, Any]) -> str:
    return str(args.get("quality") or args.get("tier") or "professional").strip().lower()


def _contains_any(text: str, words: set[str]) -> bool:
    haystack = str(text or "").lower()
    for word in words:
        needle = str(word or "").lower()
        if not needle:
            continue
        if len(needle) <= 3:
            if re.search(rf"\b{re.escape(needle)}\b", haystack):
                return True
        elif needle in haystack:
            return True
    return False


def _append_unique(text: str, additions: list[str], *, max_chars: int = 1800) -> str:
    base = str(text or "").strip()
    lower = base.lower()
    clean_additions = []
    for item in additions:
        item = str(item or "").strip().strip(".")
        if item and item.lower() not in lower:
            clean_additions.append(item)
    if clean_additions:
        base = base.rstrip(" .") + ". " + ", ".join(clean_additions) + "."
    return base[:max_chars].rstrip()


def _enhance_prompt_enabled(args: Dict[str, Any]) -> bool:
    return _coerce_bool(args.get("enhance_prompt"), True) and not _coerce_bool(args.get("raw_prompt"), False)


def _split_size(size: str, default: tuple[int, int] = (1024, 1024)) -> tuple[int, int]:
    raw = str(size or "").lower().replace("_", "x")
    if "x" not in raw:
        return default
    left, right = raw.split("x", 1)
    return (
        _coerce_int(left, default[0], 256, 2048),
        _coerce_int(right, default[1], 256, 2048),
    )


def _size_from_aspect(aspect_ratio: str, default: tuple[int, int] = (1024, 1024)) -> tuple[int, int]:
    ratio = str(aspect_ratio or "").strip()
    if ratio in {"16:9", "1.78", "1.777"}:
        return 1536, 864
    if ratio in {"9:16", "0.56", "0.5625"}:
        return 864, 1536
    if ratio in {"3:2", "1.5"}:
        return 1536, 1024
    if ratio in {"2:3", "0.67", "0.666"}:
        return 1024, 1536
    if ratio in {"21:9", "2.33", "2.333"}:
        return 1536, 658
    return default


def _gpt_image_size(size: str) -> str:
    raw = str(size or "").lower().replace("x", "_")
    if raw in {"1024_1024", "1024_1536", "1536_1024"}:
        return raw
    if raw in {"1024_1792", "1792_1024"}:
        return "1024_1536" if raw.startswith("1024") else "1536_1024"
    return "1024_1024"


def _normalize_aspect_ratio(value: Any) -> str:
    raw = str(value or "16:9").strip().lower()
    aliases = {
        "landscape": "16:9",
        "wide": "16:9",
        "horizontal": "16:9",
        "portrait": "9:16",
        "vertical": "9:16",
        "square": "1:1",
        "cinema": "21:9",
        "cinematic": "21:9",
        "anamorphic": "21:9",
    }
    return aliases.get(raw, raw if raw in {"16:9", "9:16", "1:1", "21:9", "4:5", "3:2", "2:3"} else "16:9")


def _normalize_resolution(value: Any, quality: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"720p", "1080p", "4k"}:
        return raw
    if quality in {"draft", "low", "fast", "cheap"}:
        return "720p"
    return "1080p"


def _normalize_model_id(model_id: str) -> str:
    raw = str(model_id or "").strip()
    return MODEL_ALIASES.get(raw, raw)


def _is_kling_3_model(model_id: str) -> bool:
    return _normalize_model_id(model_id).startswith("kling/kling-3.0/")


def _is_happyhorse_model(model_id: str) -> bool:
    return _normalize_model_id(model_id).startswith("happyhorse/happyhorse-1.0/")


def _is_wan_27_model(model_id: str) -> bool:
    return _normalize_model_id(model_id).startswith("wan-ai/wan-2.7/")


def _kling_aspect_ratio(value: Any) -> str:
    ratio = _normalize_aspect_ratio(value)
    if ratio in {"16:9", "9:16", "1:1"}:
        return ratio
    if ratio in {"4:5", "3:4", "2:3"}:
        return "9:16"
    return "16:9"


def _wan_or_happyhorse_aspect_ratio(value: Any) -> str:
    raw = str(value or "16:9").strip().lower()
    aliases = {
        "landscape": "16:9",
        "wide": "16:9",
        "horizontal": "16:9",
        "portrait": "9:16",
        "vertical": "9:16",
        "square": "1:1",
    }
    ratio = aliases.get(raw, raw)
    return ratio if ratio in {"16:9", "9:16", "1:1", "4:3", "3:4"} else "16:9"


def _wants_video_sound(prompt: str, args: Dict[str, Any]) -> bool:
    for key in ("sound", "generate_audio", "with_audio"):
        if args.get(key) is not None:
            return _coerce_bool(args.get(key), False)
    lower = str(prompt or "").lower()
    if any(phrase in lower for phrase in ("no sound", "no audio", "silent", "without audio", "without sound")):
        return False
    return any(
        phrase in lower
        for phrase in (
            "with sound",
            "with audio",
            "dialogue",
            "voiceover",
            "voice over",
            "speaking",
            "talking",
            "ambient sound",
            "foley",
            "lip sync",
            "lipsync",
        )
    )


def _runcomfy_executable() -> Optional[str]:
    return shutil.which("runcomfy") or shutil.which("runcomfy.cmd")


def _subprocess_command(cmd: list[str]) -> list[str]:
    if not cmd:
        return cmd
    launcher = str(cmd[0]).lower()
    if launcher.endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", subprocess.list2cmdline(cmd)]
    return cmd


def _run_checked(cmd: list[str], timeout: int, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        _subprocess_command(cmd),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _auth_error_message(output: str = "") -> str:
    extra = (output or "").strip()
    if extra:
        extra = "\n\nRunComfy said:\n" + extra[:800]
    return (
        "RunComfy is installed, but it is not signed in. Create/sign in to a "
        "RunComfy account, then run `runcomfy login` once, or set "
        "`RUNCOMFY_TOKEN` from your RunComfy profile. Restart Odysseus after "
        "setting a token so the app process can see it."
        + extra
    )


def _check_runcomfy_ready(exe: str) -> Optional[Dict[str, Any]]:
    try:
        proc = _run_checked([exe, "whoami"], timeout=20)
    except FileNotFoundError:
        return {"error": "RunComfy CLI is not installed or not on PATH.", "exit_code": 1}
    except subprocess.TimeoutExpired:
        return {"error": "RunComfy CLI did not answer `whoami` within 20s.", "exit_code": 1}
    output = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
    if proc.returncode == 0:
        return None
    return {"error": _auth_error_message(output), "exit_code": proc.returncode or 1}


def _select_model(kind: str, args: Dict[str, Any], default_model: Optional[str]) -> str:
    explicit = str(args.get("model_id") or args.get("model") or "").strip()
    if explicit:
        return _normalize_model_id(explicit)
    if default_model:
        return _normalize_model_id(default_model)

    promptish = " ".join(str(args.get(key) or "") for key in ("prompt", "tags", "lyrics", "description"))
    quality = _quality(args)

    if kind == "image":
        if _contains_any(promptish, TYPOGRAPHY_WORDS):
            return "openai/gpt-image-2/text-to-image"
        return DEFAULT_MODELS["image"]

    if kind == "video":
        has_image = bool(args.get("image_url") or args.get("image"))
        has_audio = bool(args.get("audio_url"))
        if has_audio and not has_image:
            return "wan-ai/wan-2.7/text-to-video"
        if has_image:
            if quality in {"4k"} or "4k" in promptish.lower():
                return "kling/kling-3.0/4k/image-to-video"
            if quality in {"premium", "hero"} or "physics" in promptish.lower() or "product spin" in promptish.lower():
                return "kling/kling-3.0/pro/image-to-video"
            return "kling/kling-3.0/standard/image-to-video"
        if quality in {"4k"} or "4k" in promptish.lower():
            return "kling/kling-3.0/4k/text-to-video"
        if quality in {"premium", "hero"}:
            return "kling/kling-3.0/pro/text-to-video"
        return DEFAULT_MODELS["video"]

    if kind == "music":
        if args.get("audio"):
            if args.get("extend_before_duration") is not None or args.get("extend_after_duration") is not None:
                return "acestep-ai/ace-step/audio-outpaint"
            return "acestep-ai/ace-step/audio-inpaint"
        if quality in {"premium", "commercial", "hero", "broadcast"}:
            return "elevenlabs/elevenlabs/music-generation"
        if quality in {"draft", "low", "cheap", "cost", "cost-sensitive", "batch"}:
            return "acestep-ai/ace-step/text-to-audio"
        return DEFAULT_MODELS["music"]

    return DEFAULT_MODELS.get(kind, "")


def _professional_image_prompt(prompt: str, args: Dict[str, Any], model_id: str) -> str:
    if not _enhance_prompt_enabled(args):
        return prompt
    additions: list[str] = []
    style = str(args.get("style") or "").strip()
    if style:
        additions.append(style)
    if _contains_any(prompt, TYPOGRAPHY_WORDS) or "gpt-image" in model_id:
        additions.extend([
            "clean professional layout",
            "precise readable typography when text is requested",
            "balanced whitespace",
            "brand-ready composition",
            "print-quality finish",
        ])
    elif _contains_any(prompt, PHOTO_WORDS):
        additions.extend([
            "editorial photography",
            "controlled studio lighting",
            "natural textures",
            "sharp subject separation",
            "premium color grade",
        ])
    else:
        additions.append(PROFESSIONAL_IMAGE_SUFFIX)
    return _append_unique(prompt, additions)


def _professional_video_prompt(prompt: str, args: Dict[str, Any], model_id: str) -> str:
    if not _enhance_prompt_enabled(args):
        return prompt
    additions = []
    camera = str(args.get("camera") or args.get("camera_motion") or "").strip()
    if camera:
        additions.append(f"Camera: {camera}")
    if "Audio:" not in prompt and "audio:" not in prompt and "happyhorse" in model_id:
        additions.append("Audio: subtle, professionally mixed ambience appropriate to the scene")
    additions.append(PROFESSIONAL_VIDEO_SUFFIX)
    return _append_unique(prompt, additions, max_chars=2200)


def _professional_audio_tags(tags: str, args: Dict[str, Any]) -> str:
    if not _enhance_prompt_enabled(args):
        return tags
    additions = [PROFESSIONAL_AUDIO_TAGS]
    if _contains_any(tags, LOOP_WORDS) or _coerce_bool(args.get("loop"), False):
        additions.append("seamless loop, consistent groove, clean loop point")
    if args.get("bpm"):
        additions.append(f"{args.get('bpm')} BPM")
    if args.get("mood"):
        additions.append(str(args["mood"]))
    return _append_unique(tags, additions, max_chars=1200)


def _professional_elevenlabs_prompt(prompt: str, args: Dict[str, Any]) -> str:
    if not _enhance_prompt_enabled(args):
        return prompt
    additions = [
        "premium commercial-ready production",
        "44.1 kHz stereo feel",
        "professional arrangement",
        "polished mix and master",
        "clear section structure",
    ]
    if _coerce_bool(args.get("force_instrumental"), False):
        additions.append("instrumental only, no vocals")
    if _contains_any(prompt, LOOP_WORDS) or _coerce_bool(args.get("loop"), False):
        additions.append("seamless loopable ending")
    return _append_unique(prompt, additions, max_chars=2200)


def _input_for_kind(kind: str, args: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    explicit = args.get("input") or args.get("body")
    if isinstance(explicit, dict):
        return dict(explicit)

    prompt = str(args.get("prompt") or args.get("description") or "").strip()
    if kind == "image":
        if not prompt:
            return {}
        prompt = _professional_image_prompt(prompt, args, model_id)
        if "gpt-image-2" in model_id:
            return {"prompt": prompt, "size": _gpt_image_size(str(args.get("size") or "1024_1024"))}
        if args.get("size"):
            width, height = _split_size(str(args.get("size") or "1024x1024"))
        else:
            width, height = _size_from_aspect(str(args.get("aspect_ratio") or ""), (1024, 1024))
        quality = _quality(args)
        body = {
            "prompt": prompt,
            "steps": _coerce_int(args.get("steps"), 8 if quality in {"draft", "low", "fast"} else 25, 1, 50),
            "width": _coerce_int(args.get("width"), width, 256, 2048),
            "height": _coerce_int(args.get("height"), height, 256, 2048),
        }
        for key in ("seed", "image", "image_url", "images", "reference_images"):
            if args.get(key) is not None:
                body[key] = args[key]
        return body

    if kind == "video":
        if not prompt:
            return {}
        quality = _quality(args)
        prompt = _professional_video_prompt(prompt, args, model_id)
        duration = _coerce_int(args.get("duration"), 5, 3, 15)
        if _is_kling_3_model(model_id):
            body = {
                "prompt": prompt,
                "duration": duration,
                "aspect_ratio": _kling_aspect_ratio(args.get("aspect_ratio")),
                "sound": _wants_video_sound(prompt, args),
            }
            if args.get("negative_prompt"):
                body["negative_prompt"] = str(args["negative_prompt"])
            if args.get("cfg_scale") is not None:
                body["cfg_scale"] = _coerce_float(args.get("cfg_scale"), 0.5, 0.0, 1.0)
            if args.get("multi_prompt") is not None:
                body["multi_prompt"] = args["multi_prompt"]
            if args.get("shot_type") in {"intelligent", "customize"}:
                body["shot_type"] = args["shot_type"]
            source_image = args.get("start_image_url") or args.get("image_url") or args.get("image")
            if source_image is not None:
                body["start_image_url"] = source_image
            if args.get("tail_image_url") is not None:
                body["tail_image_url"] = args["tail_image_url"]
            if args.get("seed") is not None:
                body["seed"] = args["seed"]
            return body
        if _is_happyhorse_model(model_id):
            body = {
                "prompt": prompt,
                "duration": duration,
                "aspect_ratio": _wan_or_happyhorse_aspect_ratio(args.get("aspect_ratio")),
                "resolution": _normalize_resolution(args.get("resolution"), quality).upper(),
                "watermark": _coerce_bool(args.get("watermark"), False),
            }
            if args.get("seed") is not None:
                body["seed"] = args["seed"]
            source_image = args.get("image_url") or args.get("image")
            if source_image is not None:
                body["image_url"] = source_image
            return body
        if _is_wan_27_model(model_id):
            body = {
                "prompt": prompt,
                "duration": duration,
                "aspect_ratio": _wan_or_happyhorse_aspect_ratio(args.get("aspect_ratio")),
                "resolution": _normalize_resolution(args.get("resolution"), quality),
                "enable_prompt_expansion": _coerce_bool(args.get("enable_prompt_expansion"), True),
            }
            if args.get("negative_prompt"):
                body["negative_prompt"] = str(args["negative_prompt"])
            if args.get("audio_url") is not None:
                body["audio_url"] = args["audio_url"]
            if args.get("seed") is not None:
                body["seed"] = args["seed"]
            return body
        body = {
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": _normalize_aspect_ratio(args.get("aspect_ratio")),
        }
        for key in ("image", "image_url", "audio_url", "video_url", "seed", "negative_prompt"):
            if args.get(key) is not None:
                body[key] = args[key]
        return body

    if kind == "music":
        duration = _coerce_int(args.get("duration") or args.get("seconds"), 60, 5, 300)
        if "elevenlabs/elevenlabs/music-generation" in model_id:
            eleven_prompt = str(args.get("prompt") or args.get("tags") or "").strip()
            lyrics = str(args.get("lyrics") or "").strip()
            if lyrics and lyrics not in eleven_prompt:
                eleven_prompt = (eleven_prompt + "\n" + lyrics).strip()
            if not eleven_prompt:
                return {}
            eleven_prompt = _professional_elevenlabs_prompt(eleven_prompt, args)
            return {
                "prompt": eleven_prompt,
                "music_length_ms": _coerce_int(args.get("music_length_ms"), duration * 1000, 5000, 300000),
            }
        tags = str(args.get("tags") or prompt).strip()
        if not tags:
            return {}
        tags = _professional_audio_tags(tags, args)
        lyrics = args.get("lyrics")
        if lyrics is None:
            lyrics = "[inst]" if args.get("force_instrumental", True) is not False else ""
        body = {
            "tags": tags,
            "duration": duration,
        }
        if lyrics:
            body["lyrics"] = str(lyrics)
        for key in ("audio", "start_time", "end_time", "extend_before_duration", "extend_after_duration"):
            if args.get(key) is not None:
                body[key] = args[key]
        return body

    if prompt:
        return {"prompt": prompt}
    return {}


def _media_type_for_path(path: Path, preferred_type: str = "") -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext == "webm" and preferred_type == "audio":
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "file"


def _image_dimensions(path: Path) -> tuple[Optional[int], Optional[int]]:
    try:
        from PIL import Image

        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return None, None


def _save_gallery_row(
    *,
    path: Path,
    filename: str,
    media_type: str,
    prompt: str,
    model_id: str,
    owner: Optional[str],
    session_id: Optional[str],
    args: Dict[str, Any],
) -> str:
    if media_type not in {"image", "video"}:
        return ""
    try:
        from src.database import GalleryImage, SessionLocal

        raw = path.read_bytes()
        width, height = _image_dimensions(path) if media_type == "image" else (None, None)
        size_label = args.get("size")
        if not size_label and width and height:
            size_label = f"{width}x{height}"
        elif not size_label and args.get("duration"):
            size_label = f"{args.get('duration')}s"
        row_id = str(uuid.uuid4())
        db = SessionLocal()
        try:
            db.add(GalleryImage(
                id=row_id,
                filename=filename,
                prompt=(prompt or f"Generated {media_type}")[:500],
                model=model_id,
                size=str(size_label or ""),
                quality=str(args.get("quality") or args.get("resolution") or ""),
                session_id=session_id,
                owner=owner,
                file_hash=hashlib.sha256(raw).hexdigest(),
                file_size=len(raw),
                width=width,
                height=height,
                tags=f"generated,{media_type}",
            ))
            db.commit()
            return row_id
        finally:
            db.close()
    except Exception:
        return ""


def _collect_outputs(
    out_dir: Path,
    *,
    kind: str,
    prompt: str,
    model_id: str,
    owner: Optional[str],
    session_id: Optional[str],
    args: Dict[str, Any],
) -> list[Dict[str, Any]]:
    generated_root = Path(GENERATED_IMAGES_DIR)
    generated_root.mkdir(parents=True, exist_ok=True)
    files: list[Dict[str, Any]] = []
    candidates: list[tuple[int, float, Path]] = []
    preferred_type = "audio" if kind == "music" else kind
    for candidate in out_dir.rglob("*"):
        if not candidate.is_file():
            continue
        ext = candidate.suffix.lower().lstrip(".")
        if ext not in MEDIA_EXTS:
            continue
        media_type = _media_type_for_path(candidate, preferred_type)
        priority = 0 if media_type == preferred_type else 1
        try:
            mtime = -candidate.stat().st_mtime
        except OSError:
            mtime = 0
        candidates.append((priority, mtime, candidate))

    for _, _, candidate in sorted(candidates):
        ext = candidate.suffix.lower().lstrip(".")
        media_type = _media_type_for_path(candidate, preferred_type)
        final_name = f"{uuid.uuid4().hex[:12]}.{ext}"
        final_path = generated_root / final_name
        shutil.copyfile(candidate, final_path)
        media_id = _save_gallery_row(
            path=final_path,
            filename=final_name,
            media_type=media_type,
            prompt=prompt,
            model_id=model_id,
            owner=owner,
            session_id=session_id,
            args=args,
        )
        files.append({
            "url": f"/api/generated-image/{final_name}",
            "id": media_id,
            "filename": final_name,
            "type": media_type,
            "kind": kind,
            "size_bytes": final_path.stat().st_size,
        })
    return files


def _failure_message(kind: str, model_id: str, code: int, output: str, body: Dict[str, Any]) -> str:
    base = f"RunComfy {kind} generation failed with exit code {code}."
    hint = ""
    if code == 64:
        hint = "The CLI rejected the command arguments. Verify the RunComfy CLI version and model id."
    elif code == 65:
        hint = "The model rejected the input schema. Use `runcomfy_media` with the exact JSON fields from the skill/model page, or remove unsupported optional fields."
    elif code == 69:
        hint = "The upstream model service returned an error. Retry or switch to a nearby model tier."
    elif code == 75:
        hint = "This is retryable: timeout, rate limit, or queue pressure. Try again with a shorter duration/lower resolution."
    elif code == 77:
        hint = "RunComfy auth failed. Run `runcomfy login` or set `RUNCOMFY_TOKEN`."
    request_preview = json.dumps({"model_id": model_id, "input": body}, ensure_ascii=False)[:1200]
    parts = [base]
    if hint:
        parts.append(hint)
    if output:
        parts.append(output[:2000])
    parts.append("Request preview:\n" + request_preview)
    return "\n\n".join(parts)


async def generate_runcomfy_media(
    kind: str,
    content: str,
    *,
    owner: Optional[str] = None,
    session_id: Optional[str] = None,
    default_model: Optional[str] = None,
) -> Dict[str, Any]:
    args = _parse_args(content, kind=kind)
    model_id = _select_model(kind, args, default_model).strip()
    if not model_id:
        return {"error": "RunComfy model_id is required.", "exit_code": 1}

    body = _input_for_kind(kind, args, model_id)
    if not body:
        return {"error": f"A prompt or input JSON body is required for {kind} generation.", "exit_code": 1}

    exe = _runcomfy_executable()
    if not exe:
        return {
            "error": (
                "RunComfy CLI is not installed or not on PATH. Install it with "
                "`npm i -g @runcomfy/cli`, then run `runcomfy login` once."
            ),
            "exit_code": 1,
        }

    ready_error = await asyncio.to_thread(_check_runcomfy_ready, exe)
    if ready_error:
        return ready_error

    prompt = str(body.get("prompt") or body.get("tags") or args.get("prompt") or f"Generated {kind}").strip()
    timeout = _coerce_int(args.get("timeout"), 1800 if kind in {"video", "music"} else 900, 60, 7200)

    generated_root = Path(GENERATED_IMAGES_DIR)
    generated_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="runcomfy_", dir=str(generated_root)) as tmp:
        tmp_dir = Path(tmp)
        input_path = tmp_dir / "input.json"
        input_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        cmd = [
            exe,
            "run",
            model_id,
            "--input-file",
            input_path.name,
            "--output-dir",
            ".",
        ]
        if args.get("output") == "json":
            cmd.extend(["--output", "json"])

        try:
            proc = await asyncio.to_thread(_run_checked, cmd, timeout, str(tmp_dir))
        except subprocess.TimeoutExpired:
            return {"error": f"RunComfy {kind} generation timed out after {timeout}s.", "exit_code": 1}
        except Exception as exc:
            return {"error": f"RunComfy execution failed: {exc}", "exit_code": 1}

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        combined = "\n".join(part for part in (stdout, stderr) if part).strip()
        if proc.returncode != 0:
            if proc.returncode == 77 or "not signed in" in combined.lower() or "login" in combined.lower():
                return {"error": _auth_error_message(combined), "exit_code": proc.returncode or 1}
            return {
                "error": _failure_message(kind, model_id, proc.returncode, combined, body),
                "exit_code": proc.returncode or 1,
            }

        files = _collect_outputs(
            tmp_dir,
            kind=kind,
            prompt=prompt,
            model_id=model_id,
            owner=owner,
            session_id=session_id,
            args=args,
        )

    if not files:
        return {
            "error": (
                f"RunComfy completed, but no downloaded media file was found in the output directory.\n"
                f"{combined[:2000]}"
            ),
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": 1,
        }

    first = files[0]
    label = first["type"]
    result: Dict[str, Any] = {
        "output": f"Generated {label} saved: {first['url']}",
        "media_url": first["url"],
        "media_id": first.get("id") or "",
        "media_type": first["type"],
        "media_prompt": prompt,
        "media_model": model_id,
        "media_size": str(args.get("size") or args.get("duration") or ""),
        "media_quality": _quality(args),
        "media_files": files,
        "exit_code": 0,
    }
    if first["type"] == "image":
        result.update({
            "image_url": first["url"],
            "image_id": first.get("id") or "",
            "image_prompt": prompt,
            "image_model": model_id,
            "image_size": str(args.get("size") or body.get("size") or (
                f"{body.get('width')}x{body.get('height')}" if body.get("width") and body.get("height") else ""
            )),
            "image_quality": _quality(args),
        })
    return result
