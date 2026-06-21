import re
from copy import deepcopy

from fastapi import APIRouter, HTTPException

from routes._validators import validate_remote_host, validate_ssh_port


# Backends the manual hardware simulator accepts. Must stay a subset of what
# services.hwfit.fit understands so a simulated box ranks like a real one:
# "metal" routes through the Apple-Silicon path (GGUF-only, llama.cpp/Ollama),
# the CPU backends through the RAM/offload path, cuda/rocm through vLLM.
_MANUAL_BACKENDS = {"cuda", "rocm", "metal", "cpu_x86", "cpu_arm"}


def _validate_detection_target(host: str = "", ssh_port: str = "") -> tuple[str, str]:
    host_value = validate_remote_host(host) or ""
    port_value = validate_ssh_port(ssh_port) or ""
    if port_value and not host_value:
        raise HTTPException(400, "ssh_port requires host")
    return host_value, port_value


def _apply_manual_hardware(system, manual_mode="", manual_gpu_count="", manual_vram_gb="", manual_ram_gb="", manual_backend=""):
    """Manual hardware is a "what if I had this setup" simulator —
    REPLACES the detected hardware entirely instead of adding to it.

    The previous additive behavior averaged the manual VRAM across
    all GPUs (base + manual), which meant adding "1× 400 GB" on top
    of "2× 70 GB" only nudged the per-GPU cap from 70 to 180 GB
    (= 540 / 3), so GGUF models bigger than that still didn't surface
    — exactly the "cap stuck at detected level" bug the user hit.
    """
    manual_mode = (manual_mode or "").lower()
    if manual_mode not in {"gpu", "ram"}:
        return system

    try:
        override_ram_gb = float(manual_ram_gb) if manual_ram_gb else 0
    except ValueError:
        override_ram_gb = 0
    override_ram_gb = max(0.0, override_ram_gb)
    if override_ram_gb:
        # Replace RAM, don't add. The number in the field is the
        # TOTAL system memory the user wants to simulate.
        system["available_ram_gb"] = round(override_ram_gb, 1)
        system["total_ram_gb"] = round(override_ram_gb, 1)
    system["manual_hardware"] = True

    if manual_mode == "ram":
        # RAM-only simulation — wipe GPU entirely so the ranker uses
        # CPU/RAM paths.
        system["has_gpu"] = False
        system["gpu_name"] = None
        system["gpu_vram_gb"] = 0
        system["gpu_count"] = 0
        system["gpus"] = []
        system["gpu_groups"] = []
        system["backend"] = "cpu_x86"
        system.pop("unified_memory", None)
        return system

    try:
        count = int(manual_gpu_count) if manual_gpu_count else 1
    except ValueError:
        count = 1
    try:
        vram_each = float(manual_vram_gb) if manual_vram_gb else 8.0
    except ValueError:
        vram_each = 8.0
    count = max(1, min(count, 16))
    vram_each = max(1.0, vram_each)
    backend = (manual_backend or system.get("backend") or "cuda").lower()
    if backend not in _MANUAL_BACKENDS:
        backend = "cuda"
    total_vram = round(vram_each * count, 1)
    gpu_name = f"Simulated {backend.upper()} GPU" + (f" × {count}" if count > 1 else "")
    system["has_gpu"] = True
    system["gpu_name"] = gpu_name
    system["gpu_vram_gb"] = total_vram
    system["gpu_count"] = count
    system["gpus"] = [
        {"index": i, "name": gpu_name, "vram_gb": vram_each}
        for i in range(count)
    ]
    # Single homogeneous pool — vram_each here is the ACTUAL per-GPU
    # VRAM the user entered, not an average. That's the whole point:
    # raising vram_each lifts the per-GPU cap (GGUF, tensor-parallel
    # math) all the way up, not just by a small fraction.
    system["gpu_groups"] = [{
        "name": gpu_name,
        "vram_each": vram_each,
        "count": count,
        "indices": list(range(count)),
        "vram_total": total_vram,
    }]
    system["homogeneous"] = True
    system["backend"] = backend
    # Apple Silicon shares one unified memory pool with the GPU; flag it so
    # the API/UI report it the way real Metal detection does. Discrete GPUs
    # (cuda/rocm) and the CPU backends carry separate VRAM, so clear any
    # stale flag a previous detection left on the dict.
    if backend == "metal":
        system["unified_memory"] = True
    else:
        system.pop("unified_memory", None)
    return system


def _apply_ram_override(system, override_ram_gb=""):
    """Override only the available-RAM budget the ranker uses, leaving the
    DETECTED GPU intact.

    Distinct from the manual-hardware simulator: probed ``available_ram_gb`` is
    just the live free memory at scan time, not a real ceiling. This lets the
    user say "rank as if I'll free N GB before serving" (the inline RAM-budget
    chip) without faking a different GPU, so speed estimates keep using the real
    card's bandwidth. ``total_ram_gb`` is bumped to at least the override so the
    chip's "used / total" reads sensibly.
    """
    try:
        ov = float(override_ram_gb) if override_ram_gb else 0.0
    except (TypeError, ValueError):
        return system
    if ov <= 0:
        return system
    ov = round(min(ov, 100000.0), 1)
    system["available_ram_gb"] = ov
    if (system.get("total_ram_gb") or 0) < ov:
        system["total_ram_gb"] = ov
    system["ram_override_gb"] = ov
    return system


def setup_hwfit_routes():
    router = APIRouter(prefix="/api/hwfit", tags=["hwfit"])

    @router.get("/system")
    def get_system(host: str = "", ssh_port: str = "", platform: str = "", fresh: bool = False):
        """Detect and return current system hardware info. Pass host=user@server for remote.
        fresh=true bypasses the per-host cache (the Rescan button)."""
        from services.hwfit.hardware import detect_system
        host, ssh_port = _validate_detection_target(host, ssh_port)
        return detect_system(host=host, ssh_port=ssh_port, platform=platform, fresh=fresh)

    @router.get("/models")
    def get_models(use_case: str = "", sort: str = "newest", limit: int = 50, search: str = "", host: str = "", quant: str = "", ctx: str = "", gpu_count: str = "", gpu_group: str = "", ssh_port: str = "", platform: str = "", fresh: bool = False, manual_mode: str = "", manual_gpu_count: str = "", manual_vram_gb: str = "", manual_ram_gb: str = "", manual_backend: str = "", ignore_detected_gpu: bool = False, ignore_detected_ram: bool = False, override_ram_gb: str = "", fit_only: bool = False):
        """Rank LLM models against detected hardware and return scored results.
        gpu_count: override GPU count (0 = CPU only, 1-N = simulate N GPUs of the
            active group). gpu_group: index into system.gpu_groups (the homogeneous
            pools) to target — empty/auto = the largest pool. vLLM can only
            tensor-parallel across identical GPUs, so we never mix pools.
        fresh=true bypasses the hardware-detection cache."""
        from services.hwfit.hardware import detect_system
        from services.hwfit.fit import rank_models
        from services.hwfit.catalog_sync import get_catalog_or_static, upsert_discovered_models
        from services.hwfit.models import get_models, model_catalog_path
        host, ssh_port = _validate_detection_target(host, ssh_port)
        system = deepcopy(detect_system(host=host, ssh_port=ssh_port, platform=platform, fresh=fresh))
        if system.get("error"):
            return {"system": system, "models": [], "error": system["error"]}
        if not get_models():
            return {
                "system": system,
                "models": [],
                "error": f"Model catalog missing or empty: {model_catalog_path()}",
            }

        if ignore_detected_gpu:
            system["has_gpu"] = False
            system["gpu_name"] = None
            system["gpu_vram_gb"] = 0
            system["gpu_count"] = 0
            system["gpus"] = []
            system["gpu_groups"] = []
        if ignore_detected_ram:
            system["available_ram_gb"] = 0
            system["total_ram_gb"] = 0

        # True installed RAM, captured before any simulator/override mutates it,
        # so the inline RAM-budget control can cap its slider at the real ceiling.
        system["detected_ram_total_gb"] = system.get("total_ram_gb")

        system = _apply_manual_hardware(system, manual_mode, manual_gpu_count, manual_vram_gb, manual_ram_gb, manual_backend)
        system = _apply_ram_override(system, override_ram_gb)

        # Keep the raw detection around so the UI can still show the box's full
        # GPU complement even while we rank against one homogeneous pool.
        system["detected_gpu_vram_gb"] = system.get("gpu_vram_gb")
        system["detected_gpu_count"] = system.get("gpu_count")

        groups = system.get("gpu_groups") or []
        # Resolve the target homogeneous pool. Default (auto) = the largest pool,
        # which for a uniform box is simply "all the GPUs" — no behaviour change.
        grp = None
        if groups:
            try:
                gidx = int(gpu_group) if gpu_group != "" else 0
            except ValueError:
                gidx = 0
            if 0 <= gidx < len(groups):
                grp = groups[gidx]

        def _apply_group(g, n):
            n = max(1, min(n, g["count"]))
            system["gpu_count"] = n
            system["gpu_vram_gb"] = round(g["vram_each"] * n, 1)
            system["gpu_name"] = g["name"]
            system["active_group"] = {**g, "use_count": n}

        # Parse the optional count defensively (matches the gpu_group guard
        # above): a non-numeric query param previously raised ValueError ->
        # HTTP 500. A malformed value is ignored, same as omitting it.
        try:
            n = int(gpu_count) if gpu_count != "" else None
        except ValueError:
            n = None
        if n is not None:
            if n == 0:
                # RAM-only mode: rank against system memory, offload allowed.
                system["has_gpu"] = False
                system["gpu_vram_gb"] = 0
                system["gpu_count"] = 0
                system["gpu_only"] = False
                system.pop("active_group", None)
            elif grp:
                _apply_group(grp, n)
                system["gpu_only"] = True
            else:
                # No per-GPU detail (older detection) — assume uniform split.
                single_vram = (system.get("gpu_vram_gb") or 0) / (system.get("gpu_count") or 1)
                system["gpu_count"] = max(1, n)
                system["gpu_vram_gb"] = round(single_vram * max(1, n), 1)
                system["gpu_only"] = True
        elif grp:
            # No explicit count, but we still pin to one pool so heterogeneous
            # boxes rank against a real mixable group, not a fictional VRAM sum.
            # gpu_only stays off here so the default view still surfaces offload.
            _apply_group(grp, grp["count"])

        try:
            target_context = int(ctx) if ctx else None
        except ValueError:
            target_context = None
        if target_context is not None:
            target_context = max(1024, min(target_context, 1000000))

        from services.hwfit.local_scanner import scan_local_gguf
        from services.hwfit.lmstudio_catalog import fetch_lmstudio_models
        from services.hwfit.ollama_catalog import fetch_ollama_models
        catalog_models = get_catalog_or_static(seed_if_empty=True)
        local_models = scan_local_gguf() if not host else []
        lmstudio_models = fetch_lmstudio_models(host=host)
        ollama_models = fetch_ollama_models(host=host)
        live_models = local_models + lmstudio_models + ollama_models
        if live_models:
            try:
                from core.database import get_db_session
                with get_db_session() as db:
                    upsert_discovered_models(db, live_models)
                    db.commit()
            except Exception:
                pass
        results = rank_models(system, use_case=use_case or None, limit=limit, search=search or None, sort=sort, quant=quant or None, target_context=target_context, fit_only=fit_only, extra_models=live_models, catalog_models=catalog_models)
        return {"system": system, "models": results}

    @router.get("/profiles")
    def get_serve_profiles(model: str = "", host: str = "", ssh_port: str = "", platform: str = "", fresh: bool = False, serve_weights_gb: float = 0.0, serve_quant: str = ""):
        """Compute llama.cpp serve profiles (Quality/Balanced/Speed) for `model`
        against the detected hardware on `host` (or local). Returns concrete
        flags (n_gpu_layers, n_cpu_moe, cache_type, ctx) the serve UI can apply.

        `model` is matched against the catalog by name; if it's not in the
        catalog (e.g. an ad-hoc HF repo), pass enough hints via a minimal synthetic
        entry isn't possible here, so we return [] and the UI keeps manual flags.
        """
        from services.hwfit.hardware import detect_system
        from services.hwfit.models import get_models
        from services.hwfit.profiles import compute_serve_profiles
        host, ssh_port = _validate_detection_target(host, ssh_port)
        system = detect_system(host=host, ssh_port=ssh_port, platform=platform, fresh=fresh)
        if system.get("error"):
            return {"system": system, "profiles": [], "error": system["error"]}
        catalog = {m.get("name"): m for m in (get_models() or [])}

        def _norm(s):
            # Normalize for matching: drop org/ prefix, a trailing -GGUF/-gguf
            # marker, and any quant tag, lowercase. So "DeepSeek-Coder-V2-Lite-
            # Instruct-GGUF" (a local folder name) matches catalog entry
            # "deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct".
            s = (s or "").lower().strip()
            s = s.split("/")[-1]                     # drop org prefix
            s = re.sub(r"[-_.]?gguf$", "", s)        # drop trailing gguf marker
            s = re.sub(r"[-_.](q\d[^/]*|iq\d[^/]*|fp8|bf16|f16|awq[^/]*|gptq[^/]*)$", "", s)
            return s

        m = catalog.get(model)
        if m is None and model:
            want = _norm(model)
            for name, entry in catalog.items():
                nn = _norm(name)
                if nn and (nn == want or want.endswith(nn) or nn.endswith(want)):
                    m = entry
                    break
        if m is None:
            return {"system": system, "profiles": [], "error": "model not in catalog"}
        # Surface the model's trained context limit so the serve UI can clamp a
        # user-typed context down to it (asking for ctx > n_ctx_train overflows
        # and, with a quantized KV cache, can crash the GPU).
        model_ctx_max = 0
        for k in ("context_length", "max_position_embeddings", "n_ctx_train", "context"):
            v = m.get(k)
            if isinstance(v, (int, float)) and v > 0:
                model_ctx_max = int(v)
                break
        # Compute the smart initial -ngl default. Uses analyze_model() to determine
        # whether the model fits fully on GPU, partially offloads, or is CPU-only,
        # then translates that to an integer layer count (99/proportional/0).
        from services.hwfit.fit import analyze_model, _default_ram_budget
        from routes.cookbook_helpers import _compute_gpu_layers
        # Rank the serve recommendation against the model's default RAM budget
        # (e.g. 20 GB for the 26B) so the panel's -ngl matches the What Fits
        # verdict instead of whatever RAM happens to be free this second. The
        # explicit RAM override still wins; copy the dict so the cached detection
        # result isn't mutated.
        _mb = _default_ram_budget(m)
        if _mb > 0 and not system.get("ram_override_gb"):
            _cap = system.get("total_ram_gb") or _mb
            _budget = round(min(_mb, float(_cap)), 1)
            system = {**system, "available_ram_gb": _budget, "ram_budget_gb": _budget}
        _fit = analyze_model(m, system)
        recommended_ngl = _compute_gpu_layers(
            (_fit or {}).get("run_mode", "gpu"),
            (_fit or {}).get("vram_gb"),
            (_fit or {}).get("required_gb"),
        )
        return {
            "system": system,
            "profiles": compute_serve_profiles(
                system, m,
                serve_weights_gb=(serve_weights_gb or None),
                serve_quant=(serve_quant or None),
            ),
            "model_ctx_max": model_ctx_max,
            "recommended_ngl": recommended_ngl,
        }

    @router.get("/image-models")
    def get_image_models(sort: str = "fit", search: str = "", host: str = "", gpu_count: str = "", ssh_port: str = "", platform: str = "", fresh: bool = False, manual_mode: str = "", manual_gpu_count: str = "", manual_vram_gb: str = "", manual_ram_gb: str = "", manual_backend: str = "", ignore_detected_gpu: bool = False, ignore_detected_ram: bool = False):
        """Rank image generation models against detected hardware."""
        from services.hwfit.hardware import detect_system
        from services.hwfit.image_models import rank_image_models
        host, ssh_port = _validate_detection_target(host, ssh_port)
        system = deepcopy(detect_system(host=host, ssh_port=ssh_port, platform=platform, fresh=fresh))
        if system.get("error"):
            return {"system": system, "models": [], "error": system["error"]}
        if ignore_detected_gpu:
            system["has_gpu"] = False
            system["gpu_name"] = None
            system["gpu_vram_gb"] = 0
            system["gpu_count"] = 0
            system["gpus"] = []
            system["gpu_groups"] = []
        if ignore_detected_ram:
            system["available_ram_gb"] = 0
            system["total_ram_gb"] = 0
        system = _apply_manual_hardware(system, manual_mode, manual_gpu_count, manual_vram_gb, manual_ram_gb, manual_backend)
        # Image models use a single GPU — always use per-GPU VRAM
        gpu_vrams = [float(g.get("vram_gb") or 0) for g in (system.get("gpus") or []) if isinstance(g, dict)]
        single_vram = max(gpu_vrams) if gpu_vrams else ((system.get("gpu_vram_gb") or 0) / max(system.get("gpu_count") or 1, 1))
        system["gpu_vram_gb"] = single_vram
        system["gpu_count"] = 1 if single_vram > 0 else 0
        results = rank_image_models(system, search=search or None, sort=sort)
        return {"system": system, "models": results}

    @router.get("/model-caps")
    def get_model_caps(model: str = ""):
        """Return the capability list for a model ID looked up in the catalog DB.

        Performs an exact name match first, then a suffix match on the portion
        after the last ``/``. Returns an empty capability list (not an error)
        when the model is unknown so the frontend can degrade gracefully.
        """
        if not model:
            return {"model_id": model, "capabilities": [], "found": False}
        from core.database import get_db_session, DiscoveredModel
        with get_db_session() as db:
            row = db.query(DiscoveredModel).filter(
                DiscoveredModel.name == model
            ).first()
            if row is None:
                suffix = model.split("/")[-1]
                row = (
                    db.query(DiscoveredModel)
                    .filter(DiscoveredModel.name.like(f"%/{suffix}"))
                    .first()
                )
            if row is not None:
                caps = row.capabilities if isinstance(row.capabilities, list) else []
                return {"model_id": model, "capabilities": caps, "found": True}
        return {"model_id": model, "capabilities": [], "found": False}

    return router
