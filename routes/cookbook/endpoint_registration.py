"""Auto-register Cookbook-served models as model endpoints."""

import json
import logging
import uuid

from routes.cookbook_helpers import ServeRequest

logger = logging.getLogger(__name__)


def _auto_register_image_endpoint(
    req: ServeRequest,
    remote: str | None,
    serve_session_id: str | None = None,
) -> str | None:
    """Register a diffusion model as an image endpoint so it appears in the model selector."""
    import re
    from core.database import SessionLocal, ModelEndpoint
    from routes.model_routes import _normalize_model_ids
    from src.settings import load_settings, save_settings

    # Parse port from command (--port NNNN), default 8100 for diffusion_server
    port_match = re.search(r'--port\s+(\d+)', req.cmd)
    port = int(port_match.group(1)) if port_match else 8100

    # Determine host
    if remote:
        # SSH alias — use as hostname (Tailscale resolves it later)
        host = remote.split("@")[-1] if "@" in remote else remote
    else:
        host = "localhost"

    base_url = f"http://{host}:{port}/v1"

    # Friendly display name from repo_id
    short_name = req.repo_id.split("/")[-1] if "/" in req.repo_id else req.repo_id
    display_name = f"{short_name} (image)"
    pinned_models = [req.repo_id] if req.repo_id else []

    db = SessionLocal()
    try:
        # Check for existing endpoint with same base_url — update it
        existing = db.query(ModelEndpoint).filter(ModelEndpoint.base_url == base_url).first()
        if existing:
            existing.is_enabled = True
            existing.model_type = "image"
            # Follow the newly served model unless the user renamed the endpoint.
            pinned_names = {f"{m.split('/')[-1]} (image)" for m in _normalize_model_ids(existing.pinned_models)}
            if existing.name in pinned_names:
                existing.name = display_name
            existing.endpoint_kind = "local"
            existing.model_refresh_mode = "manual"
            existing.cookbook_serve_session_id = serve_session_id
            if pinned_models:
                existing.cached_models = json.dumps(pinned_models)
                existing.pinned_models = json.dumps(pinned_models)
            db.commit()
            settings = load_settings()
            if settings.get("image_gen_enabled") is not True:
                settings["image_gen_enabled"] = True
                save_settings(settings)
            logger.info(f"Updated existing image endpoint: {base_url}")
            return existing.id

        ep_id = f"img-{uuid.uuid4().hex[:8]}"
        ep = ModelEndpoint(
            id=ep_id,
            name=display_name,
            base_url=base_url,
            api_key=None,
            is_enabled=True,
            model_type="image",
            endpoint_kind="local",
            model_refresh_mode="manual",
            cached_models=json.dumps(pinned_models) if pinned_models else None,
            pinned_models=json.dumps(pinned_models) if pinned_models else None,
            cookbook_serve_session_id=serve_session_id,
        )
        db.add(ep)
        db.commit()
        settings = load_settings()
        settings["image_gen_enabled"] = True
        if not settings.get("image_model"):
            settings["image_model"] = req.repo_id
        save_settings(settings)
        logger.info(f"Auto-registered image endpoint: {display_name} @ {base_url}")
        return ep_id
    except Exception as e:
        logger.error(f"Failed to auto-register image endpoint: {e}")
        db.rollback()
        return None
    finally:
        db.close()


def _auto_register_llm_endpoint(
    req: ServeRequest,
    remote: str | None,
    serve_session_id: str | None = None,
) -> str | None:
    """Register a freshly-served LLM as a model endpoint so it appears in the
    model picker without a manual /setup step — the text-model sibling of
    _auto_register_image_endpoint.

    Cookbook serve commands launch an OpenAI-compatible server (llama.cpp's
    llama-server, vLLM, SGLang, or Ollama) on a known port. We point an
    endpoint at that server's /v1; the picker auto-discovers the model id by
    probing /v1/models and dims the endpoint until the server is reachable,
    so registering immediately (before the server finishes loading) is safe.
    """
    logger.info(
        f"_auto_register_llm_endpoint: ENTRY repo_id={req.repo_id!r} "
        f"remote={remote!r} cmd_prefix={req.cmd[:80]!r}"
    )
    import re
    from core.database import SessionLocal, ModelEndpoint
    from routes.model_routes import _normalize_model_ids

    # Port: ordered fallbacks so we match whatever the user actually
    # asked for, not a hardcoded default:
    #   1. explicit `--port N`  (vllm / sglang / llama-server)
    #   2. `OLLAMA_HOST=host:port`  (the way Ollama specifies its bind)
    #   3. fallback by backend (11434 ollama / 8080 llama.cpp)
    # Previously the OLLAMA_HOST form was silently ignored and we
    # registered every Ollama endpoint at 11434 — even if the user
    # set OLLAMA_HOST=0.0.0.0:11435 to avoid colliding with an
    # existing systemd Ollama, the registered endpoint pointed at
    # the OLD port and showed as offline.
    port_match = re.search(r'--port\s+(\d+)', req.cmd)
    # Host length is capped at the longest DNS name; unbounded, repeated
    # assignments make this scan quadratic and stall the event loop.
    ollama_host_match = re.search(r'OLLAMA_HOST=[^\s]{0,253}?:(\d+)', req.cmd)
    if port_match:
        port = int(port_match.group(1))
    elif ollama_host_match:
        port = int(ollama_host_match.group(1))
    elif "ollama" in req.cmd:
        port = 11434
    else:
        port = 8080  # llama.cpp's llama-server default — the Apple Silicon path

    # Determine host. The cookbook tmux for `local=true` serves runs INSIDE
    # the odysseus container — so the right URL for the in-container
    # backend to reach it is `localhost`, NOT `host.docker.internal`
    # (the latter points at the docker HOST, which doesn't have a server
    # on that port). The previous host.docker.internal fallback only made
    # sense for /setup-added external services like systemd Ollama on the
    # host — and those go through manual setup, not this auto-register
    # code path. For remote serves we still use the SSH host alias.
    if remote:
        host = remote.split("@")[-1] if "@" in remote else remote
    elif re.search(r"\bdocker\s+exec\s+(?:ollama-rocm|ollama-test)\b", req.cmd or ""):
        host = "host.docker.internal"
    else:
        host = "localhost"

    base_url = f"http://{host}:{port}/v1"

    short_name = req.repo_id.split("/")[-1] if "/" in req.repo_id else req.repo_id
    display_name = short_name or "Local model"
    is_mlx_deepseek_v4 = (
        "mlx_lm.server" in (req.cmd or "")
        and "deepseek-v4" in ((req.repo_id or "") + " " + (req.cmd or "")).lower()
    )
    mlx_shim_model_id = ""
    if is_mlx_deepseek_v4 and short_name:
        home_match = re.search(r"((?:/Users|/home)/[^/\s'\"]+)", req.cmd or "")
        remote_home = home_match.group(1) if home_match else ""
        if remote_home:
            mlx_shim_model_id = f"{remote_home}/.cache/odysseus/mlx-shims/{short_name}"

    # If the serve command opts models into OpenAI tool-calling, record it so
    # agent_loop trusts emitted tool_calls instead of the name heuristic.
    is_ollama_endpoint = "ollama" in (req.cmd or "").lower()
    supports_tools = True if "--enable-auto-tool-choice" in req.cmd else None
    # Pin the model the user launched for every Cookbook-created LLM
    # endpoint, not just Ollama. Some OpenAI-compatible servers report a
    # deployment alias from /v1/models, and a stale server can answer on the
    # same port while the new launch failed. Keeping the requested model id
    # pinned makes the picker reflect the actual launch intent.
    launched_model = mlx_shim_model_id or req.repo_id
    pinned_models = [launched_model] if launched_model else []

    # Launch registration must not delete other endpoints.
    # Matching names, model pins, and failed probes do not prove that
    # another serve has stopped. Lifecycle cleanup owns termination.
    db = SessionLocal()
    try:
        # Reuse an endpoint already pointed at this URL instead of duplicating.
        existing = db.query(ModelEndpoint).filter(ModelEndpoint.base_url == base_url).first()
        if existing:
            existing.is_enabled = True
            existing.model_type = "llm"
            # Follow the newly served model unless the user renamed the endpoint.
            pinned_names = {m.split("/")[-1] for m in _normalize_model_ids(existing.pinned_models)}
            if existing.name in pinned_names:
                existing.name = display_name
            existing.endpoint_kind = "local"
            existing.model_refresh_mode = "auto"
            # A reused endpoint belongs to the newest serve session.
            existing.cookbook_serve_session_id = serve_session_id
            if launched_model:
                # Pins represent model selection, not launch history.
                existing_pins = _normalize_model_ids(
                    existing.pinned_models
                )
                if launched_model not in existing_pins:
                    existing_pins.append(launched_model)
                existing.pinned_models = json.dumps(existing_pins)
            if is_ollama_endpoint:
                existing.endpoint_kind = "ollama"
                if pinned_models:
                    existing.cached_models = json.dumps(pinned_models)
            if supports_tools is not None:
                existing.supports_tools = supports_tools
            db.commit()
            logger.info(f"Updated existing local model endpoint: {base_url}")
            # Re-probe so cached_models matches what the server actually
            # serves right now (the URL may have stayed the same but the
            # model behind it changed across launches).
            try:
                if mlx_shim_model_id:
                    existing.cached_models = json.dumps([mlx_shim_model_id])
                    existing.pinned_models = json.dumps([mlx_shim_model_id])
                    db.commit()
                else:
                    from routes.model_routes import _probe_endpoint
                    import json as _json2
                    probed = _probe_endpoint(base_url, existing.api_key, timeout=5)
                    if probed:
                        existing.cached_models = _json2.dumps(probed)
                        db.commit()
            except Exception as _pe:
                logger.warning(f"Re-probe failed for {base_url}: {_pe!r}")
            return existing.id

        ep_id = f"local-{uuid.uuid4().hex[:8]}"
        ep = ModelEndpoint(
            id=ep_id,
            name=display_name,
            base_url=base_url,
            api_key=None,
            is_enabled=True,
            model_type="llm",
            endpoint_kind="ollama" if is_ollama_endpoint else "local",
            model_refresh_mode="auto",
            cached_models=json.dumps(pinned_models) if pinned_models else None,
            pinned_models=json.dumps(pinned_models) if pinned_models else None,
            supports_tools=supports_tools,
            cookbook_serve_session_id=serve_session_id,
        )
        db.add(ep)
        db.commit()
        logger.info(f"Auto-registered local model endpoint: {display_name} @ {base_url}")
        # Probe /v1/models NOW and write cached_models so the chat
        # picker actually shows the model on the next /api/models
        # call. Without this immediate probe, the endpoint has empty
        # cached_models until the next background refresh fires (up
        # to a minute later) and the picker shows nothing — even
        # though the endpoint is in the DB and the server is up.
        try:
            if mlx_shim_model_id:
                ep.cached_models = json.dumps([mlx_shim_model_id])
                ep.pinned_models = json.dumps([mlx_shim_model_id])
                db.commit()
                logger.info(f"Auto-register: pinned MLX DeepSeek-V4 shim model @ {base_url}")
            else:
                from routes.model_routes import _probe_endpoint
                import json as _json2
                probed = _probe_endpoint(base_url, None, timeout=5)
                if probed:
                    ep.cached_models = _json2.dumps(probed)
                    db.commit()
                    logger.info(f"Auto-register: probed {len(probed)} models @ {base_url}")
        except Exception as _pe:
            logger.warning(f"Auto-register: probe-after-create failed for {base_url}: {_pe!r}")
        return ep_id
    except Exception as e:
        logger.error(f"Failed to auto-register local model endpoint: {e}")
        db.rollback()
        return None
    finally:
        db.close()
