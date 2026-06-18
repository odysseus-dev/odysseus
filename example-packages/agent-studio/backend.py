# agent-studio/backend.py
# Manages an Open WebUI Docker container and reverse-proxies its UI
# through Odysseus so it appears in pkg-app-view without cross-origin issues.

import logging
import asyncio
from typing import Optional

logger = logging.getLogger("pkg_agent_studio")

CONTAINER_NAME = "odysseus-agent-studio"
CONTAINER_IMAGE = "ghcr.io/open-webui/open-webui:main"
CONTAINER_INTERNAL_PORT = 8080
CONTAINER_HOST_PORT = 38080
PROXY_PREFIX = "/pkgs/agent-studio/proxy"

_docker_client = None


def _get_docker():
    global _docker_client
    if _docker_client is None:
        import docker
        _docker_client = docker.from_env()
    return _docker_client


def _container_info() -> dict:
    try:
        import docker
        c = _get_docker().containers.get(CONTAINER_NAME)
        return {"status": c.status, "id": c.id[:12], "name": c.name}
    except Exception:
        try:
            import docker
            if isinstance(Exception, docker.errors.NotFound):
                pass
        except Exception:
            pass
        return {"status": "not_found"}


def _get_default_endpoint_url() -> Optional[str]:
    """Return base_url of the default model endpoint configured in Odysseus."""
    try:
        from src.settings import load_settings
        from core.database import SessionLocal, ModelEndpoint
        settings = load_settings()
        ep_id = settings.get("default_endpoint_id", "")
        if not ep_id:
            return None
        with SessionLocal() as db:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
            if ep:
                return ep.base_url
    except Exception as e:
        logger.warning(f"[agent-studio] Could not get default endpoint: {e}")
    return None


def register_routes(app):
    import httpx
    from fastapi import APIRouter, Request, HTTPException
    from fastapi.responses import Response, JSONResponse

    router = APIRouter(prefix="/pkgs/agent-studio")

    # ---------- status ----------

    @router.get("/status")
    def status():
        try:
            import docker
            _get_docker()
        except Exception:
            return {"status": "error", "error": "Docker not available — install docker-py: pip install docker"}
        info = _container_info()
        # Enrich with port info
        if info["status"] == "running":
            info["url"] = f"http://localhost:{CONTAINER_HOST_PORT}"
        return info

    # ---------- start ----------

    @router.post("/start")
    async def start(request: Request):
        try:
            import docker
        except ImportError:
            raise HTTPException(500, "docker-py not installed. Run: pip install docker")

        client = _get_docker()
        info = _container_info()

        if info["status"] == "running":
            return info

        if info["status"] in ("exited", "stopped", "paused"):
            # Re-start existing container
            try:
                c = client.containers.get(CONTAINER_NAME)
                c.start()
                logger.info("[agent-studio] Restarted existing container")
                return {"status": "starting", "id": c.id[:12]}
            except Exception as e:
                logger.warning(f"[agent-studio] Re-start failed, will recreate: {e}")
                try:
                    c = client.containers.get(CONTAINER_NAME)
                    c.remove(force=True)
                except Exception:
                    pass

        # Detect LLM endpoint to configure Open WebUI
        endpoint_url = _get_default_endpoint_url()
        env = {
            "WEBUI_AUTH": "false",
            "ENABLE_SIGNUP": "false",
        }

        if endpoint_url:
            # If the endpoint looks like Ollama (no /v1/chat/completions) set OLLAMA_BASE_URL
            if "11434" in endpoint_url or "ollama" in endpoint_url.lower():
                # Strip /v1 suffix if present — Ollama native URL needed
                base = endpoint_url.rstrip("/")
                if base.endswith("/v1"):
                    base = base[:-3]
                env["OLLAMA_BASE_URL"] = base
                env["OLLAMA_API_BASE_URL"] = base
                logger.info(f"[agent-studio] Using Ollama endpoint: {base}")
            else:
                # OpenAI-compatible endpoint
                env["OPENAI_API_BASE_URL"] = endpoint_url
                env["OPENAI_API_KEY"] = "odysseus-local"
                env["ENABLE_OLLAMA_API"] = "false"
                logger.info(f"[agent-studio] Using OpenAI-compat endpoint: {endpoint_url}")
        else:
            # Default: point at localhost Ollama
            env["OLLAMA_BASE_URL"] = "http://host.docker.internal:11434"
            logger.info("[agent-studio] No endpoint configured, defaulting to host Ollama")

        # Ensure volume exists
        try:
            client.volumes.get("odysseus-agent-studio-data")
        except Exception:
            client.volumes.create("odysseus-agent-studio-data")

        # Pull image if not present (non-blocking: pull in background)
        try:
            client.images.get(CONTAINER_IMAGE)
        except Exception:
            logger.info(f"[agent-studio] Pulling {CONTAINER_IMAGE} in background...")
            asyncio.create_task(asyncio.to_thread(_pull_image))

        # Create and start container
        try:
            c = client.containers.run(
                CONTAINER_IMAGE,
                detach=True,
                name=CONTAINER_NAME,
                ports={f"{CONTAINER_INTERNAL_PORT}/tcp": CONTAINER_HOST_PORT},
                environment=env,
                volumes={
                    "odysseus-agent-studio-data": {
                        "bind": "/app/backend/data",
                        "mode": "rw",
                    }
                },
                extra_hosts={"host.docker.internal": "host-gateway"},
                restart_policy={"Name": "unless-stopped"},
            )
            logger.info(f"[agent-studio] Container started: {c.id[:12]}")
            return {"status": "starting", "id": c.id[:12]}
        except Exception as e:
            logger.error(f"[agent-studio] Failed to start container: {e}")
            raise HTTPException(500, str(e))

    # ---------- stop ----------

    @router.post("/stop")
    def stop():
        try:
            import docker
            c = _get_docker().containers.get(CONTAINER_NAME)
            c.stop(timeout=10)
            return {"status": "stopped"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ---------- remove ----------

    @router.delete("/remove")
    def remove():
        try:
            import docker
            c = _get_docker().containers.get(CONTAINER_NAME)
            c.remove(force=True)
            return {"status": "removed"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ---------- logs ----------

    @router.get("/logs")
    def logs(lines: int = 100):
        try:
            import docker
            c = _get_docker().containers.get(CONTAINER_NAME)
            raw = c.logs(tail=lines).decode("utf-8", errors="replace")
            return {"logs": raw}
        except Exception as e:
            return {"logs": "", "error": str(e)}

    # ---------- reverse proxy ----------

    @router.api_route(
        "/proxy",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    @router.api_route(
        "/proxy/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    async def proxy(request: Request, path: str = ""):
        target_base = f"http://localhost:{CONTAINER_HOST_PORT}"
        url = f"{target_base}/{path}"
        qs = request.url.query
        if qs:
            url += f"?{qs}"

        # Forward headers, strip hop-by-hop
        fwd_headers = {}
        skip = {
            "host", "connection", "transfer-encoding", "upgrade",
            "keep-alive", "proxy-authenticate", "proxy-authorization",
            "te", "trailers",
        }
        for k, v in request.headers.items():
            if k.lower() not in skip:
                fwd_headers[k] = v
        fwd_headers["host"] = f"localhost:{CONTAINER_HOST_PORT}"

        body = await request.body()

        try:
            async with httpx.AsyncClient(timeout=30.0) as hc:
                resp = await hc.request(
                    method=request.method,
                    url=url,
                    headers=fwd_headers,
                    content=body,
                    follow_redirects=False,
                )
        except httpx.ConnectError:
            return Response(
                content=_loading_page(),
                status_code=503,
                media_type="text/html",
            )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=502)

        # Build response headers — strip framing-blocker headers
        strip_resp = {
            "x-frame-options",
            "content-security-policy",
            "x-xss-protection",
            "transfer-encoding",
            "content-encoding",
        }
        resp_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in strip_resp
        }

        # Handle redirects: rewrite Location to stay within proxy
        if "location" in resp_headers:
            loc = resp_headers["location"]
            if loc.startswith("/"):
                resp_headers["location"] = f"{PROXY_PREFIX}{loc}"

        content = resp.content
        ct = resp_headers.get("content-type", "")

        # Inject <base href> into HTML so relative URLs go through proxy
        if "text/html" in ct and content:
            html = content.decode("utf-8", errors="replace")
            base_tag = f'<base href="{PROXY_PREFIX}/">'
            if "<head>" in html:
                html = html.replace("<head>", f"<head>{base_tag}", 1)
            elif "<html" in html:
                # insert after opening html tag
                idx = html.index("<html")
                end = html.index(">", idx) + 1
                html = html[:end] + base_tag + html[end:]
            else:
                html = base_tag + html
            content = html.encode("utf-8")
            resp_headers["content-length"] = str(len(content))

        return Response(
            content=content,
            status_code=resp.status_code,
            headers=resp_headers,
            media_type=ct or None,
        )

    app.include_router(router)
    logger.info("[agent-studio] Routes registered")


def _pull_image():
    try:
        logger.info(f"[agent-studio] Pulling {CONTAINER_IMAGE}...")
        _get_docker().images.pull(CONTAINER_IMAGE)
        logger.info("[agent-studio] Image pull complete")
    except Exception as e:
        logger.error(f"[agent-studio] Image pull failed: {e}")


def _loading_page() -> bytes:
    html = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<style>body{background:#0f0f1a;color:#94a3b8;font-family:sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column;gap:16px;}
.spinner{width:40px;height:40px;border:3px solid #333;border-top-color:#4f46e5;border-radius:50%;animation:spin 1s linear infinite;}
@keyframes spin{to{transform:rotate(360deg)}}
</style></head>
<body>
<div class="spinner"></div>
<div style="font-size:16px;">Agent Studio is starting up...</div>
<div style="font-size:13px;color:#6b7280;">This page will refresh automatically</div>
</body></html>"""
    return html.encode("utf-8")
