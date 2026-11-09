import base64
import hashlib
import io
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from src.llm_core import _sanitize_llm_messages
from src.media_ingress import (
    LocalMediaAttachment,
    MediaIngressLimits,
    _uniform_timestamps,
    build_multimodal_user_content,
)


def _write_image(path: Path, *, size: tuple[int, int] = (320, 180)) -> None:
    Image.new("RGB", size, (30, 160, 90)).save(path)


def test_image_ingress_hashes_normalizes_and_emits_openai_blocks(tmp_path):
    source = tmp_path / "evidence.png"
    _write_image(source, size=(2200, 1100))

    result = build_multimodal_user_content(
        "Inspect evidence.png",
        [LocalMediaAttachment(source, "/workspace/evidence.png")],
        limits=MediaIngressLimits(max_dimension=800),
    )

    assert [block["type"] for block in result.content] == ["text", "text", "image_url"]
    assert result.content[0]["text"] == "Inspect evidence.png"
    assert "/workspace/evidence.png" in result.content[1]["text"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() in result.content[1]["text"]
    url = result.content[2]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    decoded = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert decoded.size == (800, 400)
    artifact = result.artifacts[0]
    assert (artifact.width, artifact.height) == (800, 400)
    assert artifact.encoded_bytes == len(url.split(",", 1)[1])
    assert result.estimated_visual_tokens > 0
    assert "base64" not in str(result.metadata())


def test_ingress_rejects_symlink_without_leaking_target(tmp_path):
    target = tmp_path / "target.png"
    link = tmp_path / "link.png"
    _write_image(target)
    link.symlink_to(target)

    result = build_multimodal_user_content(
        "Inspect link.png",
        [LocalMediaAttachment(link, "/workspace/link.png")],
    )

    assert result.content == [{"type": "text", "text": "Inspect link.png"}]
    assert not result.artifacts
    assert "symbolic links" in result.warnings[0]


def test_ingress_enforces_count_and_source_byte_limits(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    _write_image(first)
    _write_image(second)

    result = build_multimodal_user_content(
        "Inspect files",
        [
            LocalMediaAttachment(first, "/workspace/first.png"),
            LocalMediaAttachment(second, "/workspace/second.png"),
        ],
        limits=MediaIngressLimits(max_media_files=1, max_image_source_bytes=10),
    )

    assert not result.artifacts
    assert any("source is" in warning for warning in result.warnings)
    assert any("file count capped" in warning for warning in result.warnings)


def test_uniform_video_timestamps_cover_the_full_duration():
    timestamps = _uniform_timestamps(40.0, 8)
    assert timestamps == [2.5, 7.5, 12.5, 17.5, 22.5, 27.5, 32.5, 37.5]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg unavailable")
def test_video_ingress_extracts_timestamped_frames(tmp_path):
    video = tmp_path / "evidence.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:d=2",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(video),
        ],
        check=True,
        timeout=20,
    )

    result = build_multimodal_user_content(
        "Inspect evidence.mp4",
        [LocalMediaAttachment(video, "/workspace/evidence.mp4")],
        limits=MediaIngressLimits(max_video_frames=2),
    )

    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    assert artifact.modality == "video"
    assert artifact.frame_timestamps_s == [1.0]
    assert any("timestamp=00:01.000" in block.get("text", "") for block in result.content)
    assert sum(block["type"] == "image_url" for block in result.content) == 1


def test_provider_sanitizer_preserves_ingested_image_blocks(tmp_path):
    source = tmp_path / "evidence.png"
    _write_image(source)
    ingress = build_multimodal_user_content(
        "Inspect evidence.png",
        [LocalMediaAttachment(source, "/workspace/evidence.png")],
    )

    sanitized = _sanitize_llm_messages([{"role": "user", "content": ingress.content}])

    assert sanitized[0]["content"] == ingress.content


def test_document_ingress_extracts_text_with_shared_budget_and_metadata(tmp_path):
    source = tmp_path / "brief.txt"
    source.write_text("alpha beta gamma\n" * 20, encoding="utf-8")

    result = build_multimodal_user_content(
        "Read brief.txt",
        [LocalMediaAttachment(source, "/workspace/brief.txt")],
        limits=MediaIngressLimits(max_inline_document_chars=100),
    )

    assert [block["type"] for block in result.content] == ["text", "text"]
    assert "Document source=/workspace/brief.txt" in result.content[1]["text"]
    assert "alpha beta gamma" in result.content[1]["text"]
    assert "truncated by shared inline budget" in result.content[1]["text"]
    artifact = result.artifacts[0]
    assert artifact.modality == "document"
    assert artifact.extracted_chars == 100
    assert artifact.truncated is True
    assert "alpha beta gamma" not in str(result.metadata())


def test_audio_ingress_falls_back_to_verified_workspace_path(tmp_path):
    source = tmp_path / "sample.wav"
    source.write_bytes(b"RIFF" + b"\x00" * 32)

    result = build_multimodal_user_content(
        "Inspect sample.wav",
        [LocalMediaAttachment(source, "/workspace/sample.wav")],
    )

    assert [block["type"] for block in result.content] == ["text", "text"]
    assert "inspect with workspace tools" in result.content[1]["text"]
    assert result.artifacts[0].modality == "audio"
    assert result.artifacts[0].encoded_bytes == 0


def test_audio_ingress_emits_native_block_only_when_enabled(tmp_path):
    source = tmp_path / "sample.wav"
    source.write_bytes(b"RIFF" + b"\x00" * 32)

    result = build_multimodal_user_content(
        "Listen to sample.wav",
        [LocalMediaAttachment(source, "/workspace/sample.wav")],
        input_modalities=["text", "audio"],
    )

    assert [block["type"] for block in result.content] == ["text", "text", "audio"]
    assert result.content[2]["audio"]["url"].startswith("data:audio/")
    assert result.artifacts[0].encoded_bytes > 0
