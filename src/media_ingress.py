"""Bounded local media ingestion for multimodal agent requests."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import mimetypes
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageOps, UnidentifiedImageError


IMAGE_SUFFIXES = frozenset({".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"})
VIDEO_SUFFIXES = frozenset({".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"})
DOCUMENT_SUFFIXES = frozenset({
    ".bash", ".c", ".cpp", ".css", ".csv", ".doc", ".docx", ".epub", ".go",
    ".h", ".htm", ".html", ".java", ".js", ".json", ".jsx", ".log",
    ".md", ".nix", ".pdf", ".php", ".pptx", ".py", ".rb", ".rs",
    ".sh", ".sql", ".ts", ".tsx", ".txt", ".xls", ".xlsx", ".xml",
    ".yaml", ".yml",
})
AUDIO_SUFFIXES = frozenset({".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"})
SUPPORTED_MEDIA_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES
SUPPORTED_ATTACHMENT_SUFFIXES = SUPPORTED_MEDIA_SUFFIXES | DOCUMENT_SUFFIXES | AUDIO_SUFFIXES


@dataclass(frozen=True)
class MediaIngressLimits:
    max_media_files: int = 4
    max_image_source_bytes: int = 12 * 1024 * 1024
    max_video_source_bytes: int = 128 * 1024 * 1024
    max_document_source_bytes: int = 32 * 1024 * 1024
    max_audio_source_bytes: int = 32 * 1024 * 1024
    max_encoded_bytes: int = 24 * 1024 * 1024
    max_inline_document_chars: int = 24_000
    max_dimension: int = 1600
    max_image_pixels: int = 40_000_000
    max_video_frames: int = 8
    video_probe_timeout_s: int = 15
    video_frame_timeout_s: int = 30


@dataclass(frozen=True)
class LocalMediaAttachment:
    path: Path
    source_path: str


@dataclass
class MediaArtifact:
    source_path: str
    modality: str
    source_bytes: int
    source_sha256: str
    encoded_bytes: int = 0
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None
    frame_timestamps_s: list[float] = field(default_factory=list)
    estimated_visual_tokens: int = 0
    extracted_chars: int = 0
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MediaIngressResult:
    content: list[dict[str, Any]]
    artifacts: list[MediaArtifact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def estimated_visual_tokens(self) -> int:
        return sum(item.estimated_visual_tokens for item in self.artifacts)

    def metadata(self) -> dict[str, Any]:
        return {
            "artifacts": [item.to_dict() for item in self.artifacts],
            "warnings": list(self.warnings),
            "estimated_visual_tokens": self.estimated_visual_tokens,
        }


def is_supported_media_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_MEDIA_SUFFIXES


def is_supported_attachment_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_ATTACHMENT_SUFFIXES


def limits_from_env() -> MediaIngressLimits:
    """Build limits from optional positive-integer environment overrides."""

    defaults = MediaIngressLimits()

    def value(name: str, default: int) -> int:
        raw = os.getenv(name)
        if raw is None or not raw.strip():
            return default
        parsed = int(raw)
        if parsed < 1:
            raise ValueError(f"{name} must be greater than zero")
        return parsed

    return MediaIngressLimits(
        max_media_files=value("ODYSSEUS_MEDIA_MAX_FILES", defaults.max_media_files),
        max_image_source_bytes=value(
            "ODYSSEUS_MEDIA_MAX_IMAGE_BYTES", defaults.max_image_source_bytes
        ),
        max_video_source_bytes=value(
            "ODYSSEUS_MEDIA_MAX_VIDEO_BYTES", defaults.max_video_source_bytes
        ),
        max_document_source_bytes=value(
            "ODYSSEUS_MEDIA_MAX_DOCUMENT_BYTES", defaults.max_document_source_bytes
        ),
        max_audio_source_bytes=value(
            "ODYSSEUS_MEDIA_MAX_AUDIO_BYTES", defaults.max_audio_source_bytes
        ),
        max_encoded_bytes=value(
            "ODYSSEUS_MEDIA_MAX_ENCODED_BYTES", defaults.max_encoded_bytes
        ),
        max_inline_document_chars=value(
            "ODYSSEUS_MEDIA_MAX_DOCUMENT_CHARS", defaults.max_inline_document_chars
        ),
        max_dimension=value("ODYSSEUS_MEDIA_MAX_DIMENSION", defaults.max_dimension),
        max_image_pixels=value("ODYSSEUS_MEDIA_MAX_PIXELS", defaults.max_image_pixels),
        max_video_frames=value(
            "ODYSSEUS_MEDIA_MAX_VIDEO_FRAMES", defaults.max_video_frames
        ),
        video_probe_timeout_s=value(
            "ODYSSEUS_MEDIA_PROBE_TIMEOUT", defaults.video_probe_timeout_s
        ),
        video_frame_timeout_s=value(
            "ODYSSEUS_MEDIA_FRAME_TIMEOUT", defaults.video_frame_timeout_s
        ),
    )


def _regular_file_size(path: Path) -> int:
    if path.is_symlink():
        raise ValueError("symbolic links are not accepted")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("path is not a regular file")
    return info.st_size


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _visual_token_estimate(width: int, height: int) -> int:
    # This is trace telemetry, not a provider billing calculation. Tile-based
    # accounting deliberately errs high enough to expose visual context cost.
    return 85 + 170 * math.ceil(width / 512) * math.ceil(height / 512)


def _normalize_image(path: Path, limits: MediaIngressLimits) -> tuple[bytes, int, int]:
    try:
        with Image.open(path) as opened:
            width, height = opened.size
            if width < 1 or height < 1 or width * height > limits.max_image_pixels:
                raise ValueError(f"image dimensions are outside limits: {width}x{height}")
            image = ImageOps.exif_transpose(opened)
            image.seek(0)
            image = image.convert("RGB")
            image.thumbnail((limits.max_dimension, limits.max_dimension))
            width, height = image.size
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=88, optimize=True)
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"invalid image: {exc}") from exc
    return output.getvalue(), width, height


def _data_uri(payload: bytes) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _audio_data_uri(payload: bytes, path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "audio/mpeg"
    if not mime.startswith("audio/"):
        mime = "audio/mpeg"
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def _base64_size(payload: bytes) -> int:
    return 4 * math.ceil(len(payload) / 3)


def _timestamp_label(value: float) -> str:
    minutes, seconds = divmod(value, 60)
    return f"{int(minutes):02d}:{seconds:06.3f}"


def _probe_video(path: Path, limits: MediaIngressLimits) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ValueError("ffprobe is required for video ingress")
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=limits.video_probe_timeout_s,
    )
    duration = float(json.loads(completed.stdout)["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("video has no positive finite duration")
    return duration


def _uniform_timestamps(duration: float, max_frames: int) -> list[float]:
    frame_count = min(max_frames, max(1, math.ceil(duration / 5.0)))
    return [duration * (index + 0.5) / frame_count for index in range(frame_count)]


def _extract_video_frame(
    path: Path,
    timestamp: float,
    target: Path,
    limits: MediaIngressLimits,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("ffmpeg is required for video ingress")
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.6f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-y",
            str(target),
        ],
        check=True,
        capture_output=True,
        timeout=limits.video_frame_timeout_s,
    )


def build_multimodal_user_content(
    prompt: str,
    attachments: Sequence[LocalMediaAttachment],
    *,
    limits: MediaIngressLimits | None = None,
    input_modalities: Sequence[str] | None = None,
) -> MediaIngressResult:
    """Convert bounded local attachments into provider-ready content blocks."""

    active_limits = limits or limits_from_env()
    native_modalities = {
        str(value).strip().lower()
        for value in (input_modalities or ("text", "image"))
        if str(value).strip()
    }
    result = MediaIngressResult(content=[{"type": "text", "text": prompt}])
    encoded_total = 0
    document_chars_remaining = active_limits.max_inline_document_chars

    for attachment in list(attachments)[: active_limits.max_media_files]:
        path = Path(attachment.path)
        source = str(attachment.source_path)
        try:
            size = _regular_file_size(path)
            suffix = path.suffix.lower()
            if suffix not in SUPPORTED_ATTACHMENT_SUFFIXES:
                raise ValueError(f"unsupported attachment extension: {suffix or '(none)'}")
            if suffix in IMAGE_SUFFIXES:
                max_source = active_limits.max_image_source_bytes
            elif suffix in VIDEO_SUFFIXES:
                max_source = active_limits.max_video_source_bytes
            elif suffix in DOCUMENT_SUFFIXES:
                max_source = active_limits.max_document_source_bytes
            else:
                max_source = active_limits.max_audio_source_bytes
            if size > max_source:
                raise ValueError(f"source is {size} bytes, limit is {max_source}")
            source_hash = _sha256(path)

            if suffix in DOCUMENT_SUFFIXES:
                from src.document_processor import extract_local_document

                extracted = extract_local_document(
                    str(path),
                    display_name=Path(source).name,
                    analyze_embedded_images=False,
                ).strip()
                available = max(0, document_chars_remaining)
                visible = extracted[:available]
                truncated = len(visible) < len(extracted)
                document_chars_remaining -= len(visible)
                if not visible:
                    visible = "[Document content omitted: inline document budget exhausted.]"
                elif truncated:
                    visible += "\n[Document content truncated by shared inline budget.]"
                result.content.append({
                    "type": "text",
                    "text": (
                        f"[Document source={source} sha256={source_hash}]\n{visible}"
                    ),
                })
                result.artifacts.append(MediaArtifact(
                    source_path=source,
                    modality="document",
                    source_bytes=size,
                    source_sha256=source_hash,
                    extracted_chars=min(len(extracted), available),
                    truncated=truncated,
                ))
                continue

            if suffix in AUDIO_SUFFIXES:
                payload = path.read_bytes()
                native_audio = "audio" in native_modalities
                encoded_size = _base64_size(payload) if native_audio else 0
                if native_audio and encoded_total + encoded_size > active_limits.max_encoded_bytes:
                    raise ValueError("combined encoded media limit exceeded")
                status = (
                    "native audio input attached"
                    if native_audio
                    else "native audio input unavailable; inspect with workspace tools"
                )
                result.content.append({
                    "type": "text",
                    "text": f"[Audio source={source} sha256={source_hash}; {status}]",
                })
                if native_audio:
                    result.content.append({
                        "type": "audio",
                        "audio": {"url": _audio_data_uri(payload, path)},
                    })
                    encoded_total += encoded_size
                result.artifacts.append(MediaArtifact(
                    source_path=source,
                    modality="audio",
                    source_bytes=size,
                    source_sha256=source_hash,
                    encoded_bytes=encoded_size,
                ))
                continue

            if suffix in IMAGE_SUFFIXES:
                payload, width, height = _normalize_image(path, active_limits)
                encoded_size = _base64_size(payload)
                if encoded_total + encoded_size > active_limits.max_encoded_bytes:
                    raise ValueError("combined encoded media limit exceeded")
                artifact = MediaArtifact(
                    source_path=source,
                    modality="image",
                    source_bytes=size,
                    source_sha256=source_hash,
                    encoded_bytes=encoded_size,
                    width=width,
                    height=height,
                    estimated_visual_tokens=_visual_token_estimate(width, height),
                )
                result.content.extend(
                    [
                        {
                            "type": "text",
                            "text": f"[Image source={source} sha256={source_hash}]",
                        },
                        {"type": "image_url", "image_url": {"url": _data_uri(payload)}},
                    ]
                )
                encoded_total += encoded_size
                result.artifacts.append(artifact)
                continue

            duration = _probe_video(path, active_limits)
            timestamps = _uniform_timestamps(duration, active_limits.max_video_frames)
            artifact = MediaArtifact(
                source_path=source,
                modality="video",
                source_bytes=size,
                source_sha256=source_hash,
                duration_s=round(duration, 6),
                frame_timestamps_s=[round(value, 6) for value in timestamps],
            )
            media_blocks: list[dict[str, Any]] = []
            attachment_encoded_total = 0
            with tempfile.TemporaryDirectory(prefix="odysseus-video-frames-") as temp_dir:
                for index, timestamp in enumerate(timestamps):
                    frame_path = Path(temp_dir) / f"frame-{index:03d}.png"
                    _extract_video_frame(path, timestamp, frame_path, active_limits)
                    payload, width, height = _normalize_image(frame_path, active_limits)
                    encoded_size = _base64_size(payload)
                    if (
                        encoded_total + attachment_encoded_total + encoded_size
                        > active_limits.max_encoded_bytes
                    ):
                        raise ValueError("combined encoded media limit exceeded")
                    media_blocks.extend(
                        [
                            {
                                "type": "text",
                                "text": (
                                    f"[Video frame source={source} "
                                    f"timestamp={_timestamp_label(timestamp)} "
                                    f"sha256={source_hash}]"
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": _data_uri(payload)}},
                        ]
                    )
                    attachment_encoded_total += encoded_size
                    artifact.encoded_bytes += encoded_size
                    artifact.width = max(artifact.width or 0, width)
                    artifact.height = max(artifact.height or 0, height)
                    artifact.estimated_visual_tokens += _visual_token_estimate(width, height)
            result.content.extend(media_blocks)
            encoded_total += attachment_encoded_total
            result.artifacts.append(artifact)
        except (
            ImportError,
            KeyError,
            RuntimeError,
            ValueError,
            OSError,
            subprocess.SubprocessError,
        ) as exc:
            result.warnings.append(f"{source}: {exc}")

    if len(attachments) > active_limits.max_media_files:
        result.warnings.append(
            f"attachment file count capped at {active_limits.max_media_files}; "
            f"skipped {len(attachments) - active_limits.max_media_files}"
        )
    return result
