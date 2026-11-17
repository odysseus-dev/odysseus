#!/usr/bin/env python3
"""Produce WYSIWYG Document Editor Feature Demonstration Video for Maintainer Review."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ARTIFACTS_DIR = Path("/home/alteixeira20/Projects/Odysseus/audit-artifacts/document-wysiwyg-release-validation")
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
FRAMES_DIR = ARTIFACTS_DIR / "frames"
FRAMES_DIR.mkdir(parents=True, exist_ok=True)
FINAL_MP4 = ARTIFACTS_DIR / "wysiwyg-feature-demo.mp4"


def find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get_git_revision() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        return out
    except Exception:
        return "unknown"


def main() -> int:
    rev = get_git_revision()
    print(f"Producing WYSIWYG Demo Video on revision {rev}")

    with tempfile.TemporaryDirectory(prefix="ody-demo-") as tmpdir:
        tmp_path = Path(tmpdir)
        db_file = tmp_path / "demo.db"
        data_dir = tmp_path / "data"
        fastembed_dir = tmp_path / "fastembed"
        rec_dir = tmp_path / "recordings"
        data_dir.mkdir(parents=True, exist_ok=True)
        fastembed_dir.mkdir(parents=True, exist_ok=True)
        rec_dir.mkdir(parents=True, exist_ok=True)

        port = find_free_port()
        env = dict(os.environ)
        env.update(
            DATABASE_URL=f"sqlite:///{db_file}",
            ODYSSEUS_DATA_DIR=str(data_dir),
            AUTH_ENABLED="false",
            ODYSSEUS_STARTUP_WARMUPS="0",
            ODYSSEUS_TOOL_INDEX_PREWARM="0",
            FASTEMBED_CACHE_PATH=str(fastembed_dir),
        )
        env.pop("PYTHONPATH", None)

        print(f"Launching isolated Odysseus backend on 127.0.0.1:{port}...")
        uvicorn_log_path = Path("/tmp/odysseus-demo-uvicorn.log")
        uvicorn_log = open(uvicorn_log_path, "w", encoding="utf-8")
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=uvicorn_log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=(os.name != "nt"),
        )

        from src.process_lifecycle import (
            ProcessIdentity,
            terminate_identities,
            bind_descendants,
            term_kill_steps,
            OWNED,
        )

        server_identity = ProcessIdentity.capture(server.pid)
        origin = f"http://127.0.0.1:{port}"

        try:
            print("Waiting for server ready...")
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    uvicorn_log.flush()
                    logs = uvicorn_log_path.read_text(encoding="utf-8")
                    raise RuntimeError(f"Server exited prematurely:\n{logs}")
                try:
                    with urlopen(f"{origin}/", timeout=1) as res:
                        if res.status == 200:
                            print("Server ready!")
                            break
                except Exception:
                    time.sleep(0.3)
            else:
                raise RuntimeError("Server did not become ready within 45s")

            # Run Playwright demo recorder
            test_env = dict(os.environ)
            test_env.update(
                ODYSSEUS_DEMO_ORIGIN=origin,
                ODYSSEUS_DEMO_VIDEO_DIR=str(rec_dir),
            )
            print("Recording video scenarios with Playwright Chromium...")
            node_proc = subprocess.Popen(
                ["node", "scripts/record_wysiwyg_demo.mjs"],
                cwd=ROOT,
                env=test_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                for line in iter(node_proc.stdout.readline, ""):
                    print(line, end="", flush=True)
                node_proc.wait(timeout=300)
            except subprocess.TimeoutExpired:
                node_proc.kill()
                print("\n[ERROR] Playwright demo recording timed out!", flush=True)
                raise
            finally:
                node_proc.stdout.close()

            if node_proc.returncode != 0:
                print(f"[ERROR] Node recorder failed with code {node_proc.returncode}", file=sys.stderr)
                return node_proc.returncode

            # Find recorded video file (.webm)
            webm_files = list(rec_dir.glob("*.webm"))
            if not webm_files:
                raise RuntimeError("No .webm file found in recordings directory")
            source_webm = webm_files[0]
            print(f"Recorded raw WebM: {source_webm} ({source_webm.stat().st_size} bytes)")

            # Encode to MP4 with FFmpeg
            print(f"Encoding to review-grade MP4 at {FINAL_MP4}...")
            ffmpeg_cmd = [
                "/usr/bin/ffmpeg",
                "-y",
                "-i",
                str(source_webm),
                "-c:v",
                "libx264",
                "-preset",
                "slow",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(FINAL_MP4),
            ]
            ff_res = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
            if ff_res.returncode != 0:
                print(ff_res.stderr, file=sys.stderr)
                raise RuntimeError(f"FFmpeg encoding failed with returncode {ff_res.returncode}")

            print(f"Successfully generated {FINAL_MP4} ({FINAL_MP4.stat().st_size} bytes)")

            # Probe technical specs via ffprobe
            ffprobe_cmd = [
                "/usr/bin/ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration,size,bit_rate:stream=width,height,codec_name,r_frame_rate",
                "-of",
                "json",
                str(FINAL_MP4),
            ]
            probe_res = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=True)
            probe_data = json.loads(probe_res.stdout)
            print("FFprobe Verification Data:")
            print(json.dumps(probe_data, indent=2))

            duration_sec = float(probe_data["format"]["duration"])
            width = probe_data["streams"][0]["width"]
            height = probe_data["streams"][0]["height"]
            codec = probe_data["streams"][0]["codec_name"]
            size_mb = FINAL_MP4.stat().st_size / (1024 * 1024)

            print(f"\nFinal Video Metrics:")
            print(f"- Duration: {duration_sec:.1f} seconds ({duration_sec/60:.2f} minutes)")
            print(f"- Dimensions: {width}x{height}")
            print(f"- Codec: {codec}")
            print(f"- File Size: {size_mb:.2f} MB")

            # Extract frame screenshots at key scene milestones
            print("\nExtracting scene frame snapshots for demo-evidence.md...")
            milestones = [
                ("scene1-architecture.png", 8),
                ("scene2-typography.png", 28),
                ("scene3-structures.png", 50),
                ("scene4-source-mode.png", 72),
                ("scene5-source-fidelity.png", 96),
                ("scene6-safeguard.png", 120),
                ("scene7-readiness.png", min(int(duration_sec) - 3, 140)),
            ]
            for fname, sec in milestones:
                out_png = FRAMES_DIR / fname
                subprocess.run(
                    [
                        "/usr/bin/ffmpeg",
                        "-y",
                        "-ss",
                        str(sec),
                        "-i",
                        str(FINAL_MP4),
                        "-vframes",
                        "1",
                        "-q:v",
                        "2",
                        str(out_png),
                    ],
                    capture_output=True,
                    check=True,
                )
                print(f"Extracted {fname} at {sec}s")

            print("\nVideo demonstration production complete!")
            return 0
        finally:
            print("Stopping test server...")
            try:
                uvicorn_log.close()
            except Exception:
                pass
            if server_identity.verdict() == OWNED:
                descendants = bind_descendants([server_identity.pid])
                terminate_identities(
                    [*(e.identity for e in reversed(descendants)), server_identity], steps=term_kill_steps(5)
                )
            else:
                server.terminate()
                try:
                    server.wait(timeout=3)
                except Exception:
                    server.kill()
            print("Server stopped.")


if __name__ == "__main__":
    sys.exit(main())
