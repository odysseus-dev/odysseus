"""Update routes — admin-only in-app update via git pull."""

import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

# Resolved once at import time. Running inside the container the CWD is
# the cloned repo, so "." is the correct default. Override via
# ODYSSEUS_REPO_PATH when the working directory differs from the repo root.
_REPO_PATH = Path(os.getenv("ODYSSEUS_REPO_PATH", ".")).resolve()

# Written by the pull endpoint so the host-side watcher (scripts/update-watch.sh)
# can trigger a docker compose rebuild automatically.
_TRIGGER_FILE = Path("data") / "update_ready"

_GIT_TIMEOUT = 60  # seconds


def _require_admin(request: Request):
    auth_manager = getattr(request.app.state, "auth_manager", None)
    if not auth_manager:
        return
    user = getattr(request.state, "current_user", None)
    if user == "internal-tool":
        return
    if not user or user == "api":
        raise HTTPException(403, "Admin only")
    if not auth_manager.is_admin(user):
        raise HTTPException(403, "Admin only")


async def _git(*args, cwd: Path = _REPO_PATH) -> tuple[str, str, int]:
    """Run a git command and return (stdout, stderr, returncode)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=_GIT_TIMEOUT)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        return "", f"git timed out after {_GIT_TIMEOUT}s", -1
    return out.decode(errors="replace").strip(), err.decode(errors="replace").strip(), proc.returncode


async def _generate_pull(request: Request):
    """Run git pull --ff-only origin main and stream output as SSE."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "pull", "--ff-only", "origin", "main",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_REPO_PATH),
        )

        q: asyncio.Queue = asyncio.Queue()

        async def _reader(stream, name):
            try:
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    await q.put((name, line.decode(errors="replace").rstrip("\r\n")))
            finally:
                await q.put((name, None))

        reader_tasks = [
            asyncio.create_task(_reader(proc.stdout, "stdout")),
            asyncio.create_task(_reader(proc.stderr, "stderr")),
        ]

        finished = 0
        deadline = asyncio.get_event_loop().time() + _GIT_TIMEOUT
        while finished < 2:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                if proc:
                    proc.kill()
                yield f"data: {json.dumps({'stream': 'stderr', 'data': 'git pull timed out'})}\n\n"
                yield f"data: {json.dumps({'exit_code': -1})}\n\n"
                return
            try:
                name, text = await asyncio.wait_for(q.get(), timeout=min(remaining, 2.0))
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    if proc:
                        proc.kill()
                    return
                continue
            if text is None:
                finished += 1
                continue
            yield f"data: {json.dumps({'stream': name, 'data': text})}\n\n"

        await proc.wait()
        exit_code = proc.returncode

        if exit_code == 0:
            try:
                _TRIGGER_FILE.parent.mkdir(parents=True, exist_ok=True)
                _TRIGGER_FILE.write_text("1")
            except Exception as e:
                logger.warning("Failed to write update trigger file: %s", e)

        yield f"data: {json.dumps({'exit_code': exit_code})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'stream': 'stderr', 'data': str(e)})}\n\n"
        yield f"data: {json.dumps({'exit_code': -1})}\n\n"
    finally:
        for t in reader_tasks if 'reader_tasks' in dir() else []:
            t.cancel()


def setup_update_routes() -> APIRouter:
    router = APIRouter(tags=["update"])

    @router.get("/api/admin/update/status")
    async def update_status(request: Request):
        """Return current commit and how many commits behind origin/main we are. Admin only."""
        _require_admin(request)

        # Fetch so we see current upstream state
        _, fetch_err, fetch_rc = await _git("fetch", "origin")
        if fetch_rc != 0:
            raise HTTPException(502, f"git fetch failed: {fetch_err}")

        current, _, _ = await _git("rev-parse", "HEAD")
        current_short, _, _ = await _git("rev-parse", "--short", "HEAD")
        current_msg, _, _ = await _git("log", "-1", "--pretty=format:%s", "HEAD")

        behind_str, _, _ = await _git("rev-list", "--count", "HEAD..origin/main")
        try:
            commits_behind = int(behind_str)
        except ValueError:
            commits_behind = 0

        upstream_short, _, _ = await _git("rev-parse", "--short", "origin/main")
        upstream_msg, _, _ = await _git("log", "-1", "--pretty=format:%s", "origin/main")

        return {
            "current_commit": current,
            "current_short": current_short,
            "current_message": current_msg,
            "upstream_short": upstream_short,
            "upstream_message": upstream_msg,
            "commits_behind": commits_behind,
            "repo_path": str(_REPO_PATH),
        }

    @router.post("/api/admin/update/pull")
    async def update_pull(request: Request):
        """Run git pull --ff-only origin main and stream output via SSE. Admin only."""
        _require_admin(request)
        logger.info("Admin update pull requested")
        return StreamingResponse(_generate_pull(request), media_type="text/event-stream")

    @router.get("/api/admin/update/stream")
    async def update_stream(request: Request):
        """SSE stream of git pull progress (EventSource-compatible GET). Admin only."""
        _require_admin(request)
        logger.info("Admin update stream requested")
        return StreamingResponse(_generate_pull(request), media_type="text/event-stream")

    return router
