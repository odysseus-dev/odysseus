import asyncio
import base64
import functools
import io
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from PIL import Image

from src.agent_tools.media_tools import (
    InspectMediaTool,
    TranscribeMediaTool,
    _parse_seconds,
    _parse_video_position,
    _resolve_media_path,
)
from src.tool_execution import _active_workspace
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS


@functools.lru_cache(maxsize=None)
def _ffmpeg_has_encoder(name: str) -> bool:
    """Whether the ffmpeg on PATH was built with the named encoder.

    Codec support is a build option, not something the project requires. The
    Homebrew ffmpeg on macOS ships without libwebp, for instance, so a test
    that asserts a successful `.webp` export there fails on the build rather
    than on the tool.
    """
    if not shutil.which("ffmpeg"):
        return False
    listed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-encoders"],
        check=False, capture_output=True, text=True,
    )
    return any(
        line.split()[1:2] == [name] or f"(codec {name})" in line
        for line in listed.stdout.splitlines()
        if line.strip()
    )


def test_media_timestamp_parser_accepts_units_and_four_field_timecodes():
    assert _parse_seconds("0m", default=-1) == 0
    assert _parse_seconds("30m", default=-1) == 1800
    assert _parse_seconds("1h2m3.5s", default=-1) == pytest.approx(3723.5)
    assert _parse_seconds("00:01:00:50", default=-1) == pytest.approx(60.5)
    assert _parse_seconds("00:00:15:30", default=-1) == pytest.approx(15.3)


def test_media_timestamp_parser_accepts_natural_video_positions():
    assert _parse_seconds("start", default=-1) == 0
    assert _parse_video_position("beginning", default=-1, duration=120) == 0
    assert _parse_video_position("middle", default=-1, duration=120) == 60
    assert _parse_video_position("end", default=-1, duration=120) == 120


def test_inspect_media_schema_requires_workspace_export_destinations():
    schema = next(
        item["function"]
        for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_media"
    )
    properties = schema["parameters"]["properties"]

    assert schema["parameters"]["additionalProperties"] is False
    assert "/workspace" in properties["output_path"]["description"]
    assert "/workspace" in properties["exports"]["description"]
    assert "/workspace" in (
        properties["exports"]["items"]["properties"]["output_path"]["description"]
    )
    assert "distribute an explicit frames budget" in properties["segments"]["description"]


def test_inspect_media_ignores_unknown_arguments_with_visible_feedback(tmp_path: Path):
    Image.new("RGB", (16, 16), "blue").save(tmp_path / "source.png")
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.png",
            "end_title": "00:01:30",
            "segment_count": 6,
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert "Ignored unknown argument(s): `end_title`, `segment_count`" in result["output"]
    assert "advertised inspect_media schema in future calls" in result["output"]


def test_inspect_media_applies_still_image_crop_and_preview_dimension(tmp_path: Path):
    source = Image.new("RGB", (100, 80), "red")
    for x in range(50, 100):
        for y in range(80):
            source.putpixel((x, y), (0, 0, 255))
    source.save(tmp_path / "split.png")

    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/split.png",
            "crop": {"x": 50, "y": 0, "width": 50, "height": 80},
            "max_dimension": 256,
            "frames": 4,
            "sampling": "uniform",
        }), {}))
        invalid = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/split.png",
            "crop": {"x": 90, "y": 0, "width": 20, "height": 80},
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["source_dimensions"] == [100, 80]
    assert result["preview_dimensions"] == [50, 80]
    preview = Image.open(io.BytesIO(base64.b64decode(result["images"][0]["data"])))
    assert preview.size == (50, 80)
    assert preview.convert("RGB").getpixel((25, 40))[2] > 200
    assert "Applied pixel crop x=50" in result["output"]
    assert "Ignored video/PDF-only arguments" in result["output"]
    assert invalid["exit_code"] == 1
    assert "stay inside the source image (100x80)" in invalid["error"]

    token = _active_workspace.set(str(tmp_path))
    try:
        legacy_alias = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/split.png",
            "export": json.dumps({
                "crop": {"x": 50, "y": 0, "width": 50, "height": 80},
                "caption": "inspect the blue half",
            }),
        }), {}))
        malformed_alias = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/split.png",
            "export": "please export a detailed view",
        }), {}))
    finally:
        _active_workspace.reset(token)
    assert legacy_alias["exit_code"] == 0
    assert legacy_alias["preview_dimensions"] == [50, 80]
    assert "Normalized inspection-only `export` object" in legacy_alias["output"]
    assert malformed_alias["exit_code"] == 1
    assert "must be a JSON object or array" in malformed_alias["error"]


def test_inspect_media_clamps_numeric_preview_dimension_with_feedback(tmp_path: Path):
    Image.new("RGB", (1200, 800), "blue").save(tmp_path / "source.png")
    token = _active_workspace.set(str(tmp_path))
    try:
        high = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.png",
            "max_dimension": 1100,
        }), {}))
        low = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.png",
            "max_dimension": 128,
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert high["exit_code"] == 0
    assert high["preview_dimensions"] == [1024, 683]
    assert "Clamped `max_dimension` from 1100 to 1024" in high["output"]
    assert low["exit_code"] == 0
    assert low["preview_dimensions"] == [256, 171]
    assert "Clamped `max_dimension` from 128 to 256" in low["output"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_normalizes_automatic_timestamp_sentinel(tmp_path: Path):
    video = tmp_path / "video.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=320x180:rate=10:duration=2", "-pix_fmt", "yuv420p",
        "-y", str(video),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "timestamp": "auto",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0, result
    assert len(result["frame_timestamps"]) == 4, result
    assert "Normalized automatic `timestamp` to representative frame sampling" in result["output"], result

@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_returns_ordered_timestamped_video_frames(tmp_path: Path):
    video = tmp_path / "video.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=960x540:rate=4:duration=2", "-pix_fmt", "yuv420p",
        "-y", str(video),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "start": 0,
            "end": 2,
            "frames": 3,
            "query": "find the changing test pattern",
        }), {}))
        implicit_default = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "start": 0,
            "end": 2,
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert len(result["images"]) == 3
    assert all(image["mimeType"] == "image/jpeg" and image["data"] for image in result["images"])
    assert result["frame_timestamps"] == pytest.approx([1 / 3, 1, 5 / 3])
    assert "Frames follow in this exact order" in result["output"]
    assert "Inspection target: find the changing test pattern" in result["output"]
    assert implicit_default["exit_code"] == 0
    assert len(implicit_default["images"]) == 4
    assert Image.open(io.BytesIO(base64.b64decode(implicit_default["images"][0]["data"]))).size == (512, 288)

    token = _active_workspace.set(str(tmp_path))
    try:
        capped = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
                "start": 0,
                "end": 1.5,
                "frames": 16,
                "sampling": "motion",
        }), {}))
    finally:
        _active_workspace.reset(token)
    assert capped["exit_code"] == 0
    assert len(capped["images"]) == 2
    assert len(capped["frame_timestamps"]) == 16
    assert capped["requested_frames"] == 16
    assert capped["frame_limit"] == 64
    assert capped["contact_sheet_observations"] == 16
    assert "Packed 16 motion observations" in capped["output"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_treats_suffixless_directory_as_sampling_destination(tmp_path: Path):
    video = tmp_path / "video.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=320x180:rate=4:duration=2", "-pix_fmt", "yuv420p",
        "-y", str(video),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "segments": [{"start": 0, "end": 1}, {"start": 1, "end": 2}],
            "frames": 4,
            "output_path": "/workspace/.odysseus",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert len(result["images"]) == 4
    assert "Ignored suffixless output_path directory" in result["output"]



    token = _active_workspace.set(str(tmp_path))
    try:
        normalized = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "pages": 1,
            "page": 1,
            "speed": 4,
            "caption": "find the rally",
            "queries": ["rally", "score"],
        }), {}))
    finally:
        _active_workspace.reset(token)
    assert normalized["exit_code"] == 0
    assert len(normalized["images"]) == 1
    assert normalized["requested_frames"] == 1
    assert "Normalized PDF-only `pages` to video `frames`" in normalized["output"]
    assert "frame inspection does not play or speed up video" in normalized["output"]
    assert "does not semantically analyze video" in normalized["output"]
    assert "Ignored unknown `queries`" in normalized["output"]
    assert "Ignored PDF-only `page` for video" in normalized["output"]

    token = _active_workspace.set(str(tmp_path))
    try:
        normalized_page_list = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "pages": [1, 2],
            "sampling": "scene",
        }), {}))
    finally:
        _active_workspace.reset(token)
    assert normalized_page_list["exit_code"] == 0
    assert normalized_page_list["requested_frames"] == 2
    assert len(normalized_page_list["images"]) == 2
    assert "Normalized PDF-style `pages` list" in normalized_page_list["output"]

    token = _active_workspace.set(str(tmp_path))
    try:
        stringified_page_list = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "pages": "[1, 2, 3]",
            "sampling": "scene",
        }), {}))
    finally:
        _active_workspace.reset(token)
    assert stringified_page_list["exit_code"] == 0
    assert stringified_page_list["requested_frames"] == 3
    assert "Normalized PDF-style `pages` list" in stringified_page_list["output"]

    token = _active_workspace.set(str(tmp_path))
    try:
        exact = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "timestamp": 0.75,
        }), {}))
    finally:
        _active_workspace.reset(token)
    assert exact["exit_code"] == 0
    assert exact["sampling"] == "timestamp"
    assert exact["frame_timestamps"] == [0.75]
    assert len(exact["images"]) == 1
    assert "exact still inspection" in exact["output"]

    token = _active_workspace.set(str(tmp_path))
    try:
        symbolic_full_duration = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "duration": "full",
            "frames": 16,
            "sampling": "uniform",
        }), {}))
    finally:
        _active_workspace.reset(token)
    assert symbolic_full_duration["exit_code"] == 0
    assert symbolic_full_duration["frame_timestamps"][0] > 0
    assert symbolic_full_duration["frame_timestamps"][-1] < 2
    assert "Normalized symbolic `duration`" in symbolic_full_duration["output"]

    token = _active_workspace.set(str(tmp_path))
    try:
        automatic_duration = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "duration": "auto",
            "frames": 4,
            "sampling": "uniform",
        }), {}))
    finally:
        _active_workspace.reset(token)
    assert automatic_duration["exit_code"] == 0, automatic_duration
    assert len(automatic_duration["frame_timestamps"]) == 4
    assert "Normalized symbolic `duration`" in automatic_duration["output"]

    token = _active_workspace.set(str(tmp_path))
    try:
        symbolic_max_duration = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "duration": "max",
            "frames": 4,
            "sampling": "unifor",
        }), {}))
    finally:
        _active_workspace.reset(token)
    assert symbolic_max_duration["exit_code"] == 0
    assert symbolic_max_duration["sampling"] == "uniform"
    assert "Normalized symbolic `duration`" in symbolic_max_duration["output"]
    assert "Normalized sampling `unifor` to `uniform`" in symbolic_max_duration["output"]

    token = _active_workspace.set(str(tmp_path))
    try:
        symbolic_overview_duration = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "duration": "overview",
            "sampling": "overview",
            "frames": 12,
        }), {}))
    finally:
        _active_workspace.reset(token)
    assert symbolic_overview_duration["exit_code"] == 0
    assert len(symbolic_overview_duration["frame_timestamps"]) == 12
    assert "Normalized symbolic `duration`" in symbolic_overview_duration["output"]

    token = _active_workspace.set(str(tmp_path))
    try:
        range_form_duration = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "duration": "00:00:00.250-00:00:01.250",
            "frames": 2,
        }), {}))
    finally:
        _active_workspace.reset(token)
    assert range_form_duration["exit_code"] == 0
    assert range_form_duration["frame_timestamps"] == pytest.approx([0.5, 1.0])
    assert "Normalized range-form `duration`" in range_form_duration["output"]

    token = _active_workspace.set(str(tmp_path))
    try:
        slash_range_duration = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "duration": "00:00:00.250/00:00:01.250",
            "frames": 2,
        }), {}))
    finally:
        _active_workspace.reset(token)
    assert slash_range_duration["exit_code"] == 0
    assert slash_range_duration["frame_timestamps"] == pytest.approx([0.5, 1.0])
    assert "Normalized range-form `duration`" in slash_range_duration["output"]

    token = _active_workspace.set(str(tmp_path))
    try:
        duration_range = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "start": 0.5,
            "duration": "0.75s",
            "frames": 1,
        }), {}))
    finally:
        _active_workspace.reset(token)
    assert duration_range["exit_code"] == 0
    assert duration_range["frame_timestamps"] == pytest.approx([0.875])
    assert "Normalized `duration` to an end position relative to `start`" in duration_range["output"]

    token = _active_workspace.set(str(tmp_path))
    try:
        range_alias = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "time_range": "00:00:00.5-00:00:01.5",
            "frames": 1,
        }), {}))
        observe_alias = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "observe": "overview",
            "frames": 12,
        }), {}))
        timing_alias = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "timing": "uniform",
            "frames": 12,
            "frames_per_page": "16",
        }), {}))
    finally:
        _active_workspace.reset(token)
    assert range_alias["exit_code"] == 0
    assert range_alias["frame_timestamps"] == pytest.approx([1.0])
    assert "Normalized `time_range` to explicit `start` and `end`" in range_alias["output"]
    assert observe_alias["exit_code"] == 0
    assert observe_alias["sampling"] == "overview"
    assert "Normalized `observe` to `sampling`" in observe_alias["output"]
    assert timing_alias["exit_code"] == 0
    assert timing_alias["sampling"] == "overview"
    assert len(timing_alias["frame_timestamps"]) == 12
    assert "Normalized `timing` to `sampling`" in timing_alias["output"]
    assert "Ignored `frames_per_page` layout hint" in timing_alias["output"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_normalizes_ordered_end_boundaries_but_rejects_mixed_incomplete_segments(tmp_path: Path):
    video = tmp_path / "video.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=320x180:rate=4:duration=2", "-pix_fmt", "yuv420p",
        "-y", str(video),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        end_boundaries = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "frames": 2,
            "segments": [{"end": "00:00:01"}, {"end": "00:00:02"}],
        }), {}))
        mixed_incomplete = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "segments": [{"end": "00:00:01"}, {"start": "00:00:01"}],
        }), {}))
        ambiguous = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "timestamp": "00:00:01",
            "output_path": "/workspace/clip.mp4",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert end_boundaries["exit_code"] == 0
    assert end_boundaries["frame_timestamps"] == pytest.approx([0.5, 1.5])
    assert "Interpreted ordered end-only segment boundaries" in end_boundaries["output"]
    assert mixed_incomplete["exit_code"] == 1
    assert "explicitly contain both start and end" in mixed_incomplete["error"]
    assert ambiguous["exit_code"] == 1
    assert "timestamp selects a still image" in ambiguous["error"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_exports_final_decodable_frame_at_exact_duration(tmp_path: Path):
    video = tmp_path / "video.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=320x180:rate=4:duration=2", "-pix_fmt", "yuv420p",
        "-y", str(video),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "timestamp": "end",
            # PNG, not WebP: this asserts that the *final* frame is decodable at
            # the exact duration, so it must not also depend on an optional
            # ffmpeg encoder. WebP export is covered separately below.
            "output_path": "/workspace/final.png",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0, result
    assert (tmp_path / "final.png").stat().st_size > 0

    token = _active_workspace.set(str(tmp_path))
    try:
        high_detail = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "start": 0,
            "end": 2,
            "frames": 1,
            "max_dimension": 768,
        }), {}))
    finally:
        _active_workspace.reset(token)
    assert high_detail["exit_code"] == 0
    assert Image.open(io.BytesIO(base64.b64decode(high_detail["images"][0]["data"]))).size == (768, 432)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
@pytest.mark.skipif(not _ffmpeg_has_encoder("webp"), reason="ffmpeg built without a webp encoder")
def test_inspect_media_exports_a_webp_still(tmp_path: Path):
    """A `.webp` output_path is passed straight through to ffmpeg.

    Guarded on the encoder rather than asserted unconditionally: WebP is a
    build option (Homebrew's macOS ffmpeg omits it) and the project does not
    require it. When the encoder is missing the tool reports ffmpeg's failure
    with `exit_code` 1, which is covered by
    `test_inspect_media_reports_a_missing_encoder_instead_of_crashing`.
    """
    video = tmp_path / "video.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=320x180:rate=4:duration=2", "-pix_fmt", "yuv420p",
        "-y", str(video),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "timestamp": "end",
            "output_path": "/workspace/final.webp",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0, result
    assert Image.open(tmp_path / "final.webp").format == "WEBP"


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
@pytest.mark.skipif(_ffmpeg_has_encoder("webp"), reason="needs an ffmpeg built without webp")
def test_inspect_media_reports_a_missing_encoder_instead_of_crashing(tmp_path: Path):
    """An export in a format this ffmpeg cannot encode fails as a tool error.

    The tool does not probe the encoder list, so the only contract it can keep
    is to surface ffmpeg's own failure rather than raise or write a truncated
    file. Asserted only on builds that actually lack the encoder.
    """
    video = tmp_path / "video.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=320x180:rate=4:duration=2", "-pix_fmt", "yuv420p",
        "-y", str(video),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "timestamp": "end",
            "output_path": "/workspace/final.webp",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 1
    assert "ffmpeg still extraction failed" in result["error"]
    assert not (tmp_path / "final.webp").exists()


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_rejects_ambiguous_multi_frame_single_image_export(tmp_path: Path):
    video = tmp_path / "video.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=320x180:rate=4:duration=2", "-pix_fmt", "yuv420p",
        "-y", str(video),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        ambiguous = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "frames": 12,
            "sampling": "scene",
            "output_path": "/workspace/evidence.png",
        }), {}))
        single = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4",
            "frames": 1,
            "output_path": "/workspace/single.png",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert ambiguous["exit_code"] == 1
    assert "image output_path requires one explicit timestamp" in ambiguous["error"]
    assert "exports=[...]" in ambiguous["error"]
    assert not (tmp_path / "evidence.png").exists()
    assert single["exit_code"] == 0
    assert (tmp_path / "single.png").is_file()


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_scene_sampling_includes_visual_cuts(tmp_path: Path):
    video = tmp_path / "cuts.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=red:s=320x180:d=1:r=10",
        "-f", "lavfi", "-i", "color=blue:s=320x180:d=1:r=10",
        "-f", "lavfi", "-i", "color=green:s=320x180:d=1:r=10",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0",
        "-c:v", "libx264", "-y", str(video),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/cuts.mp4",
            "frames": 3,
            "sampling": "scene",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["sampling"] == "scene"
    assert any(abs(value - 1.05) < 0.25 for value in result["frame_timestamps"])
    assert any(abs(value - 2.05) < 0.25 for value in result["frame_timestamps"])
    assert len(result["images"]) == 3

    token = _active_workspace.set(str(tmp_path))
    try:
        motion = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/cuts.mp4",
            "frames": 3,
            "sampling": "motion",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert motion["exit_code"] == 0
    assert motion["sampling"] == "motion"
    assert motion["motion_candidates_detected"] >= 2
    assert any(abs(value - 1.05) < 0.35 for value in motion["frame_timestamps"])
    assert any(abs(value - 2.05) < 0.35 for value in motion["frame_timestamps"])


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_overview_packs_dense_timeline_into_contact_sheets(tmp_path: Path):
    video = tmp_path / "overview.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=8:size=320x180:rate=10", "-y", str(video),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/overview.mp4",
            "sampling": "overview",
            "frames": 12,
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["sampling"] == "overview"
    assert len(result["frame_timestamps"]) == 12
    assert result["overview_observations"] == 12
    assert len(result["images"]) == 2
    assert "contact sheets" in result["output"]
    assert "row-major" in result["output"]
    sheet = Image.open(io.BytesIO(base64.b64decode(result["images"][0]["data"])))
    assert sheet.width <= 1024
    assert sheet.height <= 1024

    token = _active_workspace.set(str(tmp_path))
    try:
        implicit = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/overview.mp4",
            "sampling": "overview",
            "pages": 4,
        }), {}))
        segmented = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/overview.mp4",
            "sampling": "overview",
            "frames": 64,
            "segments": [{"start": 0, "end": 2}],
        }), {}))
        normalized = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/overview.mp4",
            "sampling": "uniform",
            "frames": 12,
        }), {}))
        bounded = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/overview.mp4",
            "sampling": "uniform",
            "frames": 16,
            "output_path": "/workspace/bounded.mp4",
            "start": 0,
            "end": 2,
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert implicit["overview_observations"] == 48
    assert len(implicit["images"]) == 6
    assert "Ignored PDF-only `pages` for video overview" in implicit["output"]
    assert segmented["frame_limit"] == 64
    assert len(segmented["frame_timestamps"]) == 64
    assert len(segmented["images"]) == 8
    assert segmented["contact_sheet_observations"] == 64
    assert normalized["sampling"] == "overview"
    assert normalized["overview_observations"] == 12
    assert len(normalized["images"]) == 2
    assert "Normalized uniform video sampling above 8 observations" in normalized["output"]
    assert bounded["exit_code"] == 0
    assert "Capped video inspection from 16 requested observations to 8" in bounded["output"]


def test_inspect_media_schema_exposes_dense_overview_without_unbounding_images():
    schema = next(
        item["function"]
        for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_media"
    )
    properties = schema["parameters"]["properties"]
    assert "overview" in properties["sampling"]["enum"]
    assert properties["frames"]["maximum"] == 64
    assert "contact sheet" in properties["sampling"]["description"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_sampled_video_ignores_spurious_nonartifact_output_path(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=2:size=160x120:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "start": 0,
            "end": 1,
            "frames": 4,
            "output_path": "/workspace/inspection.json",
        }), {"client_runtime_context": {"completion_requirements": {}}}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert "Ignored non-artifact output_path" in result["output"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_sampled_video_rejects_unsupported_required_output_path(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=2:size=160x120:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "start": 0,
            "end": 1,
            "frames": 4,
            "output_path": "/workspace/inspection.json",
        }), {"client_runtime_context": {"completion_requirements": {
            "required_artifacts": ["/workspace/inspection.json"],
        }}}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 1
    assert "supported image or video file" in result["error"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_video_timestamp_plus_end_normalizes_to_range_start(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=2:size=160x120:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "timestamp": 0.5,
            "end": 1.5,
            "frames": 4,
            "output_path": "/workspace/clip.mp4",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert "Sampled range: 00:00:00.500 - 00:00:01.500" in result["output"]
    assert "Normalized timestamp plus end" in result["output"]


def test_inspect_media_rejects_path_outside_workspace(tmp_path: Path):
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(
            json.dumps({"path": "/etc/passwd"}), {}
        ))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 1
    assert "inside the active workspace" in result["error"]


def test_media_input_repairs_missing_virtual_workspace_prefix(tmp_path: Path):
    fixture = tmp_path / "fixtures" / "video.mp4"
    fixture.parent.mkdir()
    fixture.write_bytes(b"fixture")
    token = _active_workspace.set(str(tmp_path))
    try:
        resolved = _resolve_media_path("/fixtures/video.mp4")
    finally:
        _active_workspace.reset(token)

    assert resolved == fixture


def test_inspect_media_path_error_names_inspect_media(tmp_path: Path):
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(
            json.dumps({"path": "/tmp/outside.mp4"}), {}
        ))
    finally:
        _active_workspace.reset(token)

    assert result == {
        "error": "inspect_media path must stay inside the active workspace",
        "exit_code": 1,
    }


def test_inspect_media_names_unconfined_export_field(tmp_path: Path, monkeypatch):
    (tmp_path / "source.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>',
        encoding="utf-8",
    )
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rsvg-convert")
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.svg",
            "output_path": "/tmp/render.png",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result == {
        "error": "inspect_media output_path must stay inside the active workspace",
        "exit_code": 1,
    }


def test_inspect_media_falls_back_to_imagemagick_for_svg(tmp_path: Path, monkeypatch):
    from src.agent_tools import media_tools

    (tmp_path / "source.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>',
        encoding="utf-8",
    )
    commands = []

    def which(name):
        return "/usr/bin/convert" if name == "convert" else None

    def render(command, timeout):
        commands.append(command)
        Image.new("RGB", (10, 10), "white").save(command[-1])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(media_tools.shutil, "which", which)
    monkeypatch.setattr(media_tools, "_run", render)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.svg",
            "output_path": "/workspace/rendered.png",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert commands == [[
        "/usr/bin/convert", str(tmp_path / "source.svg"),
        str(tmp_path / "rendered.png"),
    ]]
    assert (tmp_path / "rendered.png").read_bytes().startswith(b"\x89PNG")


@pytest.mark.skipif(not shutil.which("rsvg-convert"), reason="rsvg-convert required")
def test_inspect_media_renders_svg_to_png(tmp_path: Path):
    (tmp_path / "floorplan.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40">'
        '<rect width="80" height="40" fill="#336699"/></svg>',
        encoding="utf-8",
    )
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/floorplan.svg",
            "output_path": "/workspace/floorplan.png",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["output_path"] == "/workspace/floorplan.png"
    assert result["images"][0]["mimeType"] == "image/png"
    assert (tmp_path / "floorplan.png").read_bytes().startswith(b"\x89PNG")


def test_inspect_media_can_create_confined_video_clip(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", "testsrc2=duration=2:size=160x120:rate=10", "-y", str(source),
        ],
        check=True,
    )
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(
            json.dumps({
                "path": "/workspace/source.mp4", "start": 0.5, "end": 1.5,
                "frames": 1, "output_path": "/workspace/clip.mp4",
                "timestamp_path": "/workspace/timestamp.txt",
            }),
            {},
        ))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["output_path"] == "/workspace/clip.mp4"
    assert result["timestamp_path"] == "/workspace/timestamp.txt"
    assert (tmp_path / "clip.mp4").stat().st_size > 0
    assert (tmp_path / "timestamp.txt").read_text() == "00:00:00 - 00:00:01\n"


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_accepts_single_clip_wrapped_in_exports(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=2:size=160x120:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4", "start": 0.5, "end": 1.5,
            "exports": [{"timestamp": 0.5, "output_path": "/workspace/clip.mp4"}],
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["output_path"] == "/workspace/clip.mp4"
    assert (tmp_path / "clip.mp4").stat().st_size > 0


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_rejects_timestamp_only_video_clip_export(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=2:size=160x120:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "exports": [{"timestamp": 0.5, "output_path": "/workspace/clip.mp4"}],
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result == {
        "error": (
            "a video clip export requires explicit start and end; "
            "timestamp is only for a still-image export"
        ),
        "exit_code": 1,
    }
    assert not (tmp_path / "clip.mp4").exists()


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_can_create_two_x_video_clip(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=2:size=160x120:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4", "start": 0, "end": 2,
            "speed": 2, "frames": 1, "output_path": "/workspace/fast.mp4",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert "2x speed" in result["output"]
    duration = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(tmp_path / "fast.mp4"),
    ], text=True))
    assert duration == pytest.approx(1.0, abs=0.2)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_can_concatenate_video_segments(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=4:size=160x120:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "segments": [{"start": 0, "end": 1}, {"start": 2, "end": 3}],
            "output_path": "/workspace/combined.mp4",
            "timestamp_path": "/workspace/ranges.txt",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert "Created concatenated clip" in result["output"]
    duration = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(tmp_path / "combined.mp4"),
    ], text=True))
    assert duration == pytest.approx(2.0, abs=0.25)
    assert (tmp_path / "ranges.txt").read_text() == (
        "00:00:00 - 00:00:01\n00:00:02 - 00:00:03\n"
    )


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_uses_webm_compatible_codecs(tmp_path: Path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "clip.webm"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=2:size=160x120:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "start": 0,
            "end": 1,
            "output_path": "/workspace/clip.webm",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert output.is_file()
    codecs = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_name",
        "-of", "csv=p=0", str(output),
    ], text=True).split()
    assert "vp9" in codecs


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_can_export_captioned_video_still(tmp_path: Path):
    pytest.importorskip("PIL.Image")
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=2:size=320x180:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "timestamp": 0.25,
            "frames": 3,
            "output_path": "/workspace/meme.png",
            "caption": "wow",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["output_path"] == "/workspace/meme.png"
    assert (tmp_path / "meme.png").stat().st_size > 0
    assert "Created still image: /workspace/meme.png at 00:00:00.250" in result["output"]
    assert result["frame_timestamps"] == [0.25]
    assert result["sampling"] == "export"
    assert len(result["images"]) == 1
    returned = Image.open(io.BytesIO(base64.b64decode(result["images"][0]["data"])))
    saved = Image.open(tmp_path / "meme.png")
    assert returned.size == saved.size


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_can_export_multiple_stills_in_one_call(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=2:size=320x180:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "exports": [
                {"timestamp": 0.4, "output_path": "/workspace/images/one.png"},
                {"timestamp": 1.4, "output_path": "/workspace/images/two.png"},
            ],
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["output_paths"] == [
        "/workspace/images/one.png", "/workspace/images/two.png"
    ]
    assert (tmp_path / "images/one.png").stat().st_size > 0
    assert (tmp_path / "images/two.png").stat().st_size > 0


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_rejects_still_export_without_timestamp(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=2:size=160x120:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "exports": [{"output_path": "/workspace/frame.png"}],
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 1
    assert "timestamp" in result["error"]
    assert not (tmp_path / "frame.png").exists()


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_keeps_decoded_frames_when_one_sample_is_unreadable(tmp_path: Path, monkeypatch):
    from src.agent_tools import media_tools

    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=2:size=160x120:rate=10", "-y", str(source),
    ], check=True)
    real_run = media_tools._run

    def run_with_unreadable_fourth_frame(command, timeout):
        output = str(command[-1]) if command else ""
        if output.endswith("frame-04.jpg"):
            return subprocess.CompletedProcess(command, 1, "", "File ended prematurely")
        return real_run(command, timeout)

    monkeypatch.setattr(media_tools, "_run", run_with_unreadable_fourth_frame)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4", "frames": 4,
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert len(result["images"]) == 3
    assert len(result["frame_timestamps"]) == 3
    assert "Skipped 1 undecodable requested frame" in result["output"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_batch_export_preserves_argument_normalization_notes(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=2:size=320x180:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "duration": "2",
            "export": json.dumps([
                {"timestamp": 0.4, "output_path": "/workspace/one.png"},
                {"timestamp": 1.4, "output_path": "/workspace/two.png"},
            ]),
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert "Normalized `export` array to `exports`" in result["output"]
    assert "Normalized `duration` to an end position" in result["output"]
    assert "Created still images in one batch" in result["output"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_uses_stable_full_range_single_thread_jpeg_exports(
    tmp_path: Path, monkeypatch
):
    from src.agent_tools import media_tools

    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=1:size=320x180:rate=10",
        "-pix_fmt", "yuv420p", "-y", str(source),
    ], check=True)
    real_run = media_tools._run

    def reject_unstable_jpeg(command, timeout=60):
        is_jpeg_export = (
            "-frames:v" in command
            and command[-1].lower().endswith((".jpg", ".jpeg"))
        )
        if is_jpeg_export and (
            "yuvj420p" not in command or
            not any(
                command[index:index + 2] == ["-threads", "1"]
                for index in range(len(command) - 1)
            )
        ):
            return subprocess.CompletedProcess(
                command, 1, "", "Non full-range YUV is non-standard"
            )
        return real_run(command, timeout)

    monkeypatch.setattr(media_tools, "_run", reject_unstable_jpeg)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "exports": [
                {"timestamp": 0.4, "output_path": "/workspace/one.jpg"},
                {"timestamp": 0.7, "output_path": "/workspace/two.jpeg"},
            ],
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert (tmp_path / "one.jpg").is_file()
    assert (tmp_path / "two.jpeg").is_file()


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_rejects_out_of_range_batch_before_any_export(
    tmp_path: Path,
):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=1:size=320x180:rate=10",
        "-pix_fmt", "yuv420p", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "exports": [
                {"timestamp": 0.4, "output_path": "/workspace/first.jpg"},
                {"timestamp": 360, "output_path": "/workspace/outside.jpg"},
            ],
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 1
    assert "outside the" in result["error"]
    assert not (tmp_path / "first.jpg").exists()
    assert not (tmp_path / "outside.jpg").exists()


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_normalizes_export_path_alias(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=1:size=320x180:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "timestamp": 0.4,
            "export_path": "/workspace/still.png",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["output_path"] == "/workspace/still.png"
    assert "Normalized `export_path` to `output_path`" in result["output"]
    assert (tmp_path / "still.png").stat().st_size > 0


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_rejects_duplicate_batch_export_destinations(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=2:size=320x180:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "exports": [
                {"timestamp": 0.4, "output_path": "/workspace/student1.png"},
                {"timestamp": 1.4, "output_path": "/workspace/student1.png"},
            ],
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 1
    assert "unique output_path" in result["error"]
    assert "duplicate destination" in result["error"]
    assert not (tmp_path / "student1.png").exists()


def test_inspect_media_rejects_unrequested_evidence_caption(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "exports": [{
                "timestamp": 0.4,
                "output_path": "/workspace/student1.png",
                "caption": "student completed pull-up",
            }],
        }), {"client_runtime_context": {"media_caption_allowed": False}}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 1
    assert "only allowed when the user explicitly requests" in result["error"]
    assert "not pixel evidence" in result["error"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_expands_sampled_frame_output_template(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=2:size=320x180:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4", "start": 0, "end": 2,
            "frames": 3,
            "output_path": "/workspace/images/frame_{frame:02d}.png",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["output_paths"] == [
        "/workspace/images/frame_01.png",
        "/workspace/images/frame_02.png",
        "/workspace/images/frame_03.png",
    ]
    assert len(result["images"]) == 3
    assert all((tmp_path / f"images/frame_{index:02d}.png").is_file() for index in range(1, 4))


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_samples_each_requested_segment_without_export(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=4:size=320x180:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "segments": [{"start": 0, "end": 1}, {"start": 2, "end": 4}],
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["frame_timestamps"] == pytest.approx([0.5, 3.0])
    assert len(result["images"]) == 2
    assert "each requested segment" in result["output"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_overview_segments_use_dense_default_budget(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=4:size=320x180:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "sampling": "overview",
            "segments": [{"start": 0, "end": 2}, {"start": 2, "end": 4}],
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["sampling"] == "overview"
    assert result["requested_frames"] == 48
    assert len(result["frame_timestamps"]) == 48
    assert len(result["images"]) == 6
    assert "across the requested segments" in result["output"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_distributes_explicit_frame_budget_across_segments(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=4:size=320x180:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/source.mp4",
            "frames": 6,
            "segments": [{"start": 0, "end": 1}, {"start": 2, "end": 4}],
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["frame_timestamps"] == pytest.approx([
        0.25, 0.75, 2.25, 2.75, 3.25, 3.75,
    ])
    assert len(result["images"]) == 6
    assert "Distributed the requested frame budget" in result["output"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_allows_more_inspection_ranges_than_export_ranges(tmp_path: Path):
    video = tmp_path / "video.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=160x120:rate=4:duration=4", "-pix_fmt", "yuv420p",
        "-y", str(video),
    ], check=True)
    segments = [
        {"start": index * 0.2, "end": (index + 1) * 0.2}
        for index in range(13)
    ]
    token = _active_workspace.set(str(tmp_path))
    try:
        inspected = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4", "segments": segments, "frames": 13,
        }), {}))
        rejected_export = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/video.mp4", "segments": segments,
            "output_path": "/workspace/too_many.mp4",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert inspected["exit_code"] == 0
    assert len(inspected["frame_timestamps"]) == 13
    assert rejected_export["exit_code"] == 1
    assert "at most 12 ranges" in rejected_export["error"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_inspect_media_explains_out_of_range_video_timeline(tmp_path: Path):
    source = tmp_path / "short.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=2:size=160x120:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/short.mp4",
            "segments": [{"start": "00:35:00", "end": "00:36:00"}],
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 1
    assert "outside the" in result["error"]
    assert "absolute video timeline" in result["error"]
    assert "scoreboard or match clock" in result["error"]


def test_inspect_media_renders_local_pdf_page_for_model_vision(tmp_path: Path):
    pytest.importorskip("pypdfium2")
    image_module = pytest.importorskip("PIL.Image")
    pdf = tmp_path / "figure.pdf"
    image_module.new("RGB", (180, 120), "white").save(pdf, "PDF")
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/figure.pdf", "page": 1, "pages": 1,
            "query": "line chart",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["page_numbers"] == [1]
    assert len(result["images"]) == 1
    assert result["images"][0]["mimeType"] == "image/jpeg"


def test_inspect_media_accepts_start_as_pdf_page_alias(tmp_path: Path):
    pytest.importorskip("pypdfium2")
    image_module = pytest.importorskip("PIL.Image")
    pdf = tmp_path / "pages.pdf"
    images = [image_module.new("RGB", (80, 60), color) for color in ("red", "green", "blue")]
    images[0].save(pdf, "PDF", save_all=True, append_images=images[1:])
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/pages.pdf", "start": 2, "pages": 2,
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["page_numbers"] == [2, 3]
    assert len(result["images"]) == 2


def test_inspect_media_accepts_exact_pdf_page_list(tmp_path: Path):
    pytest.importorskip("pypdfium2")
    image_module = pytest.importorskip("PIL.Image")
    pdf = tmp_path / "pages.pdf"
    images = [
        image_module.new("RGB", (80, 60), color)
        for color in ("red", "green", "blue", "yellow", "purple")
    ]
    images[0].save(pdf, "PDF", save_all=True, append_images=images[1:])
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/pages.pdf", "pages": [5, 2, 4],
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["page_numbers"] == [5, 2, 4]


def test_inspect_media_normalizes_json_encoded_pdf_page_list(tmp_path: Path):
    pytest.importorskip("pypdfium2")
    from PIL import Image as image_module

    pdf = tmp_path / "pages.pdf"
    images = [image_module.new("RGB", (80, 60), "white") for _ in range(5)]
    images[0].save(pdf, "PDF", save_all=True, append_images=images[1:])
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/pages.pdf", "pages": "[5, 2, 4]",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["page_numbers"] == [5, 2, 4]
    assert "Normalized JSON-encoded `pages`" in result["output"]
    assert len(result["images"]) == 3


def test_inspect_media_accepts_twelve_exact_pdf_pages(tmp_path: Path):
    pytest.importorskip("pypdfium2")
    image_module = pytest.importorskip("PIL.Image")
    pdf = tmp_path / "pages.pdf"
    images = [
        image_module.new("RGB", (40, 30), (index * 17 % 255, 40, 80))
        for index in range(12)
    ]
    images[0].save(pdf, "PDF", save_all=True, append_images=images[1:])
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/pages.pdf", "pages": list(range(1, 13)),
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["page_numbers"] == list(range(1, 13))
    assert len(result["images"]) == 12


def test_inspect_media_rejects_out_of_range_pdf_page_list(tmp_path: Path):
    pytest.importorskip("pypdfium2")
    image_module = pytest.importorskip("PIL.Image")
    pdf = tmp_path / "pages.pdf"
    image_module.new("RGB", (80, 60), "white").save(pdf, "PDF")
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/pages.pdf", "pages": [1, 2],
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 1
    assert "between 1 and 1" in result["error"]


def test_transcribe_media_returns_timestamped_local_segments(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    from src.agent_tools import media_tools

    source = tmp_path / "dialogue.mp4"
    source.write_bytes(b"fixture")

    class FakeWhisper:
        def transcribe(self, path, **kwargs):
            assert path == str(source)
            assert kwargs["language"] == "zh"
            return iter([
                SimpleNamespace(start=1.25, end=2.5, text=" 夏洛 "),
                SimpleNamespace(start=3.0, end=4.0, text=" 马冬梅 "),
            ]), SimpleNamespace(language="zh", language_probability=0.99)

    monkeypatch.setitem(media_tools._WHISPER_MODELS, "tiny", FakeWhisper())
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(TranscribeMediaTool().execute(json.dumps({
            "path": "/workspace/dialogue.mp4", "language": "zh", "force_language": True,
            "model": "tiny",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["segments"] == 2
    assert "00:00:01.250" in result["transcript"]
    assert "夏洛" in result["transcript"]
    assert result["output_path"].startswith("/workspace/.odysseus/transcripts/dialogue-")
    assert "text begins after the first '] '" in result["output"]
    assert "identify its start and the next section boundary" in result["output"]
    persisted = tmp_path / result["output_path"].removeprefix("/workspace/")
    assert persisted.read_text(encoding="utf-8") == result["transcript"] + "\n"


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_transcribe_media_returns_empty_artifact_for_silent_video(
    tmp_path: Path, monkeypatch
):
    from src.agent_tools import media_tools

    source = tmp_path / "silent.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=1:size=320x180:rate=10",
        "-pix_fmt", "yuv420p", "-y", str(source),
    ], check=True)

    class WhisperMustNotRun:
        def transcribe(self, path, **kwargs):
            raise AssertionError("silent video must be handled before Whisper")

    monkeypatch.setitem(
        media_tools._WHISPER_MODELS, "tiny", WhisperMustNotRun()
    )
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(TranscribeMediaTool().execute(json.dumps({
            "path": "/workspace/silent.mp4",
            "model": "tiny",
            "output_path": "/workspace/transcript.txt",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["segments"] == 0
    assert result["transcript"] == ""
    assert "No audio stream detected" in result["output"]
    assert (tmp_path / "transcript.txt").read_text(encoding="utf-8") == ""


def test_transcribe_media_can_persist_timestamped_transcript(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    from src.agent_tools import media_tools

    source = tmp_path / "dialogue.mp4"
    source.write_bytes(b"fixture")

    class FakeWhisper:
        def transcribe(self, path, **kwargs):
            assert path == str(source)
            return iter([
                SimpleNamespace(start=1.0, end=2.0, text=" first line "),
                SimpleNamespace(start=3.5, end=4.25, text=" second line "),
            ]), SimpleNamespace(language="en", language_probability=0.98)

    monkeypatch.setitem(media_tools._WHISPER_MODELS, "tiny", FakeWhisper())
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(TranscribeMediaTool().execute(json.dumps({
            "path": "/workspace/dialogue.mp4",
            "model": "tiny",
            "output_path": "/workspace/intermediate/transcript.txt",
        }), {}))
    finally:
        _active_workspace.reset(token)

    output = tmp_path / "intermediate" / "transcript.txt"
    assert result["exit_code"] == 0
    assert result["output_path"] == "/workspace/intermediate/transcript.txt"
    assert output.read_text(encoding="utf-8") == result["transcript"] + "\n"
    assert result["output"].startswith(
        "Detected language: en (confidence 0.98)\n"
        "Saved timestamped transcript: /workspace/intermediate/transcript.txt\n"
    )


def test_transcribe_media_can_write_structured_jsonl_directly(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    from src.agent_tools import media_tools

    source = tmp_path / "dialogue.mp4"
    source.write_bytes(b"fixture")

    class FakeWhisper:
        def transcribe(self, path, **kwargs):
            return iter([
                SimpleNamespace(start=0.04, end=1.16, text=" 嗨，朋友们 "),
                SimpleNamespace(start=1.16, end=2.12, text=" 第二行 "),
            ]), SimpleNamespace(language="zh", language_probability=1.0)

    monkeypatch.setitem(media_tools._WHISPER_MODELS, "tiny", FakeWhisper())
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(TranscribeMediaTool().execute(json.dumps({
            "path": "/workspace/dialogue.mp4",
            "model": "tiny",
            "output_path": "/workspace/subtitles.jsonl",
            "timestamp_precision": 1,
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["format"] == "jsonl"
    rows = [json.loads(line) for line in (tmp_path / "subtitles.jsonl").read_text().splitlines()]
    assert rows == [
        {"start": 0.0, "end": 1.2, "text": "嗨，朋友们"},
        {"start": 1.2, "end": 2.1, "text": "第二行"},
    ]
    assert "Artifact format: JSONL" in result["output"]
    assert '"start": 0.0' in result["output"]
    assert "already contains the requested structured data" in result["output"]
    assert "[00:00:00.040 --> 00:00:01.160]" not in result["output"]


def test_transcribe_media_schema_exposes_direct_subtitle_artifacts():
    schema = next(
        item["function"]
        for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "transcribe_media"
    )
    properties = schema["parameters"]["properties"]
    assert "jsonl" in properties["output_path"]["description"].lower()
    assert properties["timestamp_precision"]["minimum"] == 0
    assert properties["timestamp_precision"]["maximum"] == 3


def test_transcribe_media_bounds_inline_output_but_persists_full_text(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    from src.agent_tools import media_tools

    source = tmp_path / "long.mp4"
    source.write_bytes(b"fixture")
    long_text = "evidence " * 5000

    class FakeWhisper:
        def transcribe(self, path, **kwargs):
            return iter([
                SimpleNamespace(start=1.0, end=100.0, text=long_text),
            ]), SimpleNamespace(language="en", language_probability=0.97)

    monkeypatch.setitem(media_tools._WHISPER_MODELS, "tiny", FakeWhisper())
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(TranscribeMediaTool().execute(json.dumps({
            "path": "/workspace/long.mp4", "model": "tiny",
        }), {}))
    finally:
        _active_workspace.reset(token)

    persisted = tmp_path / result["output_path"].removeprefix("/workspace/")
    assert persisted.read_text(encoding="utf-8") == result["transcript"] + "\n"
    assert result["output"].splitlines()[1].startswith("Saved timestamped transcript:")
    assert "Inline artifact preview truncated" in result["output"]
    assert "search the saved file with a narrow term" in result["output"]
    assert len(result["output"]) < 6000


def test_transcribe_media_rejects_unconfined_output_path(tmp_path: Path):
    source = tmp_path / "dialogue.mp4"
    source.write_bytes(b"fixture")
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(TranscribeMediaTool().execute(json.dumps({
            "path": "/workspace/dialogue.mp4",
            "output_path": "/tmp/transcript.txt",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result == {
        "error": "transcribe_media output_path must stay inside the active workspace",
        "exit_code": 1,
    }


def test_transcribe_media_path_error_names_transcribe_media(tmp_path: Path):
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(TranscribeMediaTool().execute(
            json.dumps({"path": "/tmp/outside.mp4"}), {}
        ))
    finally:
        _active_workspace.reset(token)

    assert result == {
        "error": "transcribe_media path must stay inside the active workspace",
        "exit_code": 1,
    }


def test_transcribe_media_schema_advertises_persisted_transcript_path():
    schema = next(
        item["function"]
        for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "transcribe_media"
    )
    description = schema["parameters"]["properties"]["output_path"]["description"]
    assert "/workspace" in description
    assert "timestamped transcript" in description
    assert "automatically" in description


def test_transcribe_media_prefers_quality_for_focused_ranges(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    from src.agent_tools import media_tools

    source = tmp_path / "dialogue.mp4"
    source.write_bytes(b"fixture")
    used = []

    class FakeWhisper:
        def __init__(self, name):
            self.name = name

        def transcribe(self, path, **kwargs):
            used.append(self.name)
            return iter([]), SimpleNamespace(language="zh", language_probability=1.0)

    monkeypatch.delenv("ODYSSEUS_STT_MODEL", raising=False)
    monkeypatch.setitem(media_tools._WHISPER_MODELS, "small", FakeWhisper("small"))
    monkeypatch.setitem(media_tools._WHISPER_MODELS, "base", FakeWhisper("base"))
    token = _active_workspace.set(str(tmp_path))
    try:
        focused = asyncio.run(TranscribeMediaTool().execute(json.dumps({
            "path": "/workspace/dialogue.mp4", "start": 0, "end": 30,
        }), {}))
        unbounded = asyncio.run(TranscribeMediaTool().execute(json.dumps({
            "path": "/workspace/dialogue.mp4",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert focused["exit_code"] == 0
    assert focused["model"] == "small"
    assert unbounded["exit_code"] == 0
    assert unbounded["model"] == "base"
    assert used == ["small", "base"]


def test_transcribe_media_normalizes_language_names(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace
    from src.agent_tools import media_tools

    source = tmp_path / "dialogue.mp4"
    source.write_bytes(b"fixture")

    class FakeWhisper:
        def transcribe(self, path, **kwargs):
            assert kwargs["language"] is None
            return iter([]), SimpleNamespace(language="zh", language_probability=1.0)

    monkeypatch.setitem(media_tools._WHISPER_MODELS, "tiny", FakeWhisper())
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(TranscribeMediaTool().execute(json.dumps({
            "path": "/workspace/dialogue.mp4", "language": "English", "model": "tiny",
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["language"] == "zh"


def test_new_visual_result_retires_prior_tool_images_only():
    from src.agent_loop import _append_tool_results

    messages = [
        {
            "role": "user",
            "metadata": {"trusted": False, "source": "tool visual evidence"},
            "content": [
                {"type": "text", "text": "old frames"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,old"}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,user"}}],
        },
    ]
    _append_tool_results(
        messages,
        "",
        [{"id": "call-1", "name": "inspect_media", "arguments": "{}"}],
        [{}],
        ["new frames"],
        True,
        2,
        tool_result_records=[{
            "tool_name": "inspect_media",
            "result": {"images": [{"mimeType": "image/jpeg", "data": "new"}]},
        }],
    )

    assert isinstance(messages[0]["content"], str)
    assert "retired after inspection" in messages[0]["content"]
    assert isinstance(messages[1]["content"], list)
    assert any(
        isinstance(message.get("content"), list)
        and any(block.get("image_url", {}).get("url", "").endswith("new") for block in message["content"] if isinstance(block, dict))
        for message in messages
    )


def test_text_only_tool_router_keeps_browser_result_text_without_inline_image():
    """A text-only vLLM route must not receive private-browser screenshots."""
    from src.agent_loop import _append_tool_results

    messages = []
    _append_tool_results(
        messages,
        "",
        [{"id": "call-1", "name": "private_browser", "arguments": "{}"}],
        [{}],
        ["Page opened. Title: IKEA"],
        True,
        1,
        tool_result_records=[{
            "tool_name": "private_browser",
            "result": {
                "output": "Page opened. Title: IKEA",
                "images": [{"mimeType": "image/png", "data": "pixels"}],
            },
        }],
        allow_visual_evidence=False,
    )

    assert any(message.get("role") == "tool" for message in messages)
    assert not any(
        isinstance(message.get("content"), list)
        and any(
            isinstance(block, dict) and block.get("type") == "image_url"
            for block in message["content"]
        )
        for message in messages
    )


def test_new_browser_snapshot_retires_prior_dom_and_same_batch_states():
    from src.agent_loop import _append_tool_results

    messages = [{
        "role": "tool",
        "tool_call_id": "old",
        "content": "old DOM " * 1000,
        "metadata": {"trusted": False, "source": "tool result: private_browser"},
    }]
    calls = [
        {"id": "open", "name": "private_browser", "arguments": '{"action":"batch","commands":[["open","https://example.com"],["snapshot"]]}'},
        {"id": "snap", "name": "private_browser", "arguments": '{"action":"snapshot"}'},
    ]
    records = [
        {"tool_name": "private_browser", "content": calls[0]["arguments"], "result": {"output": "first state"}},
        {"tool_name": "private_browser", "content": calls[1]["arguments"], "result": {"output": "newest DOM with refs"}},
    ]

    _append_tool_results(
        messages, "", calls, [{}, {}], ["first state", "newest DOM with refs"],
        True, 2, tool_result_records=records, allow_visual_evidence=False,
    )

    assert messages[0]["content"].startswith("[Prior private-browser DOM state retired")
    assert "superseded by the newest page snapshot" in messages[2]["content"]
    assert messages[3]["content"] == "newest DOM with refs"


def test_visual_evidence_window_is_bounded_with_explicit_override(monkeypatch):
    from src.agent_loop import _append_tool_results

    messages = []
    records = [{
        "tool_name": "inspect_media",
        "result": {"images": [{"mimeType": "image/jpeg", "data": str(i)}]},
    } for i in range(6)]
    _append_tool_results(
        messages, "", [{"id": "call-1", "name": "inspect_media", "arguments": "{}"}], [{}], ["frames"], True, 1,
        tool_result_records=records,
    )
    visual = next(message for message in messages if message.get("metadata", {}).get("source") == "tool visual evidence")
    assert len(visual["content"]) - 1 == 1

    messages.clear()
    monkeypatch.setenv("ODYSSEUS_MAX_VISUAL_EVIDENCE_IMAGES", "6")
    _append_tool_results(
        messages, "", [{"id": "call-1", "name": "inspect_media", "arguments": "{}"}], [{}], ["frames"], True, 1,
        tool_result_records=records,
    )
    visual = next(message for message in messages if message.get("metadata", {}).get("source") == "tool visual evidence")
    assert len(visual["content"]) - 1 == 3

    messages.clear()
    monkeypatch.setenv("ODYSSEUS_MAX_VISUAL_EVIDENCE_FRAMES", "6")
    _append_tool_results(
        messages, "", [{"id": "call-1", "name": "inspect_media", "arguments": "{}"}], [{}], ["frames"], True, 1,
        tool_result_records=records,
    )
    visual = next(message for message in messages if message.get("metadata", {}).get("source") == "tool visual evidence")
    assert [block["image_url"]["url"] for block in visual["content"][1:]] == [
        f"data:image/jpeg;base64,{index}" for index in range(6)
    ]


def test_visual_frames_are_uniformly_bounded_within_one_media_result(monkeypatch):
    from src.agent_loop import _append_tool_results

    monkeypatch.setenv("ODYSSEUS_MAX_VISUAL_EVIDENCE_FRAMES", "3")
    messages = []
    records = [{
        "tool_name": "inspect_media",
        "result": {
            "images": [
                {"mimeType": "image/jpeg", "data": str(index)}
                for index in range(5)
            ],
        },
    }]
    _append_tool_results(
        messages, "", [{"id": "call-1", "name": "inspect_media", "arguments": "{}"}], [{}], ["frames"], True, 1,
        tool_result_records=records,
    )

    visual = next(message for message in messages if message.get("metadata", {}).get("source") == "tool visual evidence")
    assert [block["image_url"]["url"] for block in visual["content"][1:]] == [
        "data:image/jpeg;base64,0",
        "data:image/jpeg;base64,2",
        "data:image/jpeg;base64,4",
    ]
