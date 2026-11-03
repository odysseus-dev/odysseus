import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.agent_tools.media_tools import InspectMediaTool, _parse_video_position
from src.tool_execution import _active_workspace


def test_video_percentage_parser_uses_duration_and_rejects_out_of_bounds():
    assert _parse_video_position("25%", default=0, duration=40) == 10
    assert _parse_video_position("100%", default=0, duration=40) == 40

    with pytest.raises(ValueError, match="between 0% and 100%"):
        _parse_video_position("101%", default=0, duration=40)


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg required",
)
def test_inspect_media_accepts_percentage_video_range(tmp_path: Path):
    source = tmp_path / "short.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=duration=4:size=160x120:rate=10", "-y", str(source),
    ], check=True)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(InspectMediaTool().execute(json.dumps({
            "path": "/workspace/short.mp4",
            "start": "25%",
            "end": "75%",
            "frames": 2,
        }), {}))
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert result["frame_timestamps"] == pytest.approx([1.5, 2.5], abs=0.15)
    assert len(result["images"]) == 2
