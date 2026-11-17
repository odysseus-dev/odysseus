import asyncio
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from src.agent_tools import media_tools
from src.agent_tools.media_tools import InspectMediaTool
from src.tool_execution import _active_workspace


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg required",
)
def test_inspect_media_retries_one_transient_duration_probe_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    video = tmp_path / "video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=2:duration=1",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(video),
        ],
        check=True,
    )

    original_run = media_tools._run
    duration_probe_calls = 0

    def flaky_run(command: list[str], timeout: int = 60):
        nonlocal duration_probe_calls
        if "format=duration" in command:
            duration_probe_calls += 1
            if duration_probe_calls == 1:
                raise subprocess.TimeoutExpired(command, timeout)
        return original_run(command, timeout)

    monkeypatch.setattr(media_tools, "_run", flaky_run)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(
            InspectMediaTool().execute(
                json.dumps({"path": "/workspace/video.mp4", "frames": 1}), {}
            )
        )
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert duration_probe_calls == 2
    assert len(result["images"]) == 1
