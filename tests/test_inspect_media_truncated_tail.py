"""Regression coverage for partially downloaded/truncated video containers."""

import asyncio
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from src.agent_tools.media_tools import InspectMediaTool
from src.tool_execution import _active_workspace


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg required",
)
def test_inspect_media_recovers_to_a_decodable_frame_before_a_truncated_tail(
    tmp_path: Path,
):
    complete = tmp_path / "complete.webm"
    truncated = tmp_path / "truncated.webm"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", "testsrc2=size=320x180:rate=4:duration=8",
            "-c:v", "libvpx-vp9", "-g", "4", "-pix_fmt", "yuv420p",
            "-y", str(complete),
        ],
        check=True,
    )
    payload = complete.read_bytes()
    truncated.write_bytes(payload[: int(len(payload) * 0.70)])

    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/truncated.webm",
            "timestamp": 7.9,
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0, result
    assert result["frame_timestamps"][0] < 7.0
    assert "Recovered a decodable frame before the requested timestamp" in result["output"]
