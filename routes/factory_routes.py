"""
factory_routes.py — Project Factory API routes (SQLAlchemy rewrite)

All routes are stateless — they instantiate FactoryService which
manages its own sessions. No raw sqlite3, no shared connections.
"""

import json as _json
import logging
import time
import secrets
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Request

from services.factory_service import FactoryService
from services.factory_continuation import strip_code_fences
from services.factory_orchestrator import plan_project, iterate_project, launch_iteration, launch_planning, launch, relaunch, stop as stop_orchestrator, compile_delivery

logger = logging.getLogger(__name__)

svc = FactoryService()  # stateless — no shared conn


def _extract_output(result):
    """Extract the deliverable string from a FactoryNode.result value.

    Mirrors the client-side _getOutput() in static/js/factory.js:
      - dict/object  -> return .output (stringified if object, '' if missing)
      - JSON string  -> parse, then return .output
      - other string -> return as-is
      - None/missing -> return ''
    """
    if not result:
        return ''
    if isinstance(result, dict):
        val = result.get('output')
    elif isinstance(result, str):
        try:
            parsed = _json.loads(result)
            if isinstance(parsed, dict):
                val = parsed.get('output')
            else:
                return strip_code_fences(result)  # valid JSON but not an object — use raw string
        except Exception:
            return strip_code_fences(result)  # not JSON — use raw string
    else:
        return ''
    if val is None:
        return ''
    if isinstance(val, (dict, list)):
        return _json.dumps(val)
    return strip_code_fences(str(val))


import asyncio as _asyncio

# Running dev servers: {project_id: (proc, port)}
_running_servers: dict = {}

# Ephemeral token→files cache for project-delivery previews. Stores ALL
# project files (JS, CSS, images, HTML) keyed by filename, so the catch-all
# route can serve individual files with correct MIME types — the browser
# resolves relative URLs naturally instead of fragile client-side inlining.
_preview_cache: OrderedDict = OrderedDict()  # token -> (files_dict, main_file, timestamp)
_PREVIEW_TTL = 300      # 5 minutes
_PREVIEW_MAX = 100      # max cached entries (FIFO eviction)


def _store_preview(files: dict, main_file: str) -> str:
    """Store assembled preview files, return a token."""
    now = time.time()
    # Purge expired
    expired = [k for k, (_, _, ts) in _preview_cache.items() if now - ts > _PREVIEW_TTL]
    for k in expired:
        _preview_cache.pop(k, None)
    # Evict oldest if at capacity
    while len(_preview_cache) >= _PREVIEW_MAX:
        _preview_cache.popitem(last=False)
    token = secrets.token_hex(16)
    _preview_cache[token] = (files, main_file, now)
    return token


def _get_preview(token: str):
    """Return (files_dict, main_file) for token, or None if missing/expired."""
    entry = _preview_cache.get(token)
    if not entry:
        return None
    files, main_file, ts = entry
    if time.time() - ts > _PREVIEW_TTL:
        _preview_cache.pop(token, None)
        return None
    return files, main_file


_FACTORY_PREVIEW_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
    "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
    "img-src 'self' data: blob: https:; "
    "media-src 'self' blob: https:; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "base-uri 'self'; "
    "frame-ancestors 'self'"
)

async def factory_preview_middleware(request: Request, call_next):
    """Override CSP/X-Frame-Options for factory preview endpoints.

    Registered as an OUTER middleware in app.py (after SecurityHeadersMiddleware)
    so it runs last in the response phase and can overwrite the core middleware's
    strict defaults for factory-specific paths. This keeps core/middleware.py
    pristine — no factory code in core files.
    """
    response = await call_next(request)
    path = request.url.path
    is_preview = (
        (path.startswith("/api/factory/nodes/") and path.endswith("/preview"))
        or path.startswith("/api/factory/preview/")
        or (path.startswith("/api/factory/projects/") and "/proxy/" in path)
    )
    if is_preview:
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = _FACTORY_PREVIEW_CSP
    return response


def setup_factory_routes() -> APIRouter:
    router = APIRouter(prefix="/api/factory", tags=["factory"])

    # ── Projects ───────────────────────────────────────────────

    @router.post("/projects")
    async def create_project(request: Request):
        """Create a new Factory project and launch planning as a background task.

        Returns immediately with the project in 'planning' status. The planner
        LLM call runs as a non-blocking asyncio task (launch_planning). The
        frontend polls for status and picks up tasks when planning completes
        (typically 10-30s later).
        """
        body = await request.json()
        description = body.get("description")
        if not description:
            raise HTTPException(400, "description is required")
        owner = body.get("owner", "default")
        try:
            project = svc.create_project(description=description, owner=owner)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("create_project failed")
            raise HTTPException(500, str(e))

        # Launch planning as a background task — returns immediately.
        # The planner LLM call can take 10-30s; blocking on it risks 504
        # from reverse proxies. The frontend's polling picks up tasks
        # when planning completes.
        pid = project["id"]
        launch_planning(pid, owner=owner)

        # Return the project immediately (status='planning', no tasks yet)
        return svc.get_project(pid) or project

    @router.get("/projects")
    async def list_projects(owner: str = "default"):
        """List all factory projects."""
        try:
            return svc.get_projects(owner=owner)
        except Exception as e:
            logger.exception("list_projects failed")
            raise HTTPException(500, str(e))

    # ── Agent model settings ────────────────────────────────────

    @router.get("/settings")
    async def get_factory_settings():
        """Return agent model assignments + prompts + available endpoints."""
        from src.settings import get_setting
        from core.database import SessionLocal, ModelEndpoint
        import json as _json

        agent_models = get_setting("factory_agent_models", {}) or {}
        custom_prompts = get_setting("factory_agent_prompts", {}) or {}
        agent_max_tokens = get_setting("factory_agent_max_tokens", {}) or {}

        # Build available endpoints list
        db = SessionLocal()
        try:
            endpoints = []
            for ep in db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).order_by(ModelEndpoint.name).all():
                models = []
                raw = getattr(ep, "cached_models", None)
                if raw:
                    try:
                        models = _json.loads(raw) if isinstance(raw, str) else (raw or [])
                    except Exception:
                        models = []
                pinned = getattr(ep, "pinned_models", None)
                if pinned:
                    try:
                        pm = _json.loads(pinned) if isinstance(pinned, str) else (pinned or [])
                        for m in pm:
                            if m not in models:
                                models.append(m)
                    except Exception:
                        pass
                if getattr(ep, "model_type", "llm") == "image":
                    continue
                endpoints.append({
                    "id": ep.id,
                    "name": ep.name,
                    "models": sorted(models),
                })
        finally:
            db.close()

        from services.factory_orchestrator import AGENTS
        agents = []
        for k, v in AGENTS.items():
            agents.append({
                "key": k,
                "name": v["name"],
                "role": v["role"],
                "default_prompt": v["system"],
                "current_prompt": custom_prompts.get(k) or v["system"],
                "is_custom": bool(custom_prompts.get(k)),
            })

        from src.settings import get_setting as _gs2
        _concurrent = _gs2("factory_concurrent_tasks", None)
        if not _concurrent:
            _concurrent = 3

        _produce_max = get_setting("factory_produce_max_tokens", None)
        if not _produce_max:
            _produce_max = 16384

        return {"agents": agents, "agent_models": agent_models,
                "agent_prompts": custom_prompts,
                "agent_max_tokens": agent_max_tokens,
                "default_max_tokens": 16384,
                "concurrent_tasks": int(_concurrent),
                "produce_max_tokens": int(_produce_max),
                "endpoints": endpoints}

    @router.post("/settings")
    async def save_factory_settings(request: Request):
        """Save agent model assignments + custom prompts."""
        from src.settings import load_settings, save_settings
        body = await request.json()
        settings = load_settings()

        if "agent_models" in body:
            if not isinstance(body["agent_models"], dict):
                raise HTTPException(400, "agent_models must be an object")
            settings["factory_agent_models"] = body["agent_models"]

        if "agent_prompts" in body:
            if not isinstance(body["agent_prompts"], dict):
                raise HTTPException(400, "agent_prompts must be an object")
            cleaned = {k: v for k, v in body["agent_prompts"].items() if v and v.strip()}
            settings["factory_agent_prompts"] = cleaned

        if "agent_max_tokens" in body:
            if not isinstance(body["agent_max_tokens"], dict):
                raise HTTPException(400, "agent_max_tokens must be an object")
            cleaned = {}
            for k, v in body["agent_max_tokens"].items():
                try:
                    val = int(v)
                    if val > 0:
                        cleaned[k] = val
                except (ValueError, TypeError):
                    pass
            settings["factory_agent_max_tokens"] = cleaned

        if "concurrent_tasks" in body:
            try:
                val = int(body["concurrent_tasks"])
                if 1 <= val <= 10:
                    settings["factory_concurrent_tasks"] = val
            except (ValueError, TypeError):
                pass

        if "produce_max_tokens" in body:
            try:
                val = int(body["produce_max_tokens"])
                if 1024 <= val <= 65536:
                    settings["factory_produce_max_tokens"] = val
            except (ValueError, TypeError):
                pass

        save_settings(settings)
        return {"ok": True}

    @router.get("/projects/{project_id}")
    async def get_project(project_id: int):
        """Get a single project by ID."""
        result = svc.get_project(project_id)
        if not result:
            raise HTTPException(404, f"Project {project_id} not found")
        return result

    @router.get("/projects/{project_id}/download")
    async def download_project(project_id: int):
        """Download the project's delivery ZIP. Compiles on-demand if needed."""
        import os
        from fastapi.responses import FileResponse
        project = svc.get_project(project_id)
        if not project:
            raise HTTPException(404, f"Project {project_id} not found")

        zip_path = compile_delivery(project_id)
        if not zip_path or not os.path.exists(zip_path):
            raise HTTPException(404, "No completed tasks to download yet")

        title = project.get("title") or f"project_{project_id}"
        download_name = f"{title.replace(' ', '_')}.zip"
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=download_name,
        )

    @router.post("/projects/{project_id}/exec")
    async def exec_project_command(project_id: int, request: Request):
        """Execute a shell command in the project's workspace directory.

        Extracts completed files to data/factory/workspace/{id}/ on each call
        (ensures latest versions), then runs the command with cwd set there.
        """
        import os as _os
        from src.constants import DATA_DIR

        body = await request.json()
        cmd = body.get("command", "").strip()
        if not cmd:
            raise HTTPException(400, "command is required")

        project = svc.get_project(project_id)
        if not project:
            raise HTTPException(404, f"Project {project_id} not found")

        # Extract completed files to workspace (deduped by filename — latest version)
        workspace = _os.path.join(DATA_DIR, "factory", "workspace", str(project_id))
        _os.makedirs(workspace, exist_ok=True)

        nodes = svc.get_nodes(project_id)
        completed = [n for n in nodes if n.get("status") == "completed" and n.get("filename")]

        # Dedupe: latest task wins per filename
        by_filename = {}
        for n in completed:
            fname = n["filename"]
            if fname not in by_filename or n.get("id", 0) > by_filename[fname].get("id", 0):
                by_filename[fname] = n

        for fname, n in by_filename.items():
            output = _extract_output(n.get("result"))
            if not output:
                continue
            # Security: prevent path traversal — normalize and verify within workspace
            file_path = _os.path.normpath(_os.path.join(workspace, fname))
            if not file_path.startswith(workspace + _os.sep) and file_path != workspace:
                continue
            _os.makedirs(_os.path.dirname(file_path), exist_ok=True)
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(output)
            except Exception:
                pass  # skip unwritable files

        # Execute the command — async so we don't block the event loop
        import asyncio as _asyncio
        timeout = min(int(body.get("timeout", 60)), 300)
        try:
            proc = await _asyncio.create_subprocess_shell(
                cmd, cwd=workspace,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await _asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except _asyncio.TimeoutExpired:
                proc.kill()
                await proc.wait()
                return {
                    "stdout": "",
                    "stderr": f"Command timed out after {timeout}s",
                    "exit_code": -1,
                    "workspace": workspace,
                }
            stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
            stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
            return {
                "stdout": stdout[-20000:] if len(stdout) > 20000 else stdout,
                "stderr": stderr[-20000:] if len(stderr) > 20000 else stderr,
                "exit_code": proc.returncode,
                "workspace": workspace,
            }
        except Exception as e:
            return {
                "stdout": "",
                "stderr": str(e),
                "exit_code": -1,
                "workspace": workspace,
            }

    # ── Node.js project serve / preview ────────────────────────

    @router.post("/projects/{project_id}/serve")
    async def serve_project(project_id: int, request: Request):
        """Start a dev server for a Node.js project (npm install + npm run dev).

        Extracts files, runs npm install if needed, starts the dev server
        on port 4200+project_id. Returns a proxy URL for the iframe.
        """
        import json as _json
        import os as _os
        import signal as _signal
        from src.constants import DATA_DIR

        project = svc.get_project(project_id)
        if not project:
            raise HTTPException(404, f"Project {project_id} not found")

        # Stop any existing server for this project
        existing = _running_servers.get(project_id)
        if existing:
            proc, port = existing
            try:
                _os.killpg(_os.getpgid(proc.pid), _signal.SIGTERM)
            except Exception:
                pass
            _running_servers.pop(project_id, None)

        # Extract files to workspace (same as exec)
        workspace = _os.path.join(DATA_DIR, "factory", "workspace", str(project_id))
        _os.makedirs(workspace, exist_ok=True)

        nodes = svc.get_nodes(project_id)
        completed = [n for n in nodes if n.get("status") == "completed" and n.get("filename")]
        by_filename = {}
        for n in completed:
            fname = n["filename"]
            if fname not in by_filename or n.get("id", 0) > by_filename[fname].get("id", 0):
                by_filename[fname] = n

        for fname, n in by_filename.items():
            output = _extract_output(n.get("result"))
            if not output:
                continue
            file_path = _os.path.normpath(_os.path.join(workspace, fname))
            if not file_path.startswith(workspace + _os.sep) and file_path != workspace:
                continue
            _os.makedirs(_os.path.dirname(file_path), exist_ok=True)
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(output)
            except Exception:
                pass

        # Check for package.json
        pkg_json_path = _os.path.join(workspace, "package.json")
        if not _os.path.exists(pkg_json_path):
            # List what files ARE in the workspace for diagnostics
            ws_files = []
            for root, dirs, files in _os.walk(workspace):
                for f in files:
                    rel = _os.path.relpath(_os.path.join(root, f), workspace)
                    ws_files.append(rel)
            file_list = ", ".join(ws_files[:20]) if ws_files else "(empty)"
            raise HTTPException(400, f"No package.json found in workspace. Files present: {file_list}")

        try:
            with open(pkg_json_path, "r") as f:
                pkg = _json.load(f)
        except Exception:
            raise HTTPException(400, "Invalid package.json")

        scripts = pkg.get("scripts", {})
        # Priority: dev > start > preview
        run_script = None
        for candidate in ("dev", "start", "preview", "serve"):
            if candidate in scripts:
                run_script = candidate
                break
        if not run_script:
            available = ", ".join(scripts.keys()) if scripts else "(none)"
            raise HTTPException(400, f"No dev/start/preview/serve script in package.json. Available scripts: {available}")

        # Determine port
        port = 4200 + project_id

        # Run npm install (if node_modules doesn't exist)
        node_modules = _os.path.join(workspace, "node_modules")
        install_log = ""
        if not _os.path.exists(node_modules):
            try:
                install_proc = await _asyncio.create_subprocess_shell(
                    "npm install",
                    cwd=workspace,
                    stdout=_asyncio.subprocess.PIPE,
                    stderr=_asyncio.subprocess.STDOUT,
                )
                try:
                    stdout_b, _ = await _asyncio.wait_for(install_proc.communicate(), timeout=300)
                    install_log = stdout_b.decode("utf-8", errors="replace")[-3000:]
                except _asyncio.TimeoutExpired:
                    install_proc.kill()
                    await install_proc.wait()
                    raise HTTPException(500, "npm install timed out after 300s")
            except FileNotFoundError:
                raise HTTPException(500, "npm not found — Node.js is not installed on the server")
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(500, f"npm install failed: {e}")

        # Start the dev server as a background process
        env = dict(_os.environ)
        env["PORT"] = str(port)
        env["HOST"] = "0.0.0.0"
        # Vite-specific: set the port via flag if vite is the runner
        is_vite = "vite" in (pkg.get("devDependencies", {}) or {}) or "vite" in (pkg.get("dependencies", {}) or {})

        # Base path for the proxy — all asset URLs must go through the proxy
        # to avoid hitting Odysseus's root (which returns JSON 404s).
        proxy_base = f"/api/factory/projects/{project_id}/proxy/"

        if is_vite:
            # Vite --base makes ALL generated URLs include the prefix:
            # <script src="/@vite/client"> → <script src="/api/factory/projects/{id}/proxy/@vite/client">
            cmd = f"npx vite --port {port} --host 0.0.0.0 --base {proxy_base}"
        else:
            cmd = f"npm run {run_script}"

        try:
            proc = await _asyncio.create_subprocess_shell(
                cmd,
                cwd=workspace,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
                env=env,
                preexec_fn=_os.setsid if hasattr(_os, 'setsid') else None,
            )
        except Exception as e:
            raise HTTPException(500, f"Failed to start dev server: {e}")

        _running_servers[project_id] = (proc, port)

        # Wait a moment for the server to start, then verify it's reachable
        await _asyncio.sleep(3)
        import httpx as _httpx
        reachable = False
        for attempt in range(10):
            try:
                async with _httpx.AsyncClient(follow_redirects=True) as client:
                    resp = await client.get(f"http://localhost:{port}/", timeout=2)
                    if resp.status_code < 500:
                        reachable = True
                        break
            except Exception:
                pass
            await _asyncio.sleep(2)

        if not reachable:
            # Server didn't start — read stderr for diagnostics
            try:
                stderr_b = await _asyncio.wait_for(proc.stderr.read(2000), timeout=2)
                err_msg = stderr_b.decode("utf-8", errors="replace")
            except Exception:
                err_msg = "(no output)"
            try:
                _os.killpg(_os.getpgid(proc.pid), _signal.SIGTERM)
            except Exception:
                pass
            _running_servers.pop(project_id, None)
            raise HTTPException(500, f"Dev server failed to start. Output: {err_msg[:500]}")

        logger.info(f"Factory: dev server started for project {project_id} on port {port}")

        return {
            "url": f"/api/factory/projects/{project_id}/proxy/",
            "port": port,
            "script": run_script,
            "install_log": install_log[-500:] if install_log else None,
            "status": "running",
        }

    @router.post("/projects/{project_id}/serve/stop")
    async def stop_serve_project(project_id: int):
        """Stop the dev server for a project."""
        import os as _os
        import signal as _signal
        existing = _running_servers.get(project_id)
        if not existing:
            return {"status": "not_running"}
        proc, port = existing
        try:
            _os.killpg(_os.getpgid(proc.pid), _signal.SIGTERM)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        _running_servers.pop(project_id, None)
        logger.info(f"Factory: dev server stopped for project {project_id}")
        return {"status": "stopped"}

    @router.get("/projects/{project_id}/proxy/{path:path}")
    async def proxy_project(project_id: int, path: str, request: Request):
        """Proxy HTTP requests to the project's dev server."""
        import httpx as _httpx
        existing = _running_servers.get(project_id)
        if not existing:
            raise HTTPException(404, "Dev server not running")
        _, port = existing
        # Forward query params
        query = dict(request.query_params)
        target = f"http://localhost:{port}/{path}"
        try:
            async with _httpx.AsyncClient(follow_redirects=False) as client:
                resp = await client.get(target, params=query, timeout=10)
                excluded = {"transfer-encoding", "connection", "content-encoding", "content-length"}
                headers = {k: v for k, v in resp.headers.items() if k.lower() not in excluded}
                content_type = resp.headers.get("content-type", "")

                # For HTML responses: rewrite absolute paths to go through the proxy.
                # Vite handles this via --base, but CRA/Next.js/other frameworks
                # inject absolute paths (/static/js/main.js) that bypass the proxy.
                content = resp.content
                if "text/html" in content_type:
                    proxy_prefix = f"/api/factory/projects/{project_id}/proxy"
                    text = content.decode("utf-8", errors="replace")
                    # Rewrite src="/..." and href="/..." to include the proxy prefix.
                    # But DON'T rewrite paths that already start with the proxy prefix,
                    # or are protocol-relative (//) or absolute URLs (http://, https://).
                    import re as _re
                    text = _re.sub(
                        r'((?:src|href)\s*=\s*["\'])/(?!/|api/factory)',
                        rf'\1{proxy_prefix}/',
                        text
                    )
                    content = text.encode("utf-8")

                from fastapi.responses import Response
                return Response(content=content, status_code=resp.status_code,
                                headers=headers, media_type=content_type)
        except _httpx.ConnectError:
            raise HTTPException(502, "Dev server not responding")
        except Exception as e:
            raise HTTPException(502, f"Proxy error: {e}")

    @router.get("/projects/{project_id}/proxy/")
    async def proxy_project_root(project_id: int, request: Request):
        """Proxy root path to the dev server."""
        return await proxy_project(project_id, "", request)

    @router.put("/projects/{project_id}")
    async def update_project(project_id: int, request: Request):
        """Update project metadata (title, description, model)."""
        body = await request.json()
        try:
            result = svc.update_project(
                project_id=project_id,
                title=body.get("title"),
                description=body.get("description"),
                model=body.get("model"),
            )
            if not result:
                raise HTTPException(404, f"Project {project_id} not found")
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("update_project failed")
            raise HTTPException(500, str(e))

    @router.delete("/projects/{project_id}")
    async def delete_project(project_id: int):
        """Delete a project and all its nodes, edges, and events."""
        if not svc.delete_project(project_id):
            raise HTTPException(404, f"Project {project_id} not found")
        return {"deleted": True}

    # ── Project lifecycle ───────────────────────────────────────

    @router.post("/projects/{project_id}/start")
    async def start_project(project_id: int):
        """Start a project — transitions to running, marks root tasks ready."""
        try:
            return svc.start_project(project_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("start_project failed")
            raise HTTPException(500, str(e))

    @router.post("/projects/{project_id}/start-autonomous")
    async def start_project_autonomous(project_id: int, request: Request):
        """Start a project in autonomous mode — self-iterates until complete."""
        try:
            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            owner = body.get("owner", "default")
            result = svc.start_project(project_id)
            if not result:
                raise HTTPException(404, f"Project {project_id} not found")
            from services.factory_orchestrator import launch
            autonomous = bool(body.get("autonomous", True))
            launch(project_id, owner=owner, autonomous=autonomous)
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("start_project_autonomous failed")
            raise HTTPException(500, str(e))

    @router.post("/projects/{project_id}/iterate")
    async def iterate_project_route(project_id: int, request: Request):
        """Add new tasks to a completed/in-progress project via LLM planning.

        Launches planning as a background task (non-blocking) so the route
        returns immediately — the planner LLM call can take 30-120 seconds,
        which would exceed reverse proxy timeouts (504). The frontend polls
        for new tasks via the status endpoint.
        """
        body = await request.json()
        prompt = body.get("prompt", "").strip()
        if not prompt:
            raise HTTPException(400, "prompt is required")
        owner = body.get("owner", "default")

        project = svc.get_project(project_id)
        if not project:
            raise HTTPException(404, f"Project {project_id} not found")

        # Re-open completed projects synchronously so the frontend sees
        # the status change immediately (not 60s later when planning finishes)
        if project.get("status") == "completed":
            try:
                svc.set_project_status(project_id, "running")
            except Exception:
                pass  # transition may fail if already running — not critical

        # Log the iteration request
        svc._log_event_safe(project_id, None,
                            f"Iteration requested: {prompt[:200]}",
                            event_type="iteration_started")

        # Launch planning as a background task — returns immediately
        launch_iteration(project_id, prompt, owner=owner)

        # Return current state — new tasks appear via polling
        return svc.get_project(project_id)

    @router.post("/projects/{project_id}/cancel")
    async def cancel_project(project_id: int):
        """Cancel a project and all its pending/running tasks."""
        try:
            result = svc.cancel_project(project_id)
            if not result:
                raise HTTPException(404, f"Project {project_id} not found")
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/projects/{project_id}/reset")
    async def reset_project(project_id: int):
        """Reset a project back to planning state."""
        try:
            result = svc.reset_project(project_id)
            if not result:
                raise HTTPException(404, f"Project {project_id} not found")
            return result
        except Exception as e:
            logger.exception("reset_project failed")
            raise HTTPException(500, str(e))

    @router.post("/projects/{project_id}/status")
    async def set_project_status(project_id: int, request: Request):
        """Manually set project status (with validation)."""
        body = await request.json()
        new_status = body.get("status")
        if not new_status:
            raise HTTPException(400, "status is required")
        try:
            result = svc.set_project_status(project_id, new_status)
            if not result:
                raise HTTPException(404, f"Project {project_id} not found")
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/projects/{project_id}/pause")
    async def pause_project(project_id: int):
        """Pause a running project."""
        try:
            result = svc.pause_project(project_id)
            if not result:
                raise HTTPException(404, f"Project {project_id} not found")
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/projects/{project_id}/resume")
    async def resume_project(project_id: int, request: Request):
        """Resume a paused project and restart the orchestrator."""
        try:
            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            owner = body.get("owner", "default")
            autonomous = body.get("autonomous", False)
            result = svc.resume_project(project_id)
            if not result:
                raise HTTPException(404, f"Project {project_id} not found")
            launch(project_id, owner=owner, autonomous=autonomous)
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/projects/{project_id}/restart")
    async def restart_project(project_id: int, request: Request):
        """Restart a project — resets failed/stuck tasks and relaunches."""
        try:
            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            mode = body.get("mode", "partial")
            owner = body.get("owner", "default")
            result = svc.restart_project(project_id, mode=mode)
            if not result:
                raise HTTPException(404, f"Project {project_id} not found")
            svc.set_project_status(project_id, "running")
            relaunch(project_id, owner=owner)
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("restart_project failed")
            raise HTTPException(500, str(e))

    # ── Nodes / Tasks ──────────────────────────────────────────

    @router.post("/projects/{project_id}/nodes")
    async def add_node(project_id: int, request: Request):
        """Add a task node to a project."""
        body = await request.json()
        task_type = body.get("task_type")
        if not task_type:
            raise HTTPException(400, "task_type is required")
        try:
            return svc.add_node(
                project_id=project_id,
                task_type=task_type,
                title=body.get("title", ""),
                description=body.get("description", ""),
                assigned_agent=body.get("assigned_agent", ""),
                dependencies=body.get("dependencies", []),
                priority=body.get("priority", 0),
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("add_node failed")
            raise HTTPException(500, str(e))

    @router.get("/projects/{project_id}/nodes")
    async def list_nodes(project_id: int):
        """List all nodes for a project."""
        try:
            return svc.get_nodes(project_id)
        except Exception as e:
            logger.exception("list_nodes failed")
            raise HTTPException(500, str(e))

    @router.get("/nodes/{node_id}")
    async def get_node(node_id: int):
        """Get a single node by ID."""
        result = svc.get_node(node_id)
        if not result:
            raise HTTPException(404, f"Node {node_id} not found")
        return result

    @router.get("/nodes/{node_id}/preview")
    async def preview_node(node_id: int):
        """Return a completed task's HTML output as a standalone preview page.

        Served with its own permissive CSP so LLM-generated inline scripts +
        external fonts/styles run inside the preview iframe. The iframe is
        sandboxed client-side.
        """
        from fastapi.responses import HTMLResponse
        node = svc.get_node(node_id)
        if not node:
            raise HTTPException(404, f"Node {node_id} not found")
        if node.get("status") != "completed":
            raise HTTPException(409, f"Task not completed (status={node.get('status')})")
        output = _extract_output(node.get("result"))
        if not output:
            raise HTTPException(404, "No output to preview")
        response = HTMLResponse(content=output, media_type="text/html")
        response.headers["Content-Security-Policy"] = _FACTORY_PREVIEW_CSP
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    @router.post("/preview")
    async def post_preview(request: Request):
        """Stash project files for preview and return a token."""
        body = await request.json()
        files = body.get("files")
        main_file = body.get("main", "")
        if not files or not isinstance(files, dict) or not main_file:
            raise HTTPException(400, "files (dict) and main (filename) are required")
        if len(main_file) > 500:
            raise HTTPException(400, "Invalid main filename")
        total_size = sum(len(v) for v in files.values() if isinstance(v, str))
        if total_size > 10_000_000:  # 10 MB safety cap
            raise HTTPException(413, "Preview files too large")
        token = _store_preview(files, main_file)
        return {"token": token}

    @router.get("/preview/{token}")
    async def get_preview(token: str):
        """Serve the main preview page as standalone HTML."""
        from fastapi.responses import HTMLResponse
        entry = _get_preview(token)
        if not entry:
            raise HTTPException(404, "Preview not found or expired")
        files, main_file = entry
        html = files.get(main_file) or files.get("index.html") or ""
        if not html:
            raise HTTPException(404, "No preview content")
        response = HTMLResponse(content=html)
        response.headers["Content-Security-Policy"] = _FACTORY_PREVIEW_CSP
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    @router.get("/preview/{token}/{file_path:path}")
    async def get_preview_file(token: str, file_path: str):
        """Serve an individual file from a preview stash (JS, CSS, images).

        This makes relative URLs in the preview HTML resolve correctly:
        <script src="js/main.js"> → /api/factory/preview/{token}/js/main.js
        The browser fetches the file here with the correct MIME type.
        """
        import mimetypes
        from fastapi.responses import Response
        entry = _get_preview(token)
        if not entry:
            raise HTTPException(404, "Preview not found or expired")
        files, _ = entry
        # Try exact match, then basename match
        content = files.get(file_path)
        if content is None:
            basename = file_path.rsplit("/", 1)[-1]
            content = files.get(basename)
        if content is None:
            raise HTTPException(404, f"File not found: {file_path}")
        mimetype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        response = Response(content=content, media_type=mimetype)
        response.headers["Content-Security-Policy"] = _FACTORY_PREVIEW_CSP
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    @router.delete("/nodes/{node_id}")
    async def delete_node(node_id: int):
        """Delete a node and its edges."""
        if not svc.delete_node(node_id):
            raise HTTPException(404, f"Node {node_id} not found")
        return {"deleted": True}

    # ── Task status ────────────────────────────────────────────

    @router.post("/nodes/{node_id}/status")
    async def set_task_status(node_id: int, request: Request):
        """Update a task's status (with validation)."""
        body = await request.json()
        new_status = body.get("status")
        if not new_status:
            raise HTTPException(400, "status is required")
        try:
            result = svc.update_task_status(
                node_id=node_id,
                new_status=new_status,
                result=body.get("result"),
                error=body.get("error"),
            )
            if not result:
                raise HTTPException(404, f"Node {node_id} not found")
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/nodes/{node_id}/retry")
    async def retry_task(node_id: int, request: Request):
        """Retry a failed or blocked task and relaunch the orchestrator."""
        try:
            result = svc.retry_task(node_id)
            if not result:
                raise HTTPException(404, f"Node {node_id} not found")
        except ValueError as e:
            raise HTTPException(400, str(e))

        # Relaunch orchestrator for the parent project
        pid = result.get("project_id")
        if pid:
            owner = "default"
            try:
                body = await request.json()
                owner = body.get("owner", "default")
            except Exception:
                pass
            from services.factory_orchestrator import relaunch
            p = svc.get_project(pid)
            if p and p.get("status") == "paused":
                svc.resume_project(pid)
            relaunch(pid, owner=owner)
        return result

    @router.post("/tasks/{task_id}/retry")
    async def retry_task_alias(task_id: int, request: Request):
        """Alias — frontend uses /tasks/{id}/retry."""
        return await retry_task(task_id, request)

    @router.post("/nodes/{node_id}/complete")
    async def complete_task(node_id: int, request: Request = None):
        """Mark a task as completed with optional result."""
        body = {}
        try:
            if request:
                body = await request.json()
        except Exception:
            pass
        try:
            result = svc.complete_task(
                node_id=node_id,
                result=body.get("result"),
            )
            if not result:
                raise HTTPException(404, f"Node {node_id} not found")
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("complete_task failed")
            raise HTTPException(500, str(e))

    @router.post("/nodes/{node_id}/fail")
    async def fail_task(node_id: int, request: Request):
        """Mark a task as failed with an error message."""
        body = await request.json()
        try:
            result = svc.fail_task(
                node_id=node_id,
                error=body.get("error"),
            )
            if not result:
                raise HTTPException(404, f"Node {node_id} not found")
            return result
        except Exception as e:
            logger.exception("fail_task failed")
            raise HTTPException(500, str(e))

    # ── Edges ─────────────────────────────────────────────────

    @router.post("/projects/{project_id}/edges")
    async def add_edge(project_id: int, request: Request):
        """Add a dependency edge between two nodes."""
        body = await request.json()
        from_node_id = body.get("from_node_id")
        to_node_id = body.get("to_node_id")
        if from_node_id is None or to_node_id is None:
            raise HTTPException(400, "from_node_id and to_node_id are required")
        try:
            return svc.add_edge(project_id, from_node_id, to_node_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("add_edge failed")
            raise HTTPException(500, str(e))

    @router.delete("/edges/{edge_id}")
    async def remove_edge(edge_id: int):
        """Remove a dependency edge."""
        if not svc.remove_edge(edge_id):
            raise HTTPException(404, f"Edge {edge_id} not found")
        return {"deleted": True}

    # ── DAG / Kanban view ──────────────────────────────────────

    @router.get("/projects/{project_id}/dag")
    async def get_dag(project_id: int):
        """Get full DAG view with task stats."""
        return svc.get_dag(project_id)

    @router.get("/projects/{project_id}/tasks/ready")
    async def get_ready_tasks(project_id: int):
        """Get tasks that are ready to execute."""
        try:
            return svc.get_next_ready_tasks(project_id)
        except Exception as e:
            logger.exception("get_ready_tasks failed")
            raise HTTPException(500, str(e))

    # ── Execution Log ───────────────────────────────────────────

    @router.get("/projects/{project_id}/log")
    async def get_execution_log(project_id: int):
        """Get the execution event log for a project."""
        try:
            return svc.get_execution_log(project_id)
        except Exception as e:
            logger.exception("get_execution_log failed")
            raise HTTPException(500, str(e))

    return router
