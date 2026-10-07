"""Native local-media inspection tools for the Odysseus agent loop."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_SVG_SUFFIXES = {".svg"}
_VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi"}
_PDF_SUFFIXES = {".pdf"}
_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".webm"}


def _video_encode_args(output_path: Path) -> list[str]:
    """Return codecs compatible with the requested video container."""
    if output_path.suffix.casefold() == ".webm":
        # WebM does not permit H.264/AAC, which are the defaults for MP4.
        return ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-c:a", "libopus"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac"]
_DEFAULT_MODEL_FRAME_DIMENSION = 512
_MIN_MODEL_FRAME_DIMENSION = 256
_MAX_MODEL_FRAME_DIMENSION = 1024
_MAX_INLINE_TRANSCRIPT_CHARS = 4000
_INSPECT_MEDIA_ARGUMENTS = {
    "path", "start", "end", "duration", "frames", "sampling",
    "max_dimension", "query", "page", "pages", "timestamp",
    "output_path", "speed", "segments", "exports", "caption", "crop",
    "timestamp_path",
    # Explicit compatibility inputs. Each is normalized or explained below.
    "export_path", "export", "observe", "time_range", "timing",
    "frames_per_page", "queries",
}
_WHISPER_MODELS: dict[str, object] = {}
_WHISPER_LANGUAGE_ALIASES = {
    "auto": "",
    "automatic": "",
    # Models often pass the language they should answer in rather than the
    # language actually spoken. Treat full language names as an auto-detect
    # hint; exact ISO codes remain an intentional decoder constraint.
    "english": "",
    "chinese": "",
    "mandarin": "",
    "cantonese": "",
    "spanish": "",
    "french": "",
    "german": "",
    "italian": "",
    "japanese": "",
    "korean": "",
    "portuguese": "",
    "russian": "",
    "arabic": "",
    "hindi": "",
}


def _parse_seconds(value, *, default: float) -> float:
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.casefold() in {"start", "begin", "beginning", "first"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    unit_match = re.fullmatch(
        r"(?i)(?:(\d+(?:\.\d+)?)h)?"
        r"(?:(\d+(?:\.\d+)?)m)?"
        r"(?:(\d+(?:\.\d+)?)s)?",
        text,
    )
    if unit_match and any(part is not None for part in unit_match.groups()):
        hours, minutes, seconds = (
            float(part or 0) for part in unit_match.groups()
        )
        return hours * 3600.0 + minutes * 60.0 + seconds
    parts = text.split(":")
    if len(parts) == 4 and all(re.fullmatch(r"\d+", part) for part in parts):
        hours, minutes, seconds = (int(part) for part in parts[:3])
        fraction = int(parts[3]) / (10 ** len(parts[3]))
        return hours * 3600.0 + minutes * 60.0 + seconds + fraction
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"invalid timestamp: {value}")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value}") from exc
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60.0 + number
    return seconds


def _parse_video_position(value, *, default: float, duration: float) -> float:
    """Parse an absolute timestamp or an explicit percentage of a video."""
    if isinstance(value, str):
        position = value.strip().casefold()
        if position in {"start", "begin", "beginning", "first"}:
            return 0.0
        if position in {"middle", "mid", "midpoint", "center", "centre"}:
            return duration / 2.0
        if position in {"end", "finish", "last"}:
            return duration
    if isinstance(value, str) and value.strip().endswith("%"):
        raw_percent = value.strip()[:-1].strip()
        try:
            percent = float(raw_percent)
        except ValueError as exc:
            raise ValueError(f"invalid video percentage: {value}") from exc
        if not math.isfinite(percent) or not 0.0 <= percent <= 100.0:
            raise ValueError("video percentage must be between 0% and 100%")
        return duration * percent / 100.0
    return _parse_seconds(value, default=default)


def _format_seconds(value: float) -> str:
    value = max(0.0, value)
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    seconds = value % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def _resolve_workspace_path(
    raw_path: str,
    *,
    must_exist: bool,
    field_name: str = "path",
    tool_name: str = "inspect_media",
) -> Path:
    # Import lazily: tool_execution loads this module through the dynamic tool
    # registry and also owns the request-scoped workspace context variable.
    from src.tool_execution import get_active_workspace

    workspace_raw = get_active_workspace()
    if not workspace_raw:
        raise ValueError(f"{tool_name} requires an active workspace")
    workspace = Path(workspace_raw).resolve()
    raw = str(raw_path or "").strip()
    if not raw:
        raise ValueError(f"{tool_name} requires {field_name}")
    if raw == "/workspace":
        candidate = workspace
    elif raw.startswith("/workspace/"):
        candidate = workspace / raw.removeprefix("/workspace/")
    else:
        path = Path(raw).expanduser()
        candidate = path if path.is_absolute() else workspace / path
        if path.is_absolute() and must_exist:
            # Native models occasionally preserve the workspace-relative
            # suffix but omit the virtual ``/workspace`` mount prefix. Repair
            # only an input that demonstrably exists inside this request's
            # workspace. Never apply this alias to output destinations, and
            # retain the normal confinement check below for symlinks.
            confined_alias = (workspace / str(path).lstrip("/")).resolve()
            try:
                confined_alias.relative_to(workspace)
            except ValueError:
                pass
            else:
                if confined_alias.is_file():
                    candidate = confined_alias
    resolved = candidate.resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(
            f"{tool_name} {field_name} must stay inside the active workspace"
        ) from exc
    from src.agent_runtime.resources import _control_plane_path
    if _control_plane_path(str(resolved)):
        raise ValueError(f"{tool_name} {field_name} addresses execution-control state")
    if must_exist and not resolved.is_file():
        raise FileNotFoundError(f"media file not found: {raw}")
    return resolved


def _resolve_media_path(raw_path: str, *, tool_name: str = "inspect_media") -> Path:
    return _resolve_workspace_path(
        raw_path,
        must_exist=True,
        tool_name=tool_name,
    )


def _run(command: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


async def _run_video_probe(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a cheap metadata probe with one retry for transient I/O stalls."""
    try:
        return await asyncio.to_thread(_run, command, 30)
    except subprocess.TimeoutExpired:
        return await asyncio.to_thread(_run, command, 60)


async def _scene_change_timestamps(
    ffmpeg: str,
    path: Path,
    *,
    start: float,
    end: float,
) -> list[float]:
    """Return bounded absolute timestamps immediately after visual cuts."""
    scan = await asyncio.to_thread(
        _run,
        [
            ffmpeg, "-hide_banner", "-loglevel", "info",
            "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
            "-i", str(path), "-vf",
            "scale=160:-1,select=gt(scene\\,0.1),showinfo",
            "-an", "-fps_mode", "vfr", "-f", "null", "-",
        ],
        120,
    )
    if scan.returncode != 0:
        return []
    timestamps: list[float] = []
    for match in re.finditer(r"\bpts_time:([0-9]+(?:\.[0-9]+)?)", scan.stderr):
        timestamp = min(end - 0.001, start + float(match.group(1)) + 0.05)
        if timestamp >= start and all(abs(timestamp - seen) >= 0.08 for seen in timestamps):
            timestamps.append(timestamp)
    return timestamps


async def _motion_score_timestamps(
    ffmpeg: str,
    path: Path,
    *,
    start: float,
    end: float,
) -> list[tuple[float, float]]:
    """Return absolute timestamps paired with low-resolution change scores."""
    scan = await asyncio.to_thread(
        _run,
        [
            ffmpeg, "-hide_banner", "-loglevel", "info",
            "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
            "-i", str(path), "-vf",
            "fps=2,scale=160:-1,scdet=t=0,metadata=print:key=lavfi.scd.score",
            "-an", "-f", "null", "-",
        ],
        120,
    )
    if scan.returncode != 0:
        return []
    scored: list[tuple[float, float]] = []
    current_time: float | None = None
    for line in scan.stderr.splitlines():
        if "Parsed_metadata" not in line:
            continue
        time_match = re.search(r"\bpts_time:([0-9]+(?:\.[0-9]+)?)", line)
        if time_match:
            current_time = float(time_match.group(1))
            continue
        score_match = re.search(r"\blavfi\.scd\.score=([0-9]+(?:\.[0-9]+)?)", line)
        if score_match and current_time is not None:
            timestamp = min(end - 0.001, start + current_time + 0.05)
            scored.append((timestamp, float(score_match.group(1))))
            current_time = None
    return scored


def _bounded_scene_sample(
    scene_timestamps: list[float],
    *,
    start: float,
    end: float,
    count: int,
) -> list[float]:
    """Select scene cuts across the range, filling gaps with uniform samples."""
    if len(scene_timestamps) > count:
        if count == 1:
            chosen = [scene_timestamps[len(scene_timestamps) // 2]]
        else:
            chosen = [
                scene_timestamps[round(index * (len(scene_timestamps) - 1) / (count - 1))]
                for index in range(count)
            ]
    else:
        chosen = list(scene_timestamps)
    uniform = [start + (end - start) * (index + 0.5) / count for index in range(count)]
    for timestamp in uniform:
        if len(chosen) >= count:
            break
        if all(abs(timestamp - seen) >= 0.08 for seen in chosen):
            chosen.append(timestamp)
    if len(chosen) < count:
        # Dense contact-sheet requests can legitimately ask for observations
        # closer than the ordinary 80 ms deduplication threshold. Fill any
        # remainder deterministically without duplicating an exact timestamp.
        dense = [
            start + (end - start) * (index + 0.5) / max(count * 4, 1)
            for index in range(count * 4)
        ]
        for timestamp in dense:
            if len(chosen) >= count:
                break
            if all(abs(timestamp - seen) >= 0.001 for seen in chosen):
                chosen.append(timestamp)
    return sorted(chosen)


def _bounded_motion_sample(
    scored_timestamps: list[tuple[float, float]],
    *,
    start: float,
    end: float,
    count: int,
) -> list[float]:
    """Rank visually active moments while preserving temporal diversity."""
    chosen: list[float] = []
    minimum_spacing = max(0.5, (end - start) / max(1, count * 4))
    for timestamp, _score in sorted(scored_timestamps, key=lambda item: item[1], reverse=True):
        if all(abs(timestamp - seen) >= minimum_spacing for seen in chosen):
            chosen.append(timestamp)
        if len(chosen) >= count:
            break
    uniform = [start + (end - start) * (index + 0.5) / count for index in range(count)]
    for timestamp in uniform:
        if len(chosen) >= count:
            break
        if all(abs(timestamp - seen) >= 0.08 for seen in chosen):
            chosen.append(timestamp)
    if len(chosen) < count:
        dense = [
            start + (end - start) * (index + 0.5) / max(count * 4, 1)
            for index in range(count * 4)
        ]
        for timestamp in dense:
            if len(chosen) >= count:
                break
            if all(abs(timestamp - seen) >= 0.001 for seen in chosen):
                chosen.append(timestamp)
    return sorted(chosen)


def _pack_overview_contact_sheets(
    images: list[dict[str, str]], timestamps: list[float]
) -> list[dict[str, str]]:
    """Pack many timestamped observations into bounded row-major images."""
    from PIL import Image, ImageDraw, ImageFont

    sheets: list[dict[str, str]] = []
    columns = 4
    cell_width = 256
    cell_height = 168
    per_sheet = 8
    font = ImageFont.load_default()
    for offset in range(0, len(images), per_sheet):
        chunk = images[offset:offset + per_sheet]
        rows = max(1, math.ceil(len(chunk) / columns))
        sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "#101418")
        draw = ImageDraw.Draw(sheet)
        for local_index, item in enumerate(chunk):
            with Image.open(io.BytesIO(base64.b64decode(item["data"]))) as source:
                frame = source.convert("RGB")
                frame.thumbnail((240, 135))
            column = local_index % columns
            row = local_index // columns
            x = column * cell_width + (cell_width - frame.width) // 2
            y = row * cell_height + 6
            sheet.paste(frame, (x, y))
            absolute_index = offset + local_index
            draw.text(
                (column * cell_width + 8, row * cell_height + 145),
                f"{absolute_index + 1}. {_format_seconds(timestamps[absolute_index])}",
                fill="white",
                font=font,
            )
        buffer = io.BytesIO()
        sheet.save(buffer, "JPEG", quality=86)
        sheets.append({
            "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
            "mimeType": "image/jpeg",
        })
    return sheets


def _model_frame_dimension(args: dict) -> int:
    """Return the bounded size of images sent back to the vision model.

    Saved stills and clips retain their source quality.  This limit only
    applies to transient previews returned in the tool result, where large
    video frames can dominate the model context and make otherwise simple
    media tasks several minutes slower.
    """
    raw_dimension = args.get("max_dimension")
    if raw_dimension in (None, ""):
        return _DEFAULT_MODEL_FRAME_DIMENSION
    try:
        dimension = int(raw_dimension)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_dimension must be an integer between 256 and 1024") from exc
    if not _MIN_MODEL_FRAME_DIMENSION <= dimension <= _MAX_MODEL_FRAME_DIMENSION:
        raise ValueError("max_dimension must be an integer between 256 and 1024")
    return dimension


class ExtractTextTool:
    """Extract exact visible text and pixel coordinates with local OCR."""

    async def execute(self, content: str, _ctx: dict) -> dict:
        try:
            args = json.loads(content or "{}")
        except json.JSONDecodeError as exc:
            return {"error": f"extract_text arguments must be JSON: {exc}", "exit_code": 1}
        if not isinstance(args, dict):
            return {"error": "extract_text arguments must be a JSON object", "exit_code": 1}
        unknown = sorted(set(args) - {"path", "include_layout", "mode", "min_confidence", "max_results"})
        if unknown:
            return {"error": "extract_text unknown argument(s): " + ", ".join(unknown), "exit_code": 1}
        try:
            raw_path = str(args.get("path") or '')
            # Some native-schema models serialize a workspace path using the
            # same URI shape as uploads.  This alias grants no extra access:
            # convert it back to /workspace and let the normal confinement
            # resolver enforce the active root.
            if raw_path.startswith('odysseus://workspace/'):
                raw_path = '/workspace/' + raw_path[len('odysseus://workspace/'):]
            if raw_path.startswith('odysseus://'):
                # Upload access is independent of a filesystem workspace and
                # must never inherit an administrator's cross-owner override.
                from src.tool_utils import get_upload_handler
                ref = re.fullmatch(r'odysseus://attachment/([A-Za-z0-9_-]+(?:\.[A-Za-z0-9]+)?)', raw_path)
                owner = (_ctx or {}).get('owner')
                from src.agent_runtime.owned_resources import bound_attachment_path
                bound_path = bound_attachment_path(owner, raw_path)
                handler = get_upload_handler()
                info = {"path": bound_path} if bound_path else handler.resolve_upload(ref[1], owner=owner, allow_admin=False) if ref and owner and handler else None
                if not info or not info.get('path'):
                    raise ValueError('Uploaded image not found or not accessible to this user')
                path = Path(info['path'])
                if not path.is_file():
                    raise ValueError('Uploaded image is no longer available')
            else:
                path = _resolve_media_path(raw_path, tool_name="extract_text")
        except ValueError as exc:
            return {"error": str(exc), "exit_code": 1}
        suffix = path.suffix.casefold()
        if suffix not in _IMAGE_SUFFIXES | _PDF_SUFFIXES:
            return {"error": "extract_text supports local image and PDF files", "exit_code": 1}
        mode = str(args.get("mode") or "all").strip().casefold()
        try:
            minimum, maximum = float(args.get("min_confidence", .5)), int(args.get("max_results", 512))
        except (TypeError, ValueError):
            return {"error": "extract_text confidence/result limits must be numeric", "exit_code": 1}
        if mode not in {"all", "numbers"} or not 0 <= minimum <= 1 or not 1 <= maximum <= 512:
            return {"error": "invalid extract_text mode or bounds", "exit_code": 1}
        try:
            from .ocr_engine import extract_image_text
            if suffix in _PDF_SUFFIXES:
                def _extract_pdf_pages():
                    try:
                        import pypdfium2 as pdfium
                    except ImportError as exc:
                        raise RuntimeError(
                            "PDF OCR requires the optional pypdfium2 package"
                        ) from exc
                    document = pdfium.PdfDocument(str(path))
                    page_count = len(document)
                    lines, accepted = [], 0
                    # Keep one OCR call bounded while covering ordinary
                    # documents completely. Larger PDFs can be inspected in
                    # page ranges with inspect_media.
                    rendered_count = min(page_count, 12)
                    with tempfile.TemporaryDirectory(prefix="odysseus-pdf-ocr-") as temp_dir:
                        for index in range(rendered_count):
                            rendered = document[index].render(scale=2.0).to_pil().convert("RGB")
                            image_path = Path(temp_dir) / f"page-{index + 1}.png"
                            rendered.save(image_path, "PNG")
                            remaining = max(1, maximum - len(lines))
                            page_evidence = extract_image_text(
                                image_path,
                                include_layout=bool(args.get("include_layout", False)),
                                numeric_only=mode == "numbers",
                                min_confidence=minimum,
                                max_results=remaining,
                            )
                            accepted += int(page_evidence.get("count") or 0)
                            for line in page_evidence.get("lines") or []:
                                if len(lines) >= maximum:
                                    break
                                lines.append({"page": index + 1, **line})
                    return {
                        "legend": {
                            "page": "one-based PDF page",
                            "t": "text",
                            "p": "confidence",
                            "xy": "pixel center",
                        },
                        "page_count": page_count,
                        "pages_processed": rendered_count,
                        "count": accepted,
                        "returned": len(lines),
                        "truncated": accepted > len(lines) or page_count > rendered_count,
                        "lines": lines,
                    }
                evidence = await asyncio.to_thread(_extract_pdf_pages)
            else:
                evidence = await asyncio.to_thread(extract_image_text, path, include_layout=bool(args.get("include_layout", False)), numeric_only=mode == "numbers", min_confidence=minimum, max_results=maximum)
        except Exception as exc:
            return {"error": f"extract_text failed: {exc}", "exit_code": 1}
        return {"output": json.dumps(evidence, ensure_ascii=False, separators=(",", ":")), "exit_code": 0, "ocr": evidence}


class InspectMediaTool:
    """Return timestamped local image/video frames to the multimodal model."""

    async def execute(self, content: str, _ctx: dict) -> dict:
        try:
            args = json.loads(content or "{}")
        except json.JSONDecodeError as exc:
            return {"error": f"inspect_media arguments must be JSON: {exc}", "exit_code": 1}
        if not isinstance(args, dict):
            return {"error": "inspect_media arguments must be a JSON object", "exit_code": 1}
        argument_alias_notes: list[str] = []
        unknown_arguments = sorted(set(args) - _INSPECT_MEDIA_ARGUMENTS)
        if unknown_arguments:
            rendered = ", ".join(f"`{name}`" for name in unknown_arguments)
            for name in unknown_arguments:
                args.pop(name, None)
            argument_alias_notes.append(
                f"Ignored unknown argument(s): {rendered}. Use only fields from the "
                "advertised inspect_media schema in future calls."
            )
        raw_timestamp = args.get("timestamp")
        if (
            isinstance(raw_timestamp, str)
            and raw_timestamp.strip().casefold() in {"auto", "automatic", "default"}
        ):
            args.pop("timestamp", None)
            argument_alias_notes.append(
                "Normalized automatic `timestamp` to representative frame sampling; "
                "omit `timestamp` for automatic inspection in future calls."
            )
        if args.get("max_dimension") not in (None, ""):
            try:
                requested_dimension = int(args["max_dimension"])
            except (TypeError, ValueError):
                requested_dimension = None
            if requested_dimension is not None and not (
                _MIN_MODEL_FRAME_DIMENSION
                <= requested_dimension
                <= _MAX_MODEL_FRAME_DIMENSION
            ):
                bounded_dimension = min(
                    _MAX_MODEL_FRAME_DIMENSION,
                    max(_MIN_MODEL_FRAME_DIMENSION, requested_dimension),
                )
                args["max_dimension"] = bounded_dimension
                argument_alias_notes.append(
                    f"Clamped `max_dimension` from {requested_dimension} to "
                    f"{bounded_dimension}; use an integer from "
                    f"{_MIN_MODEL_FRAME_DIMENSION} to "
                    f"{_MAX_MODEL_FRAME_DIMENSION} in future calls."
                )
        if "timing" in args:
            if "sampling" in args or "observe" in args:
                return {
                    "error": "inspect_media timing must not be combined with sampling/observe; use sampling",
                    "exit_code": 1,
                }
            timing = str(args.pop("timing") or "").strip().lower()
            if timing not in {"uniform", "scene", "motion", "overview"}:
                return {
                    "error": "inspect_media timing must be uniform, scene, motion, or overview; use sampling",
                    "exit_code": 1,
                }
            args["sampling"] = timing
            argument_alias_notes.append(
                "Normalized `timing` to `sampling`; use `sampling` in future calls."
            )
        if "observe" in args:
            if "sampling" in args:
                return {
                    "error": "inspect_media observe and sampling must not be used together; use sampling",
                    "exit_code": 1,
                }
            observe = str(args.pop("observe") or "").strip().lower()
            if observe not in {"uniform", "scene", "motion", "overview"}:
                return {
                    "error": "inspect_media observe must be uniform, scene, motion, or overview; use sampling",
                    "exit_code": 1,
                }
            args["sampling"] = observe
            argument_alias_notes.append(
                "Normalized `observe` to `sampling`; use `sampling` in future calls."
            )
        if "time_range" in args:
            if any(args.get(name) not in (None, "") for name in ("start", "end")):
                return {
                    "error": "inspect_media time_range must not be combined with start/end; use start and end",
                    "exit_code": 1,
                }
            raw_range = args.pop("time_range")
            range_start = range_end = None
            if isinstance(raw_range, dict):
                range_start, range_end = raw_range.get("start"), raw_range.get("end")
            elif isinstance(raw_range, (list, tuple)) and len(raw_range) == 2:
                range_start, range_end = raw_range
            elif isinstance(raw_range, str):
                match = re.fullmatch(r"\s*(.+?)\s*(?:/|-|–|—|\bto\b)\s*(.+?)\s*", raw_range)
                if match:
                    range_start, range_end = match.groups()
            if range_start in (None, "") or range_end in (None, ""):
                return {
                    "error": (
                        "inspect_media time_range must contain one start and end, for example "
                        "00:05:00-00:08:00; use explicit start/end in future calls"
                    ),
                    "exit_code": 1,
                }
            args["start"], args["end"] = range_start, range_end
            argument_alias_notes.append(
                "Normalized `time_range` to explicit `start` and `end`; use start/end in future calls."
            )
        if "frames_per_page" in args:
            raw_frames_per_page = args.pop("frames_per_page")
            try:
                frames_per_page = int(raw_frames_per_page)
            except (TypeError, ValueError):
                return {
                    "error": "inspect_media frames_per_page must be a positive integer layout hint",
                    "exit_code": 1,
                }
            if frames_per_page <= 0:
                return {
                    "error": "inspect_media frames_per_page must be a positive integer layout hint",
                    "exit_code": 1,
                }
            argument_alias_notes.append(
                "Ignored `frames_per_page` layout hint; dense observations use bounded "
                "eight-tile contact sheets without dropping requested frames."
            )
        raw_duration = args.get("duration")
        if (
            isinstance(raw_duration, str)
            and raw_duration.strip().lower() in {
                "all", "all video", "auto", "automatic", "default", "entire",
                "entire video", "full",
                "full video", "max", "maximum", "overview", "video", "whole",
                "whole video",
            }
        ):
            args.pop("duration", None)
            argument_alias_notes.append(
                "Normalized symbolic `duration` to the full available media range; "
                "omit `duration` for whole-video inspection in future calls."
            )
        elif (
            isinstance(raw_duration, str)
            and all(args.get(name) in (None, "") for name in ("start", "end"))
        ):
            duration_range = re.fullmatch(
                r"\s*(.+?)\s*(?:/|-|–|—|\bto\b)\s*(.+?)\s*",
                raw_duration,
            )
            if duration_range:
                args.pop("duration", None)
                args["start"], args["end"] = duration_range.groups()
                argument_alias_notes.append(
                    "Normalized range-form `duration` to explicit `start` and `end`; "
                    "use `time_range` or start/end for timeline ranges in future calls."
                )
        legacy_export_note = ""
        if "export" in args:
            if "exports" in args:
                return {
                    "error": "inspect_media export and exports must not be used together; use exports",
                    "exit_code": 1,
                }
            legacy_export = args.pop("export")
            if isinstance(legacy_export, str):
                try:
                    legacy_export = json.loads(legacy_export)
                except json.JSONDecodeError:
                    return {
                        "error": (
                            "inspect_media export must be a JSON object or array. Use exports=[{timestamp, "
                            "output_path}, ...] for saved video stills, or top-level crop/query for inspection."
                        ),
                        "exit_code": 1,
                    }
            if isinstance(legacy_export, dict):
                has_destination = legacy_export.get("output_path") not in (None, "")
                has_timestamp = legacy_export.get("timestamp") not in (None, "")
                if not has_destination and not has_timestamp:
                    if isinstance(legacy_export.get("crop"), dict) and "crop" not in args:
                        args["crop"] = legacy_export["crop"]
                    label = str(legacy_export.get("caption") or "").strip()
                    if label and not str(args.get("query") or "").strip():
                        args["query"] = label
                    legacy_export_note = (
                        "Normalized inspection-only `export` object to top-level crop/query; "
                        "use top-level fields in future calls."
                    )
                else:
                    args["exports"] = [legacy_export]
                    legacy_export_note = (
                        "Normalized singular `export` to one-item `exports`; use `exports` in future calls."
                    )
            elif isinstance(legacy_export, list) and legacy_export:
                args["exports"] = legacy_export
                legacy_export_note = (
                    "Normalized `export` array to `exports`; use `exports` in future calls."
                )
            else:
                return {
                    "error": "inspect_media export must be a non-empty JSON object or array",
                    "exit_code": 1,
                }
        export_path_alias = str(args.pop("export_path", "") or "").strip()
        if export_path_alias:
            output_path = str(args.get("output_path") or "").strip()
            if output_path and output_path != export_path_alias:
                return {
                    "error": "inspect_media output_path and export_path must not conflict",
                    "exit_code": 1,
                }
            args["output_path"] = export_path_alias
        runtime_context = (
            _ctx.get("client_runtime_context")
            if isinstance(_ctx, dict)
            else None
        )
        caption_allowed = not (
            isinstance(runtime_context, dict)
            and runtime_context.get("media_caption_allowed") is False
        )
        requested_captions = [str(args.get("caption") or "").strip()]
        if isinstance(args.get("exports"), list):
            requested_captions.extend(
                str(item.get("caption") or "").strip()
                for item in args["exports"]
                if isinstance(item, dict)
            )
        if not caption_allowed and any(requested_captions):
            return {
                "error": (
                    "inspect_media caption is only allowed when the user explicitly "
                    "requests visible text drawn on the saved artifact. Omit caption "
                    "for extracted evidence frames; caption text is not pixel evidence."
                ),
                "exit_code": 1,
            }
        try:
            path = _resolve_media_path(str(args.get("path") or ""))
        except (ValueError, FileNotFoundError) as exc:
            return {"error": str(exc), "exit_code": 1}

        suffix = path.suffix.lower()
        if suffix in _SVG_SUFFIXES:
            renderer = shutil.which("rsvg-convert")
            renderer_kind = "rsvg"
            if not renderer:
                renderer = shutil.which("convert")
                renderer_kind = "imagemagick"
            if not renderer:
                return {
                    "error": (
                        "inspect_media SVG rendering requires rsvg-convert "
                        "or ImageMagick convert"
                    ),
                    "exit_code": 1,
                }
            raw_output = str(args.get("output_path") or "").strip()
            temporary = None
            try:
                if raw_output:
                    output = _resolve_workspace_path(
                        raw_output,
                        must_exist=False,
                        field_name="output_path",
                    )
                    if output.suffix.lower() != ".png":
                        return {
                            "error": "SVG output_path must be a PNG file",
                            "exit_code": 1,
                        }
                    output.parent.mkdir(parents=True, exist_ok=True)
                else:
                    temporary = tempfile.NamedTemporaryFile(suffix=".png")
                    output = Path(temporary.name)
                rendered = await asyncio.to_thread(
                    _run,
                    (
                        [renderer, "--output", str(output), str(path)]
                        if renderer_kind == "rsvg"
                        else [renderer, str(path), str(output)]
                    ),
                    60,
                )
                if rendered.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
                    return {
                        "error": "SVG rendering failed: " + rendered.stderr[-2000:],
                        "exit_code": 1,
                    }
                result = {
                    "output": (
                        f"Rendered SVG {args.get('path')}"
                        + (f" to {raw_output}." if raw_output else ".")
                        + " One PNG preview follows."
                    ),
                    "images": [{
                        "data": base64.b64encode(output.read_bytes()).decode("ascii"),
                        "mimeType": "image/png",
                    }],
                    "exit_code": 0,
                }
                if raw_output:
                    result["output_path"] = raw_output
                return result
            except ValueError as exc:
                return {"error": str(exc), "exit_code": 1}
            finally:
                if temporary is not None:
                    temporary.close()
        if suffix in _IMAGE_SUFFIXES:
            try:
                from PIL import Image

                with Image.open(path) as source:
                    source.seek(0)
                    image = source.convert("RGB")
                    source_size = image.size
                    crop = args.get("crop")
                    crop_note = ""
                    if crop not in (None, {}):
                        if not isinstance(crop, dict):
                            raise ValueError("inspect_media image crop must be an object")
                        try:
                            x = int(crop.get("x"))
                            y = int(crop.get("y"))
                            width = int(crop.get("width"))
                            height = int(crop.get("height"))
                        except (TypeError, ValueError) as exc:
                            raise ValueError(
                                "inspect_media image crop requires integer x, y, width, and height"
                            ) from exc
                        if x < 0 or y < 0 or width <= 0 or height <= 0:
                            raise ValueError(
                                "inspect_media image crop requires non-negative x/y and positive width/height"
                            )
                        if x + width > source_size[0] or y + height > source_size[1]:
                            raise ValueError(
                                "inspect_media image crop must stay inside the source image "
                                f"({source_size[0]}x{source_size[1]})"
                            )
                        image = image.crop((x, y, x + width, y + height))
                        crop_note = f" Applied pixel crop x={x}, y={y}, width={width}, height={height}."

                    preview_limit = _model_frame_dimension(args)
                    image.thumbnail((preview_limit, preview_limit))
                    preview_size = image.size
                    buffer = io.BytesIO()
                    image.save(buffer, "JPEG", quality=90)

                ignored = [
                    name for name in ("frames", "sampling", "segments", "speed", "page", "pages")
                    if args.get(name) not in (None, "", [])
                ]
                ignored_note = ""
                if ignored:
                    ignored_note = (
                        " Ignored video/PDF-only arguments for this still image: "
                        + ", ".join(f"`{name}`" for name in ignored)
                        + "."
                    )
                alias_note = f" {legacy_export_note}" if legacy_export_note else ""
                other_alias_notes = (
                    " " + " ".join(argument_alias_notes) if argument_alias_notes else ""
                )
                result = {
                    "output": (
                        f"Image from {args.get('path')}: source {source_size[0]}x{source_size[1]}; "
                        f"one {preview_size[0]}x{preview_size[1]} preview follows."
                        + crop_note
                        + ignored_note
                        + alias_note
                        + other_alias_notes
                    ),
                    "images": [{
                        "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
                        "mimeType": "image/jpeg",
                    }],
                    "source_dimensions": list(source_size),
                    "preview_dimensions": list(preview_size),
                    "exit_code": 0,
                }
                from .ocr_engine import query_requests_numbers, query_requests_ocr
                if query_requests_ocr(args.get("query")):
                    ocr = await ExtractTextTool().execute(json.dumps({"path": args.get("path"), "mode": "numbers" if query_requests_numbers(args.get("query")) else "all"}), _ctx)
                    if ocr.get("exit_code") == 0:
                        result["output"] += "\n\nLocal OCR evidence:\n" + str(ocr["output"])
                        result["ocr_augmented"] = True
                    else:
                        result["output"] += "\n\nOCR warning: " + str(ocr.get("error"))
                return result
            except (OSError, TypeError, ValueError) as exc:
                return {"error": str(exc), "exit_code": 1}
        if suffix in _PDF_SUFFIXES:
            try:
                from pypdf import PdfReader
                import pypdfium2 as pdfium
            except ImportError:
                return {"error": "inspect_media PDF rendering requires pypdf and pypdfium2", "exit_code": 1}
            try:
                reader = PdfReader(str(path))
                page_count = len(reader.pages)
                raw_pages = args.get("pages")
                if isinstance(raw_pages, str) and raw_pages.strip().startswith("["):
                    try:
                        decoded_pages = json.loads(raw_pages)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        decoded_pages = None
                    if isinstance(decoded_pages, list):
                        raw_pages = decoded_pages
                        argument_alias_notes.append(
                            "Normalized JSON-encoded `pages` to an exact PDF page list; "
                            "send `pages` as an array in future calls."
                        )
                explicit_page_numbers = None
                if isinstance(raw_pages, (list, tuple)):
                    if not raw_pages:
                        raise ValueError("pages list must contain at least one page number")
                    if len(raw_pages) > 12:
                        raise ValueError("pages list may contain at most 12 page numbers")
                    explicit_page_numbers = []
                    for value in raw_pages:
                        if isinstance(value, bool):
                            raise ValueError("pages list must contain integer page numbers")
                        try:
                            page_number = int(value)
                        except (TypeError, ValueError) as exc:
                            raise ValueError(
                                "pages list must contain integer page numbers"
                            ) from exc
                        if page_number < 1 or page_number > page_count:
                            raise ValueError(
                                f"pages list values must be between 1 and {page_count}"
                            )
                        if page_number not in explicit_page_numbers:
                            explicit_page_numbers.append(page_number)
                    count = len(explicit_page_numbers)
                else:
                    count = max(1, min(4, int(raw_pages or 2)))
                requested_page = args.get("page")
                if requested_page in (None, ""):
                    # Models often reuse the video-range vocabulary when
                    # paging through a PDF. Treat ``start`` as an unambiguous
                    # one-based page alias instead of silently returning page 1.
                    requested_page = args.get("start")
                if explicit_page_numbers is not None:
                    indices = [page_number - 1 for page_number in explicit_page_numbers]
                elif requested_page not in (None, ""):
                    first = int(requested_page) - 1
                    if first < 0 or first >= page_count:
                        raise ValueError(f"page must be between 1 and {page_count}")
                    indices = list(range(first, min(page_count, first + count)))
                else:
                    query = str(args.get("query") or "").strip().lower()
                    tokens = {
                        token for token in query.replace("-", " ").split()
                        if len(token) >= 3
                    }
                    ranked = []
                    for index, page in enumerate(reader.pages):
                        text = (page.extract_text() or "").lower()
                        score = sum(text.count(token) for token in tokens)
                        ranked.append((score, index))
                    ranked.sort(key=lambda item: (-item[0], item[1]))
                    indices = sorted(index for _, index in ranked[:count])

                document = pdfium.PdfDocument(str(path))
                images = []
                for index in indices:
                    rendered = document[index].render(scale=2.0).to_pil().convert("RGB")
                    with tempfile.NamedTemporaryFile(suffix=".jpg") as temp:
                        rendered.save(temp.name, "JPEG", quality=88)
                        images.append({
                            "data": base64.b64encode(Path(temp.name).read_bytes()).decode("ascii"),
                            "mimeType": "image/jpeg",
                        })
                pages_text = ", ".join(str(index + 1) for index in indices)
                extracted_pages = []
                for index in indices:
                    page_text = (reader.pages[index].extract_text() or "").strip()
                    if page_text:
                        extracted_pages.append(f"[Page {index + 1}]\n{page_text}")
                extracted_text = "\n\n".join(extracted_pages)
                if len(extracted_text) > 12000:
                    extracted_text = extracted_text[:12000] + "\n[...PDF text truncated]"
                text_guidance = ""
                if extracted_text:
                    text_guidance = (
                        " Extracted text for the rendered pages follows; use it for exact "
                        "table labels and values:\n\n" + extracted_text
                    )
                alias_guidance = "".join(
                    f" Argument note: {note}" for note in argument_alias_notes
                )
                return {
                    "output": (
                        f"PDF has {page_count} pages. Rendered pages {pages_text} in that order. "
                        "Inspect the returned page images with vision; use `page` plus an integer "
                        "`pages` count, or an exact `pages` list, to refine if needed."
                        + alias_guidance
                        + text_guidance
                    ),
                    "images": images,
                    "page_numbers": [index + 1 for index in indices],
                    "page_count": page_count,
                    "exit_code": 0,
                }
            except (TypeError, ValueError) as exc:
                return {"error": str(exc), "exit_code": 1}
            except Exception as exc:
                return {"error": f"PDF render failed: {exc}", "exit_code": 1}
        if suffix not in _VIDEO_SUFFIXES:
            return {
                "error": "inspect_media supports common image, SVG, video, and PDF files, not " + (suffix or "this file type"),
                "exit_code": 1,
            }

        ffprobe = shutil.which("ffprobe")
        ffmpeg = shutil.which("ffmpeg")
        if not ffprobe or not ffmpeg:
            return {"error": "inspect_media requires ffprobe and ffmpeg", "exit_code": 1}
        probe = await _run_video_probe(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)]
        )
        if probe.returncode != 0:
            return {"error": "ffprobe failed: " + probe.stderr[-2000:], "exit_code": 1}
        try:
            duration = float(json.loads(probe.stdout)["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return {"error": "ffprobe did not report a valid video duration", "exit_code": 1}
        if not math.isfinite(duration) or duration <= 0:
            return {"error": "video duration is not positive", "exit_code": 1}
        stream_probe = await _run_video_probe(
            [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)]
        )
        try:
            has_audio = any(
                stream.get("codec_type") == "audio"
                for stream in json.loads(stream_probe.stdout).get("streams", [])
                if isinstance(stream, dict)
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            has_audio = False

        try:
            normalized_duration_range = False
            ignored_duration_range = False
            normalized_timestamp_range = False
            if (
                args.get("timestamp") not in (None, "")
                and args.get("start") in (None, "")
                and args.get("end") not in (None, "")
            ):
                args["start"] = args.pop("timestamp")
                normalized_timestamp_range = True
            requested_start = _parse_video_position(
                args.get("start"), default=0.0, duration=duration
            )
            if args.get("duration") not in (None, "") and args.get("end") in (None, ""):
                requested_span = _parse_seconds(args.get("duration"), default=duration)
                if not math.isfinite(requested_span) or requested_span <= 0:
                    raise ValueError("video duration range must be a positive number of seconds")
                requested_end = requested_start + requested_span
                normalized_duration_range = True
            else:
                requested_end = _parse_video_position(
                    args.get("end"), default=duration, duration=duration
                )
                ignored_duration_range = args.get("duration") not in (None, "")
            if requested_end <= requested_start:
                return {
                    "error": (
                        "inspect_media end must be after start; video timestamps are "
                        "absolute timeline positions"
                    ),
                    "exit_code": 1,
                }
            if requested_end <= 0 or requested_start >= duration:
                return {
                    "error": (
                        f"requested video range {_format_seconds(max(0.0, requested_start))} - "
                        f"{_format_seconds(max(0.0, requested_end))} is outside the "
                        f"{_format_seconds(duration)} video; inspect_media times use the "
                        "absolute video timeline, not an on-screen scoreboard or match clock"
                    ),
                    "exit_code": 1,
                }
            start = max(0.0, requested_start)
            end = min(duration, requested_end)
            model_frame_dimension = _model_frame_dimension(args)
            sampling = str(args.get("sampling") or "uniform").strip().lower()
            sampling_aliases = {
                "overiew": "overview",
                "unifor": "uniform",
                "uniformly": "uniform",
            }
            normalized_sampling = sampling_aliases.get(sampling)
            if normalized_sampling:
                argument_alias_notes.append(
                    f"Normalized sampling `{sampling}` to `{normalized_sampling}`; "
                    "use an advertised sampling value in future calls."
                )
                sampling = normalized_sampling
            if sampling not in {"uniform", "scene", "motion", "overview"}:
                raise ValueError("sampling must be uniform, scene, motion, or overview")
            raw_frame_count = int(args.get("frames") or 0)
            normalized_uniform_overview = bool(
                sampling == "uniform"
                and raw_frame_count > 8
                and not args.get("segments")
                and not args.get("exports")
                and not str(args.get("output_path") or "").strip()
            )
            if normalized_uniform_overview:
                sampling = "overview"
            overview_inspection = (
                sampling == "overview"
                and not args.get("exports")
                and not str(args.get("output_path") or "").strip()
            )
            timestamp_inspection = bool(
                args.get("timestamp") not in (None, "")
                and not args.get("segments")
                and not args.get("exports")
                and not str(args.get("output_path") or "").strip()
            )
            # Eight frames per implicit inspection made every follow-up
            # vision turn expensive, especially when the model explored a
            # video in several overlapping ranges. Keep explicit requests
            # fully supported, but make the safe default four representative
            # frames so normal QA does not double its visual context by
            # default.
            argument_notes: list[str] = []
            argument_notes.extend(argument_alias_notes)
            if normalized_timestamp_range:
                argument_notes.append(
                    "Normalized timestamp plus end to an explicit start/end video range."
                )
            if export_path_alias:
                argument_notes.append(
                    "Normalized `export_path` to `output_path`; use `output_path` in future calls."
                )
            if legacy_export_note:
                argument_notes.append(legacy_export_note)
            if normalized_uniform_overview:
                argument_notes.append(
                    "Normalized uniform video sampling above 8 observations to `overview`, "
                    "which preserves timeline coverage in bounded contact sheets."
                )
            if normalized_duration_range:
                argument_notes.append(
                    "Normalized `duration` to an end position relative to `start`; use "
                    "explicit start/end for exact absolute timeline ranges."
                )
            elif ignored_duration_range:
                argument_notes.append(
                    "Ignored `duration` because an explicit `end` was also provided."
                )
            if args.get("queries") is not None:
                argument_notes.append(
                    "Ignored unknown `queries`; use singular `query` only as a label for what "
                    "to inspect. It does not semantically search the video."
                )
            if args.get("page") is not None:
                argument_notes.append(
                    "Ignored PDF-only `page` for video; use `timestamp` for one exact frame "
                    "or start/end for a video range."
                )
            if timestamp_inspection:
                argument_notes.append(
                    "Normalized `timestamp` without output_path to an exact still inspection; "
                    "no artifact was written."
                )
            if overview_inspection and "frames" not in args:
                requested_count = 48
                if args.get("pages") is not None:
                    argument_notes.append(
                        "Ignored PDF-only `pages` for video overview; use `frames` only to "
                        "override the default 48 observations."
                    )
            elif "frames" not in args and args.get("pages") is not None:
                # A mixed image/video/PDF schema makes smaller models
                # occasionally use the PDF count name for video. Preserve the
                # obvious numeric intent and tell the model what was
                # normalized so its next call can use the native video field.
                raw_video_pages = args.get("pages")
                if isinstance(raw_video_pages, str) and raw_video_pages.strip().startswith("["):
                    try:
                        decoded_video_pages = json.loads(raw_video_pages)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        decoded_video_pages = None
                    if isinstance(decoded_video_pages, list):
                        raw_video_pages = decoded_video_pages
                if isinstance(raw_video_pages, (list, tuple)):
                    if not raw_video_pages:
                        requested_count = 4
                    else:
                        requested_count = len(raw_video_pages)
                    argument_notes.append(
                        "Normalized PDF-style `pages` list to its video `frames` count; "
                        "use `frames` for video."
                    )
                else:
                    try:
                        requested_count = int(raw_video_pages or 4)
                    except (TypeError, ValueError):
                        requested_count = 4
                        argument_notes.append(
                            "Ignored malformed PDF-only `pages` for video; use `frames` for video."
                        )
                    else:
                        argument_notes.append(
                            "Normalized PDF-only `pages` to video `frames`; use `frames` for video."
                        )
            else:
                requested_count = int(
                    args.get("frames")
                    or (48 if overview_inspection else 1 if timestamp_inspection else 4)
                )
                if args.get("pages") is not None:
                    argument_notes.append(
                        "Ignored PDF-only `pages` because video `frames` was also provided."
                    )
            dense_contact_sheet = bool(
                requested_count > 8
                and not args.get("exports")
                and not str(args.get("output_path") or "").strip()
            )
            frame_limit = 64 if overview_inspection or dense_contact_sheet else 8
            count = max(1, min(frame_limit, requested_count))
            if requested_count > count:
                argument_notes.append(
                    f"Capped video inspection from {requested_count} requested observations "
                    f"to {count} returned observations; use a focused start/end range or "
                    "bounded follow-up instead of increasing frames again."
                )
        except (TypeError, ValueError) as exc:
            return {"error": str(exc), "exit_code": 1}
        if end <= start:
            return {"error": "inspect_media end must be after start", "exit_code": 1}

        requested_segments: list[tuple[float, float]] = []
        raw_segments = args.get("segments")
        if raw_segments is not None:
            segment_limit = 12 if str(args.get("output_path") or "").strip() else 32
            if (
                not isinstance(raw_segments, list)
                or not raw_segments
                or len(raw_segments) > segment_limit
            ):
                return {
                    "error": (
                        "inspect_media segments must be a non-empty array of at most "
                        f"{segment_limit} ranges"
                    ),
                    "exit_code": 1,
                }
            # Native models sometimes express contiguous timeline partitions as
            # an ordered list of end boundaries. This shorthand is unambiguous:
            # the first range starts at the requested/global start and every
            # later range starts at the prior boundary. Keep mixed or otherwise
            # incomplete objects strict because their intended ranges are not
            # safely inferable.
            if all(
                isinstance(segment, dict)
                and segment.get("start") in (None, "")
                and segment.get("end") not in (None, "")
                for segment in raw_segments
            ):
                cursor: object = start
                normalized_segments = []
                for segment in raw_segments:
                    normalized_segments.append({"start": cursor, "end": segment["end"]})
                    cursor = segment["end"]
                raw_segments = normalized_segments
                argument_notes.append(
                    "Interpreted ordered end-only segment boundaries as contiguous ranges."
                )
            try:
                for segment in raw_segments:
                    if not isinstance(segment, dict):
                        raise ValueError("each segment must contain start and end")
                    if segment.get("start") in (None, "") or segment.get("end") in (None, ""):
                        raise ValueError(
                            "each inspect_media video segment must explicitly contain both start "
                            "and end; incomplete ranges are not expanded to the full timeline"
                        )
                    requested_segment_start = _parse_video_position(
                        segment.get("start"), default=0.0, duration=duration
                    )
                    requested_segment_end = _parse_video_position(
                        segment.get("end"), default=duration, duration=duration
                    )
                    if requested_segment_end <= requested_segment_start:
                        raise ValueError(
                            "each segment end must be after its start; video timestamps "
                            "are absolute timeline positions"
                        )
                    if requested_segment_end <= 0 or requested_segment_start >= duration:
                        raise ValueError(
                            f"requested segment {_format_seconds(max(0.0, requested_segment_start))} - "
                            f"{_format_seconds(max(0.0, requested_segment_end))} is outside the "
                            f"{_format_seconds(duration)} video; inspect_media times use the "
                            "absolute video timeline, not an on-screen scoreboard or match clock"
                        )
                    segment_start = max(0.0, requested_segment_start)
                    segment_end = min(duration, requested_segment_end)
                    if segment_end <= segment_start:
                        raise ValueError(
                            "each segment must overlap the video and have end after start"
                        )
                    requested_segments.append((segment_start, segment_end))
            except (TypeError, ValueError) as exc:
                return {"error": str(exc), "exit_code": 1}

        batch_exports = args.get("exports")
        if isinstance(batch_exports, list) and len(batch_exports) == 1 and isinstance(batch_exports[0], dict):
            singleton = batch_exports[0]
            singleton_output = str(singleton.get("output_path") or "").strip()
            if Path(singleton_output).suffix.lower() in _VIDEO_SUFFIXES:
                clip_start = singleton.get("start", args.get("start"))
                clip_end = singleton.get("end", args.get("end"))
                if (
                    singleton.get("timestamp") not in (None, "")
                    and (clip_start in (None, "") or clip_end in (None, ""))
                ):
                    return {
                        "error": (
                            "a video clip export requires explicit start and end; "
                            "timestamp is only for a still-image export"
                        ),
                        "exit_code": 1,
                    }
                # Be forgiving when a model wraps one requested clip in the
                # still-export array. The intent is unambiguous, and forcing a
                # retry tends to push otherwise-correct work into raw ffmpeg.
                args["output_path"] = singleton_output
                for field in ("start", "end", "speed"):
                    if singleton.get(field) not in (None, ""):
                        args[field] = singleton[field]
                try:
                    start = max(0.0, _parse_video_position(
                        args.get("start"), default=start, duration=duration
                    ))
                    end = min(duration, _parse_video_position(
                        args.get("end"), default=end, duration=duration
                    ))
                except (TypeError, ValueError) as exc:
                    return {"error": str(exc), "exit_code": 1}
                if end <= start:
                    return {"error": "inspect_media end must be after start", "exit_code": 1}
                batch_exports = None
        output_template = str(args.get("output_path") or "").strip()
        template_match = re.search(r"\$FRAME%|\{frame(?::0?\d+d)?\}|%0?\d*d", output_template)
        if batch_exports is None and template_match and Path(output_template).suffix.lower() in _IMAGE_SUFFIXES:
            # Models commonly ask to inspect a range and save each returned
            # frame using an ffmpeg-like filename template. Treat that as the
            # obvious batch operation instead of silently overwriting one
            # still at the literal template path.
            span = end - start
            sampled = [start + span * (index + 0.5) / count for index in range(count)]

            def _render_template(index: int) -> str:
                rendered = output_template.replace("$FRAME%", f"{index:02d}")
                rendered = re.sub(
                    r"\{frame(?::0?(\d+)d)?\}",
                    lambda match: f"{index:0{int(match.group(1) or 2)}d}",
                    rendered,
                )
                rendered = re.sub(
                    r"%0?(\d*)d",
                    lambda match: f"{index:0{int(match.group(1) or 1)}d}",
                    rendered,
                )
                return rendered

            batch_exports = [
                {"timestamp": timestamp, "output_path": _render_template(index)}
                for index, timestamp in enumerate(sampled, 1)
            ]
        if batch_exports is not None:
            if not isinstance(batch_exports, list) or not batch_exports or len(batch_exports) > 12:
                return {
                    "error": "inspect_media exports must be a non-empty array of at most 12 still-image exports",
                    "exit_code": 1,
                }
            validated_exports: list[tuple[dict, str, Path, float]] = []
            seen_outputs: set[Path] = set()
            for item in batch_exports:
                if not isinstance(item, dict):
                    return {"error": "each inspect_media export must be an object", "exit_code": 1}
                if item.get("timestamp") in (None, ""):
                    return {
                        "error": "each inspect_media still export requires an explicit timestamp",
                        "exit_code": 1,
                    }
                raw_output = str(item.get("output_path") or "").strip()
                try:
                    output = _resolve_workspace_path(
                        raw_output,
                        must_exist=False,
                        field_name="output_path",
                    )
                    requested_timestamp = _parse_video_position(
                        item.get("timestamp"), default=start, duration=duration
                    )
                    if requested_timestamp > duration:
                        raise ValueError(
                            f"requested export timestamp "
                            f"{_format_seconds(requested_timestamp)} is outside "
                            f"the {_format_seconds(duration)} video"
                        )
                    timestamp = max(0.0, requested_timestamp)
                    if timestamp == duration:
                        # The container duration is an exclusive media bound;
                        # seeking exactly to it commonly yields no decoded
                        # frame. Interpret an exact endpoint/100% request as
                        # the final decodable instant.
                        timestamp = max(0.0, duration - 0.05)
                except ValueError as exc:
                    return {"error": str(exc), "exit_code": 1}
                if output.suffix.lower() not in _IMAGE_SUFFIXES:
                    return {
                        "error": "each inspect_media export output_path must be a supported image file",
                        "exit_code": 1,
                    }
                if output in seen_outputs:
                    return {
                        "error": (
                            "inspect_media exports must use a unique output_path for each "
                            f"still; duplicate destination: {raw_output}. Choose one timestamp "
                            "for that artifact or use distinct filenames."
                        ),
                        "exit_code": 1,
                    }
                seen_outputs.add(output)
                validated_exports.append((item, raw_output, output, timestamp))
            created: list[str] = []
            preview_images: list[dict[str, str]] = []
            for item, raw_output, output, timestamp in validated_exports:
                output.parent.mkdir(parents=True, exist_ok=True)
                still_command = [
                    ffmpeg, "-hide_banner", "-loglevel", "error",
                    "-ss", f"{timestamp:.3f}", "-i", str(path),
                    "-frames:v", "1", "-q:v", "3",
                ]
                if output.suffix.lower() in {".jpg", ".jpeg"}:
                    # MJPEG's implicit limited/full-range conversion can fail
                    # intermittently when several encoder instances start at
                    # once. Make the required JPEG range explicit and keep
                    # each tiny still encoder single-threaded.
                    still_command.extend([
                        "-pix_fmt", "yuvj420p", "-threads", "1",
                    ])
                still_command.extend(["-y", str(output)])
                still = await asyncio.to_thread(
                    _run,
                    still_command,
                    60,
                )
                if still.returncode != 0 or not output.is_file():
                    return {
                        "error": "ffmpeg batch still extraction failed: " + still.stderr[-2000:],
                        "exit_code": 1,
                    }
                crop = item.get("crop")
                caption = str(item.get("caption") or "").strip()
                if isinstance(crop, dict) or caption:
                    try:
                        from PIL import Image, ImageDraw, ImageFont

                        image = Image.open(output).convert("RGB")
                        if isinstance(crop, dict):
                            x = max(0, int(crop.get("x", 0)))
                            y = max(0, int(crop.get("y", 0)))
                            width = int(crop.get("width", image.width - x))
                            height = int(crop.get("height", image.height - y))
                            if width <= 0 or height <= 0:
                                raise ValueError("crop width and height must be positive")
                            image = image.crop(
                                (x, y, min(image.width, x + width), min(image.height, y + height))
                            )
                        if caption:
                            draw = ImageDraw.Draw(image)
                            font = ImageFont.load_default(size=max(18, image.width // 14))
                            draw.text(
                                (16, max(0, image.height - 48)),
                                caption,
                                fill="white",
                                font=font,
                                stroke_width=3,
                                stroke_fill="black",
                            )
                        image.save(output)
                    except (ImportError, OSError, TypeError, ValueError) as exc:
                        return {"error": f"batch still image edit failed: {exc}", "exit_code": 1}
                created.append(f"{raw_output} at {_format_seconds(timestamp)}")
                try:
                    from PIL import Image

                    with Image.open(output) as preview:
                        preview = preview.convert("RGB")
                        preview.thumbnail((model_frame_dimension, model_frame_dimension))
                        buffer = io.BytesIO()
                        preview.save(buffer, "JPEG", quality=82)
                    preview_images.append({
                        "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
                        "mimeType": "image/jpeg",
                    })
                except (ImportError, OSError):
                    pass
            output_lines = [*argument_notes, "Created still images in one batch:", *created]
            return {
                "output": "\n".join(output_lines),
                "output_paths": [entry.split(" at ", 1)[0] for entry in created],
                "images": preview_images,
                "exit_code": 0,
            }

        output_path = None
        output_raw = str(args.get("output_path") or "").strip()
        timestamp_path = None
        timestamp_raw = str(args.get("timestamp_path") or "").strip()
        if timestamp_raw and not output_raw:
            return {"error": "timestamp_path requires output_path, start, and end", "exit_code": 1}
        if not output_raw and batch_exports is None:
            if args.get("speed") is not None:
                argument_notes.append(
                    "Ignored export-only `speed`; frame inspection does not play or speed up video. "
                    "Provide a video output_path to create a sped-up clip."
                )
            if str(args.get("caption") or "").strip():
                argument_notes.append(
                    "Ignored export-only `caption`; it draws text on a saved still and does not "
                    "semantically analyze video. Use `query` to label the inspection target."
                )
            if isinstance(args.get("crop"), dict):
                argument_notes.append(
                    "Ignored export-only `crop`; provide an image output_path to save a cropped still."
                )
        if output_raw:
            runtime_context = (
                _ctx.get("client_runtime_context")
                if isinstance(_ctx, dict)
                else None
            )
            completion = (
                runtime_context.get("completion_requirements")
                if isinstance(runtime_context, dict)
                else None
            )
            required_artifacts = {
                str(item or "").strip()
                for item in (
                    completion.get("required_artifacts") or []
                    if isinstance(completion, dict)
                    else []
                )
                if str(item or "").strip()
            }
            output_is_required = str(output_raw).strip() in required_artifacts
            try:
                output_path = _resolve_workspace_path(
                    output_raw,
                    must_exist=False,
                    field_name="output_path",
                )
            except ValueError as exc:
                return {"error": str(exc), "exit_code": 1}
            if output_path == path:
                return {"error": "inspect_media output_path must differ from path", "exit_code": 1}
            output_suffix = output_path.suffix.lower()
            if output_suffix not in _VIDEO_SUFFIXES | _IMAGE_SUFFIXES:
                # Models commonly use a workspace directory as a scratch
                # destination while asking for sampled evidence (for
                # example, ``frames`` plus ``segments``).  That request is an
                # inspection, not a single-file export; rejecting the
                # suffixless directory prevents the tool from returning the
                # requested observations at all.  Keep single-still and clip
                # exports strict, because silently changing those would hide
                # a real artifact-contract error.
                multi_observation = (
                    raw_frame_count > 1
                    or bool(requested_segments)
                    or sampling in {"scene", "motion", "overview"}
                )
                if multi_observation and not output_is_required:
                    if output_path.suffix:
                        argument_notes.append(
                            "Ignored non-artifact output_path for sampled video evidence; "
                            "use exports or a supported image/video filename to write artifacts."
                        )
                    else:
                        argument_notes.append(
                            "Ignored suffixless output_path directory for sampled video evidence; "
                            "use exports or a supported image/video filename to write artifacts."
                        )
                    output_path = None
                    output_raw = ""
                else:
                    return {
                        "error": "inspect_media output_path must be a supported image or video file",
                        "exit_code": 1,
                    }
            if output_path is not None and (
                output_suffix in _IMAGE_SUFFIXES
                and args.get("timestamp") in (None, "")
                and (
                    raw_frame_count > 1
                    or bool(requested_segments)
                    or sampling in {"scene", "motion", "overview"}
                )
            ):
                return {
                    "error": (
                        "inspect_media image output_path requires one explicit timestamp when "
                        "the request also asks for multiple frames, segments, or adaptive "
                        "sampling. Omit output_path to inspect the sampled evidence, then export "
                        "the chosen source frame with timestamp; alternatively use exports=[...] "
                        "or a numbered output template for multiple stills."
                    ),
                    "exit_code": 1,
                }
            if output_path is not None and (
                output_suffix in _VIDEO_SUFFIXES
                and args.get("timestamp") not in (None, "")
                and not any(name in args for name in ("start", "end", "segments"))
            ):
                return {
                    "error": (
                        "inspect_media timestamp selects a still image, but output_path is a video. "
                        "Use an image output_path for one timestamp, or provide explicit start/end "
                        "for a video clip."
                    ),
                    "exit_code": 1,
                }
            if output_path is not None:
                output_path.parent.mkdir(parents=True, exist_ok=True)
            if output_path is not None and output_suffix in _IMAGE_SUFFIXES:
                try:
                    requested_timestamp = _parse_video_position(
                        args.get("timestamp"), default=start, duration=duration
                    )
                except ValueError as exc:
                    return {"error": str(exc), "exit_code": 1}
                endpoint_request = requested_timestamp >= duration
                timestamp = max(0.0, min(max(0.0, duration - 0.05), requested_timestamp))
                candidate_timestamps = [timestamp]
                if endpoint_request:
                    # Container duration is an exclusive bound and can exceed
                    # the final packet timestamp by more than one frame. Retry
                    # a few bounded positions near the tail instead of failing
                    # a natural "end"/100% request on sparse or variable-FPS
                    # media.
                    candidate_timestamps.extend(
                        max(0.0, duration - offset)
                        for offset in (0.5, 1.0, 2.0)
                    )
                still = None
                for timestamp in dict.fromkeys(candidate_timestamps):
                    still = await asyncio.to_thread(
                        _run,
                        [
                            ffmpeg, "-hide_banner", "-loglevel", "error",
                            "-ss", f"{timestamp:.3f}", "-i", str(path),
                            "-frames:v", "1", "-q:v", "2", "-y", str(output_path),
                        ],
                        60,
                    )
                    if still.returncode == 0 and output_path.is_file() and output_path.stat().st_size:
                        break
                    with contextlib.suppress(OSError):
                        output_path.unlink()
                assert still is not None
                if still.returncode != 0 or not output_path.is_file():
                    return {"error": "ffmpeg still extraction failed: " + still.stderr[-2000:], "exit_code": 1}
                caption = str(args.get("caption") or "").strip()
                crop = args.get("crop")
                if caption or isinstance(crop, dict):
                    try:
                        from PIL import Image, ImageDraw, ImageFont

                        image = Image.open(output_path).convert("RGB")
                        if isinstance(crop, dict):
                            x = max(0, int(crop.get("x", 0)))
                            y = max(0, int(crop.get("y", 0)))
                            width = int(crop.get("width", image.width - x))
                            height = int(crop.get("height", image.height - y))
                            if width <= 0 or height <= 0:
                                raise ValueError("crop width and height must be positive")
                            image = image.crop((x, y, min(image.width, x + width), min(image.height, y + height)))
                        if caption:
                            draw = ImageDraw.Draw(image)
                            font = ImageFont.load_default(size=max(18, image.width // 14))
                            box = draw.textbbox((0, 0), caption, font=font, stroke_width=2)
                            text_width = box[2] - box[0]
                            text_height = box[3] - box[1]
                            draw.text(
                                ((image.width - text_width) / 2, image.height - text_height - 16),
                                caption,
                                fill="white",
                                font=font,
                                stroke_width=3,
                                stroke_fill="black",
                            )
                        image.save(output_path)
                    except (ImportError, OSError, TypeError, ValueError) as exc:
                        return {"error": f"still image edit failed: {exc}", "exit_code": 1}
            elif output_path is not None:
                segments = requested_segments
                try:
                    speed = float(args.get("speed") or 1.0)
                except (TypeError, ValueError):
                    return {"error": "inspect_media speed must be a number", "exit_code": 1}
                if not 0.25 <= speed <= 4.0:
                    return {"error": "inspect_media speed must be between 0.25 and 4.0", "exit_code": 1}
                if segments:
                    clip_command = [
                        ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path),
                    ]
                    filters: list[str] = []
                    concat_inputs: list[str] = []
                    for index, (segment_start, segment_end) in enumerate(segments):
                        filters.append(
                            f"[0:v]trim=start={segment_start:.3f}:end={segment_end:.3f},"
                            f"setpts=PTS-STARTPTS[v{index}]"
                        )
                        concat_inputs.append(f"[v{index}]")
                        if has_audio:
                            filters.append(
                                f"[0:a]atrim=start={segment_start:.3f}:end={segment_end:.3f},"
                                f"asetpts=PTS-STARTPTS[a{index}]"
                            )
                            concat_inputs.append(f"[a{index}]")
                    filters.append(
                        "".join(concat_inputs)
                        + f"concat=n={len(segments)}:v=1:a={1 if has_audio else 0}[outv]"
                        + ("[outa]" if has_audio else "")
                    )
                    clip_command.extend([
                        "-filter_complex", ";".join(filters), "-map", "[outv]",
                    ])
                    if has_audio:
                        clip_command.extend(["-map", "[outa]"])
                    clip_command.extend(_video_encode_args(output_path))
                else:
                    clip_command = [
                        ffmpeg, "-hide_banner", "-loglevel", "error",
                        "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(path),
                        "-map", "0:v?", "-map", "0:a?", *_video_encode_args(output_path),
                    ]
                if speed != 1.0 and segments:
                    return {
                        "error": "inspect_media currently applies speed to one start/end range at a time",
                        "exit_code": 1,
                    }
                if speed != 1.0:
                    clip_command.extend(["-filter:v", f"setpts=PTS/{speed:g}"])
                    if has_audio:
                        # atempo accepts 0.5..2.0; chain factors for the wider
                        # supported speed range while preserving pitch.
                        remaining = speed
                        factors: list[float] = []
                        while remaining > 2.0:
                            factors.append(2.0)
                            remaining /= 2.0
                        while remaining < 0.5:
                            factors.append(0.5)
                            remaining /= 0.5
                        factors.append(remaining)
                        clip_command.extend([
                            "-filter:a",
                            ",".join(f"atempo={factor:g}" for factor in factors),
                        ])
                clip_command.extend(["-movflags", "+faststart", "-y", str(output_path)])
                clip = await asyncio.to_thread(
                    _run,
                    clip_command,
                    120,
                )
                if clip.returncode != 0 or not output_path.is_file():
                    return {"error": "ffmpeg clip creation failed: " + clip.stderr[-2000:], "exit_code": 1}
            if timestamp_raw:
                try:
                    timestamp_path = _resolve_workspace_path(
                        timestamp_raw,
                        must_exist=False,
                        field_name="timestamp_path",
                    )
                except ValueError as exc:
                    return {"error": str(exc), "exit_code": 1}
                timestamp_path.parent.mkdir(parents=True, exist_ok=True)
                whole_start = _format_seconds(start).split(".", 1)[0]
                whole_end = _format_seconds(end).split(".", 1)[0]
                timestamp_path.write_text(
                    ("".join(
                        f"{_format_seconds(segment_start).split('.', 1)[0]} - "
                        f"{_format_seconds(segment_end).split('.', 1)[0]}\n"
                        for segment_start, segment_end in segments
                    ) if output_suffix in _VIDEO_SUFFIXES and segments else
                     f"{_format_seconds(timestamp).split('.', 1)[0]}\n"
                     if output_suffix in _IMAGE_SUFFIXES
                     else f"{whole_start} - {whole_end}\n"),
                    encoding="utf-8",
                )

        if output_path is not None and output_path.suffix.lower() in _IMAGE_SUFFIXES:
            try:
                from PIL import Image

                with Image.open(output_path) as preview:
                    preview = preview.convert("RGB")
                    preview.thumbnail((model_frame_dimension, model_frame_dimension))
                    buffer = io.BytesIO()
                    preview.save(buffer, "JPEG", quality=88)
                preview_image = {
                    "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
                    "mimeType": "image/jpeg",
                }
            except (ImportError, OSError):
                preview_image = {
                    "data": base64.b64encode(output_path.read_bytes()).decode("ascii"),
                    "mimeType": mimetypes.guess_type(output_path.name)[0] or "image/png",
                }
            lines = [
                f"Video duration: {_format_seconds(duration)}",
                "The returned preview is the still that was saved, including requested crop or caption edits.",
                f"Created still image: {output_raw} at {_format_seconds(timestamp)}",
            ]
            if argument_notes:
                lines[1:1] = [f"Argument note: {note}" for note in argument_notes]
            if timestamp_path is not None:
                lines.append(f"Created timestamp sidecar: {timestamp_raw}")
            result = {
                "output": "\n".join(lines),
                "images": [preview_image],
                "frame_timestamps": [timestamp],
                "sampling": "export",
                "scene_changes_detected": 0,
                "motion_candidates_detected": 0,
                "duration": duration,
                "output_path": output_raw,
                "exit_code": 0,
            }
            if timestamp_path is not None:
                result["timestamp_path"] = timestamp_raw
            return result

        if timestamp_inspection:
            try:
                exact_timestamp = _parse_video_position(
                    args.get("timestamp"), default=start, duration=duration
                )
            except ValueError as exc:
                return {"error": str(exc), "exit_code": 1}
            timestamps = [max(0.0, min(duration, exact_timestamp))]
            sampling_used = "timestamp"
            scene_changes_detected = 0
            motion_candidates_detected = 0
        elif requested_segments:
            # Segment-focused inspection must not fall back to unrelated points
            # on the full-video timeline.  With no explicit frame budget, keep
            # the inexpensive one-midpoint-per-range behavior.  When callers do
            # request more frames, distribute the available budget by range
            # duration while retaining at least one observation per range.
            sampled_segments = requested_segments
            allocations = [1] * len(sampled_segments)
            if output_path is None and ("frames" in args or sampling == "overview"):
                target_count = max(len(sampled_segments), count)
                remaining = target_count - len(sampled_segments)
                durations = [segment_end - segment_start for segment_start, segment_end in sampled_segments]
                duration_total = sum(durations)
                if remaining > 0 and duration_total > 0:
                    ideal_extras = [remaining * value / duration_total for value in durations]
                    floor_extras = [int(value) for value in ideal_extras]
                    allocations = [base + extra for base, extra in zip(allocations, floor_extras)]
                    leftovers = remaining - sum(floor_extras)
                    ranked = sorted(
                        range(len(sampled_segments)),
                        key=lambda index: (ideal_extras[index] - floor_extras[index], -index),
                        reverse=True,
                    )
                    for index in ranked[:leftovers]:
                        allocations[index] += 1
            timestamps = [
                segment_start + (segment_end - segment_start) * (index + 0.5) / allocation
                for (segment_start, segment_end), allocation in zip(sampled_segments, allocations)
                for index in range(allocation)
            ]
            sampling_used = (
                "overview"
                if output_path is None and sampling == "overview"
                else "segments" if output_path is None else "export_segments"
            )
            scene_changes_detected = 0
            motion_candidates_detected = 0
        elif sampling == "scene" and output_path is None:
            scene_timestamps = await _scene_change_timestamps(
                ffmpeg, path, start=start, end=end
            )
            timestamps = _bounded_scene_sample(
                scene_timestamps, start=start, end=end, count=count
            )
            sampling_used = "scene"
            scene_changes_detected = len(scene_timestamps)
            motion_candidates_detected = 0
        elif sampling == "motion" and output_path is None:
            motion_timestamps = await _motion_score_timestamps(
                ffmpeg, path, start=start, end=end
            )
            timestamps = _bounded_motion_sample(
                motion_timestamps, start=start, end=end, count=count
            )
            sampling_used = "motion"
            scene_changes_detected = 0
            motion_candidates_detected = len(motion_timestamps)
        elif sampling == "overview" and output_path is None:
            span = end - start
            timestamps = [start + span * (index + 0.5) / count for index in range(count)]
            sampling_used = "overview"
            scene_changes_detected = 0
            motion_candidates_detected = 0
        else:
            span = end - start
            timestamps = [start + span * (index + 0.5) / count for index in range(count)]
            sampling_used = "uniform"
            scene_changes_detected = 0
            motion_candidates_detected = 0
        if len(timestamps) > 8:
            frame_limit = 64
        images: list[dict[str, str]] = []
        decoded_timestamps: list[float] = []
        skipped_timestamps: list[float] = []
        with tempfile.TemporaryDirectory(prefix="ody-media-") as temp_dir:
            for index, preview_timestamp in enumerate(timestamps, 1):
                output = Path(temp_dir) / f"frame-{index:02d}.jpg"
                requested_timestamp = preview_timestamp
                attempted_timestamps: set[float] = set()
                for backoff in (0.0, 0.25, 1.0, 3.0):
                    preview_timestamp = max(start, requested_timestamp - backoff)
                    if preview_timestamp in attempted_timestamps:
                        continue
                    attempted_timestamps.add(preview_timestamp)
                    extract = await asyncio.to_thread(
                        _run,
                        [
                            ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{preview_timestamp:.3f}",
                            "-i", str(path), "-frames:v", "1", "-vf",
                            f"scale={model_frame_dimension}:{model_frame_dimension}:force_original_aspect_ratio=decrease",
                            "-q:v", "4", "-pix_fmt", "yuvj420p", "-threads", "1",
                            "-y", str(output),
                        ],
                        60,
                    )
                    if extract.returncode == 0 and output.is_file():
                        break
                if extract.returncode != 0 or not output.is_file():
                    skipped_timestamps.append(requested_timestamp)
                    continue
                decoded_timestamps.append(preview_timestamp)
                if preview_timestamp < requested_timestamp:
                    note = (
                        "Recovered a decodable frame before the requested timestamp "
                        "because the container tail was incomplete."
                    )
                    if note not in argument_notes:
                        argument_notes.append(note)
                images.append({
                    "data": base64.b64encode(output.read_bytes()).decode("ascii"),
                    "mimeType": "image/jpeg",
                })

        if not images:
            return {
                "error": "ffmpeg could not decode any requested video frame",
                "exit_code": 1,
            }
        timestamps = decoded_timestamps
        if skipped_timestamps:
            argument_notes.append(
                f"Skipped {len(skipped_timestamps)} undecodable requested frame(s) because the "
                "media container ended early or lacked data at those positions. Earlier decoded "
                "frames remain available for inspection."
            )

        contact_sheet_observations = 0
        if sampling_used == "overview" or len(images) > 8:
            try:
                images = _pack_overview_contact_sheets(images, timestamps)
            except (ImportError, OSError, ValueError) as exc:
                return {"error": f"inspect_media contact-sheet packing failed: {exc}", "exit_code": 1}
            contact_sheet_observations = len(timestamps)

        query = str(args.get("query") or "").strip()
        lines = [
            f"Video duration: {_format_seconds(duration)}",
            "All timestamps below use the absolute video timeline, not an on-screen scoreboard or match clock.",
            f"Sampled range: {_format_seconds(start)} - {_format_seconds(end)}",
            "Frames follow in this exact order:",
        ]
        if requested_segments:
            if sampling_used == "overview":
                lines.insert(
                    2,
                    f"Dense overview packed {contact_sheet_observations} observations across "
                    f"the requested segments into {len(images)} contact sheets. Read each "
                    "sheet row-major and refine candidate intervals with focused follow-ups.",
                )
            elif output_path is None and "frames" in args:
                lines.insert(2, "Distributed the requested frame budget across the requested segments.")
            else:
                lines.insert(2, "Sampled one midpoint frame from each requested segment.")
        elif sampling_used == "timestamp":
            lines.insert(2, "Sampled the exact requested video timestamp as one still image.")
        elif sampling_used == "scene":
            lines.insert(2, f"Scene-aware sampling detected {scene_changes_detected} visual cuts; uniform frames filled any remaining slots.")
        elif sampling_used == "motion":
            lines.insert(2, f"Motion-aware sampling ranked {motion_candidates_detected} visually active candidates with temporal spacing; uniform frames filled any remaining slots.")
        elif sampling_used == "overview":
            lines.insert(
                2,
                f"Dense overview packed {contact_sheet_observations} timestamped observations into "
                f"{len(images)} contact sheets. Read each sheet row-major; labels map every tile "
                "to the numbered absolute timestamp below. Refine candidate intervals with "
                "focused start/end calls before counting brief events.",
            )
        if contact_sheet_observations and sampling_used != "overview":
            lines.insert(
                2,
                f"Packed {contact_sheet_observations} {sampling_used} observations into "
                f"{len(images)} bounded contact sheets. Read each sheet row-major; labels map "
                "tiles to the numbered absolute timestamps below. Refine candidate intervals "
                "before making exact event counts.",
            )
        if requested_count > count:
            lines.insert(
                2,
                f"Requested {requested_count} frames; capped to {count} by the visual-context safety limit. "
                "Split the timeline into non-overlapping start/end ranges for additional evidence.",
            )
        if argument_notes:
            lines[2:2] = [f"Argument note: {note}" for note in argument_notes]
        lines.extend(f"{index}. {_format_seconds(timestamp)}" for index, timestamp in enumerate(timestamps, 1))
        if query:
            lines.append(f"Inspection target: {query}")
        if output_path is not None:
            if output_path.suffix.lower() in _IMAGE_SUFFIXES:
                lines.append(f"Created still image: {output_raw} at {_format_seconds(timestamp)}")
            else:
                if segments:
                    ranges = ", ".join(
                        f"{_format_seconds(segment_start)} - {_format_seconds(segment_end)}"
                        for segment_start, segment_end in segments
                    )
                    lines.append(f"Created concatenated clip: {output_raw} from {ranges}")
                else:
                    lines.append(
                        f"Created clip: {output_raw} ({_format_seconds(start)} - {_format_seconds(end)})"
                        + (f" at {speed:g}x speed" if speed != 1.0 else "")
                    )
            if timestamp_path is not None:
                lines.append(f"Created timestamp sidecar: {timestamp_raw}")
        else:
            lines.append(
                "If the user requested a clip or still image, stop refining once the time is reasonable and call "
                "inspect_media with output_path plus start/end for video or timestamp for one image. "
                "For multiple still images, use one call with exports=[{timestamp, output_path, crop?}, ...]."
            )
        result = {
            "output": "\n".join(lines),
            "images": images,
            "frame_timestamps": timestamps,
            "requested_frames": requested_count,
            "frame_limit": frame_limit,
            "sampling": sampling_used,
            "scene_changes_detected": scene_changes_detected,
            "motion_candidates_detected": motion_candidates_detected,
            "duration": duration,
            "exit_code": 0,
        }
        if contact_sheet_observations:
            result["contact_sheet_observations"] = contact_sheet_observations
        if sampling_used == "overview":
            result["overview_observations"] = contact_sheet_observations
        if output_path is not None:
            result["output_path"] = output_raw
        if timestamp_path is not None:
            result["timestamp_path"] = timestamp_raw
        return result


class TranscribeMediaTool:
    """Transcribe local audio/video with Odysseus's local Whisper backend."""

    async def execute(self, content: str, _ctx: dict) -> dict:
        try:
            args = json.loads(content or "{}")
        except json.JSONDecodeError as exc:
            return {"error": f"transcribe_media arguments must be JSON: {exc}", "exit_code": 1}
        if not isinstance(args, dict):
            return {"error": "transcribe_media arguments must be a JSON object", "exit_code": 1}
        try:
            path = _resolve_media_path(
                str(args.get("path") or ""),
                tool_name="transcribe_media",
            )
        except (ValueError, FileNotFoundError) as exc:
            return {"error": str(exc), "exit_code": 1}
        if path.suffix.lower() not in _VIDEO_SUFFIXES | _AUDIO_SUFFIXES:
            return {"error": "transcribe_media supports local audio and video files", "exit_code": 1}

        output_raw = str(args.get("output_path") or "").strip()
        output_path = None
        output_format = "txt"
        if output_raw:
            try:
                output_path = _resolve_workspace_path(
                    output_raw,
                    must_exist=False,
                    field_name="output_path",
                    tool_name="transcribe_media",
                )
            except ValueError as exc:
                return {"error": str(exc), "exit_code": 1}
            output_format = output_path.suffix.lower().lstrip(".")
            if output_format not in {"txt", "jsonl", "srt", "vtt"}:
                return {
                    "error": "transcribe_media output_path must end in .txt, .jsonl, .srt, or .vtt",
                    "exit_code": 1,
                }
            output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            timestamp_precision = int(args.get("timestamp_precision", 3))
        except (TypeError, ValueError):
            return {"error": "transcribe_media timestamp_precision must be an integer from 0 to 3", "exit_code": 1}
        if not 0 <= timestamp_precision <= 3:
            return {"error": "transcribe_media timestamp_precision must be an integer from 0 to 3", "exit_code": 1}

        language_raw = str(args.get("language") or "").strip().lower()
        language_hint = _WHISPER_LANGUAGE_ALIASES.get(language_raw, language_raw) or None
        # Auto-detection is safer for autonomous agents: models frequently
        # confuse the prompt/answer language with the speech language. An
        # explicit force_language flag remains available for genuinely known
        # noisy or code-switched audio.
        language = language_hint if args.get("force_language") is True else None
        try:
            start = max(0.0, _parse_seconds(args.get("start"), default=0.0))
            end_value = args.get("end")
            end = _parse_seconds(end_value, default=0.0) if end_value not in (None, "") else None
        except ValueError as exc:
            return {"error": str(exc), "exit_code": 1}
        if end is not None and end <= start:
            return {"error": "transcribe_media end must be after start", "exit_code": 1}

        explicit_model = str(args.get("model") or "").strip()
        configured_model = str(os.environ.get("ODYSSEUS_STT_MODEL") or "").strip()
        if explicit_model:
            model_name = explicit_model
        elif configured_model:
            model_name = configured_model
        elif end is not None and end - start <= 300:
            # A bounded excerpt can afford the more accurate decoder without
            # making long-recording transcription unexpectedly expensive.
            model_name = "small"
        else:
            model_name = "base"
        if model_name not in {"tiny", "base", "small"}:
            return {"error": "transcribe_media model must be tiny, base, or small", "exit_code": 1}

        if output_path is None:
            from src.tool_execution import get_active_workspace

            workspace = Path(str(get_active_workspace())).resolve()
            stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-.") or "media"
            identity = f"{path.resolve()}\0{start:.3f}\0{end}\0{model_name}"
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
            relative = Path(".odysseus") / "transcripts" / f"{stem}-{digest}.txt"
            output_path = workspace / relative
            output_raw = "/workspace/" + relative.as_posix()
            output_format = "txt"
            output_path.parent.mkdir(parents=True, exist_ok=True)

        # faster-whisper raises an opaque IndexError for video containers with
        # no audio stream. Detect that valid, common media case before model
        # loading and return a successful empty artifact.
        ffprobe = shutil.which("ffprobe")
        if ffprobe and path.suffix.lower() in _VIDEO_SUFFIXES:
            stream_probe = await _run_video_probe([
                ffprobe, "-v", "error", "-show_entries",
                "stream=codec_type", "-of", "json", str(path),
            ])
            if stream_probe.returncode == 0:
                try:
                    streams = json.loads(stream_probe.stdout).get("streams", [])
                    has_audio = any(
                        isinstance(stream, dict)
                        and stream.get("codec_type") == "audio"
                        for stream in streams
                    )
                except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                    has_audio = True
                if not has_audio:
                    output_path.write_text("", encoding="utf-8")
                    return {
                        "output": (
                            "No audio stream detected in the local media. "
                            f"Saved an empty transcript: {output_raw}"
                        ),
                        "transcript": "",
                        "language": "unknown",
                        "model": model_name,
                        "segments": 0,
                        "output_path": output_raw,
                        "format": output_format,
                        "exit_code": 0,
                    }

        def _transcribe() -> tuple[list, object]:
            model = _WHISPER_MODELS.get(model_name)
            if model is None:
                from faster_whisper import WhisperModel
                model = WhisperModel(model_name, device="cpu", compute_type="int8")
                _WHISPER_MODELS[model_name] = model
            kwargs = {
                "language": language,
                "beam_size": 5,
                "vad_filter": True,
            }
            if end is not None:
                kwargs["clip_timestamps"] = [start, end]
            elif start:
                kwargs["clip_timestamps"] = [start]
            return model.transcribe(str(path), **kwargs)

        try:
            segments_iter, info = await asyncio.to_thread(_transcribe)
            # faster-whisper performs decoding lazily while iterating.
            segments = await asyncio.to_thread(list, segments_iter)
        except ImportError:
            return {"error": "transcribe_media requires faster-whisper", "exit_code": 1}
        except Exception as exc:
            return {"error": f"transcription failed: {exc}", "exit_code": 1}

        segment_rows = [
            {
                "start": float(seg.start),
                "end": float(seg.end),
                "text": str(seg.text).strip(),
            }
            for seg in segments
            if str(seg.text).strip()
        ]
        lines = [
            f"[{_format_seconds(row['start'])} --> {_format_seconds(row['end'])}] {row['text']}"
            for row in segment_rows
        ]
        detected = str(getattr(info, "language", "") or language or "unknown")
        probability = getattr(info, "language_probability", None)
        header = f"Detected language: {detected}"
        if isinstance(probability, (int, float)):
            header += f" (confidence {probability:.2f})"
        transcript = "\n".join(lines)
        if output_format == "jsonl":
            artifact = "\n".join(
                json.dumps(
                    {
                        "start": round(row["start"], timestamp_precision),
                        "end": round(row["end"], timestamp_precision),
                        "text": row["text"],
                    },
                    ensure_ascii=False,
                )
                for row in segment_rows
            )
        elif output_format in {"srt", "vtt"}:
            def _subtitle_time(value: float) -> str:
                milliseconds = max(0, round(value * 1000))
                hours, remainder = divmod(milliseconds, 3_600_000)
                minutes, remainder = divmod(remainder, 60_000)
                seconds, millis = divmod(remainder, 1000)
                separator = "," if output_format == "srt" else "."
                return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"

            cues = [
                f"{index}\n{_subtitle_time(row['start'])} --> {_subtitle_time(row['end'])}\n{row['text']}"
                for index, row in enumerate(segment_rows, 1)
            ]
            artifact = "\n\n".join(cues)
            if output_format == "vtt":
                artifact = "WEBVTT\n\n" + artifact
        else:
            artifact = transcript
        output_path.write_text(artifact + ("\n" if artifact else ""), encoding="utf-8")
        inline_artifact = artifact
        if len(inline_artifact) > _MAX_INLINE_TRANSCRIPT_CHARS:
            inline_artifact = (
                inline_artifact[:_MAX_INLINE_TRANSCRIPT_CHARS]
                + "\n[Inline artifact preview truncated; read the saved file for the complete content.]"
            )
        if output_format == "jsonl":
            format_note = (
                "Artifact format: JSONL (one {start, end, text} object per speech segment). "
                "The saved file already contains the requested structured data; use it directly "
                "instead of reparsing or overwriting it."
            )
        elif output_format == "srt":
            format_note = (
                "Artifact format: SRT subtitle cues. The saved file is ready to use directly."
            )
        elif output_format == "vtt":
            format_note = (
                "Artifact format: WebVTT subtitle cues. The saved file is ready to use directly."
            )
        else:
            format_note = (
                "Transcript format: [START --> END] TEXT; text begins after the first '] '. "
                "When the request names a chapter, question, scene, or topic, identify its "
                "start and the next section boundary before filtering; do not search the "
                "whole recording. For long transcripts, search the saved file with a narrow "
                "term or bounded range instead of reading the entire file back into context."
            )
        return {
            "output": (
                f"{header}\nSaved timestamped transcript: {output_raw}\n"
                f"Transcription model: {model_name}\n"
                f"{format_note}\n"
                + (inline_artifact if inline_artifact else "No speech detected.")
            ),
            "transcript": transcript,
            "language": detected,
            "model": model_name,
            "segments": len(lines),
            "output_path": output_raw,
            "format": output_format,
            "exit_code": 0,
        }
