import json
import os
import re
import shlex
import subprocess
from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException

from core.middleware import require_admin
from core.platform_compat import run_ssh_command
from routes._validators import validate_remote_host, validate_ssh_port


# Backends the manual hardware simulator accepts. Must stay a subset of what
# services.hwfit.fit understands so a simulated box ranks like a real one:
# "metal" routes through the Apple-Silicon path (GGUF-only, llama.cpp/Ollama),
# the CPU backends through the RAM/offload path, cuda/rocm through vLLM.
_MANUAL_BACKENDS = {"cuda", "rocm", "metal", "cpu_x86", "cpu_arm"}
_OFFICIAL_NAMESPACES = {
    "apple", "allenai", "black-forest-labs", "cohere", "deepseek-ai",
    "google", "ibm", "lightricks", "meta-llama", "microsoft", "mistralai",
    "nvidia", "openai", "qwen", "stabilityai", "tencent", "tiiuae",
    "upstage", "zai-org", "runwayml",
}


def _is_official_model(model: dict) -> bool:
    """Recognize first-party namespaces without maintaining model-name lists."""
    # Image rows expose a friendly `name` without its namespace, while regular
    # rows may use `name`. Prefer whichever field still contains `owner/repo`.
    model_id = str(model.get("id") or model.get("name") or "")
    namespace = model_id.split("/", 1)[0].strip().lower() if "/" in model_id else ""
    # `provider` is a display label for image rows (for example, "Stability AI")
    # and is not a stable repository namespace. The model id is the canonical
    # source for this filter, so a recognized namespace is sufficient.
    return namespace in _OFFICIAL_NAMESPACES


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


def _run_model_probe(host: str, ssh_port: str, cmd: str) -> str:
    try:
        if host:
            r = run_ssh_command(
                host,
                ssh_port or None,
                cmd,
                timeout=15,
                connect_timeout=5,
                strict_host_key_checking=False,
                text=True,
            )
        else:
            r = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return (r.stdout or "").strip()
    except Exception:
        return ""
    return ""


def _parse_gguf_kv_header(gguf_path: str) -> dict:
    """Parse lightweight architecture metadata from a GGUF v2/v3 file header."""
    import struct
    try:
        with open(gguf_path, "rb") as f:
            if f.read(4) != b"GGUF":
                return {}
            ver = struct.unpack("<I", f.read(4))[0]
            if ver not in (2, 3):
                return {}
            _n_tensors, n_kv = struct.unpack("<QQ", f.read(16))
            scalar_sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
            scalar_fmts = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}

            def _read_str():
                ln = struct.unpack("<Q", f.read(8))[0]
                if ln > 1_000_000:
                    raise ValueError("oversized string")
                return f.read(ln).decode("utf-8", errors="replace")

            def _skip_val(vtype):
                if vtype in scalar_sizes:
                    f.seek(scalar_sizes[vtype], 1)
                elif vtype == 8:
                    ln = struct.unpack("<Q", f.read(8))[0]
                    f.seek(ln, 1)
                elif vtype == 9:
                    atype, alen = struct.unpack("<IQ", f.read(12))
                    if atype in scalar_sizes:
                        f.seek(scalar_sizes[atype] * alen, 1)
                    else:
                        for _ in range(alen):
                            _skip_val(atype)
                else:
                    raise ValueError(f"unknown gguf type {vtype}")

            wanted_suffixes = {
                ".block_count": "num_hidden_layers",
                ".attention.head_count_kv": "num_key_value_heads",
                ".attention.head_count": "num_attention_heads",
                ".attention.key_length": "head_dim",
                ".embedding_length": "hidden_size",
                ".context_length": "model_ctx_max",
            }
            meta = {}
            for _ in range(min(int(n_kv), 2048)):
                key = _read_str()
                vtype = struct.unpack("<I", f.read(4))[0]
                matched = None
                for suffix, dest in wanted_suffixes.items():
                    if key.endswith(suffix):
                        matched = dest
                        break
                if matched and vtype in scalar_fmts:
                    val = struct.unpack(scalar_fmts[vtype], f.read(scalar_sizes[vtype]))[0]
                    if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
                        meta[matched] = int(val)
                else:
                    _skip_val(vtype)
            return meta
    except Exception:
        return {}


def _extract_arch_from_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        return {}
    arch = {}
    for dest, keys in (
        ("num_hidden_layers", ("num_hidden_layers", "n_layers", "num_layers", "block_count")),
        ("num_key_value_heads", ("num_key_value_heads", "n_kv_heads", "num_kv_heads", "head_count_kv")),
        ("num_attention_heads", ("num_attention_heads", "n_heads", "num_heads", "head_count")),
        ("head_dim", ("head_dim", "key_length", "value_length")),
        ("hidden_size", ("hidden_size", "embedding_length", "d_model")),
        ("kv_lora_rank", ("kv_lora_rank",)),
        ("qk_rope_head_dim", ("qk_rope_head_dim",)),
    ):
        for k in keys:
            v = cfg.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                arch[dest] = int(v)
                break
    return arch


def _inspect_model_path(model_path: str, host: str = "", ssh_port: str = "") -> dict:
    """Read lightweight metadata from a local or SSH-visible HF/GGUF model path."""
    path = (model_path or "").strip()
    if not path or path.startswith(("http://", "https://")):
        return {}
    if not (os.path.isabs(path) or path.startswith("~")):
        return {}

    if not host and os.path.isfile(path) and path.lower().endswith(".gguf"):
        out = _parse_gguf_kv_header(path)
        try:
            sz = os.path.getsize(path)
            if sz > 0:
                out["model_weights_gb"] = round(sz / 1073741824.0, 6)
        except Exception:
            pass
        return out

    qpath = shlex.quote(path)
    qconfig = shlex.quote(os.path.join(path, "config.json"))
    out = {}
    exists = _run_model_probe(host, ssh_port, f"test -e {qpath} && printf found || printf missing")
    if exists != "found":
        target = host or "local container"
        out["model_probe_error"] = f"Model path is not visible on {target}: {path}"
        return out
    raw_config = _run_model_probe(host, ssh_port, f"test -f {qconfig} && sed -n '1,240p' {qconfig}")
    if raw_config:
        try:
            cfg = json.loads(raw_config)
        except Exception:
            cfg = {}
        for key in ("context_length", "max_position_embeddings", "n_ctx_train", "model_max_length", "max_seq_len"):
            value = cfg.get(key)
            if isinstance(value, (int, float)) and value > 0:
                out["model_ctx_max"] = int(value)
                break
        out.update(_extract_arch_from_config(cfg))

    if not host:
        cand_gguf = path if os.path.isfile(path) and path.lower().endswith(".gguf") else ""
        if not cand_gguf and os.path.isdir(path):
            for root, _dirs, fns in os.walk(path):
                for fn in sorted(fns):
                    if fn.lower().endswith(".gguf") and "mmproj" not in fn.lower():
                        cand_gguf = os.path.join(root, fn)
                        break
                if cand_gguf:
                    break
        if cand_gguf:
            gguf_meta = _parse_gguf_kv_header(cand_gguf)
            for k, v in gguf_meta.items():
                out.setdefault(k, v)
    else:
        remote_script = (
            "import glob, json, os, struct, sys\n"
            "p = os.path.expanduser(sys.argv[1])\n"
            "cand = p if os.path.isfile(p) else next(iter(sorted(g for g in glob.glob(os.path.join(p, '**', '*.gguf'), recursive=True) if 'mmproj' not in os.path.basename(g).lower())), '')\n"
            "f = open(cand, 'rb') if cand else None\n"
            "assert f and f.read(4) == b'GGUF'\n"
            "ver = struct.unpack('<I', f.read(4))[0]\n"
            "_, n_kv = struct.unpack('<QQ', f.read(16))\n"
            "ss = {0:1,1:1,2:2,3:2,4:4,5:4,6:4,7:1,10:8,11:8,12:8}\n"
            "sf = {0:'<B',1:'<b',2:'<H',3:'<h',4:'<I',5:'<i',6:'<f',7:'<?',10:'<Q',11:'<q',12:'<d'}\n"
            "ws = {'.block_count':'num_hidden_layers','.attention.head_count_kv':'num_key_value_heads','.attention.head_count':'num_attention_heads','.attention.key_length':'head_dim','.embedding_length':'hidden_size','.context_length':'model_ctx_max'}\n"
            "out = {}\n"
            "def skip(t):\n"
            "    if t in ss: f.seek(ss[t], 1)\n"
            "    elif t == 8: f.seek(struct.unpack('<Q', f.read(8))[0], 1)\n"
            "    elif t == 9:\n"
            "        at, al = struct.unpack('<IQ', f.read(12))\n"
            "        if at in ss: f.seek(ss[at] * al, 1)\n"
            "        else:\n"
            "            for _ in range(al): skip(at)\n"
            "for _ in range(min(int(n_kv), 2048)):\n"
            "    k = f.read(struct.unpack('<Q', f.read(8))[0]).decode('utf-8', 'replace')\n"
            "    vt = struct.unpack('<I', f.read(4))[0]\n"
            "    m = next((d for s, d in ws.items() if k.endswith(s)), None)\n"
            "    if m and vt in sf:\n"
            "        v = struct.unpack(sf[vt], f.read(ss[vt]))[0]\n"
            "        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0: out[m] = int(v)\n"
            "    else:\n"
            "        skip(vt)\n"
            "print(json.dumps(out))\n"
        )
        remote_gguf_cmd = f"python3 -c {shlex.quote(remote_script)} {qpath}"
        raw_gguf = _run_model_probe(host, ssh_port, remote_gguf_cmd)
        if raw_gguf:
            try:
                for k, v in json.loads(raw_gguf).items():
                    out.setdefault(k, v)
            except Exception:
                pass

    if not out and not raw_config:
        out["model_probe_error"] = f"config.json not found in model path: {path}"

    size_cmd = (
        f"find {qpath} -type f \\( -name '*.safetensors' -o -name '*.bin' -o -name '*.gguf' \\) "
        "-printf '%s\\n' 2>/dev/null | awk '{s+=$1} END {if (s>0) printf \"%.6f\", s/1073741824}'"
    )
    weights = _run_model_probe(host, ssh_port, size_cmd)
    try:
        weights_gb = float(weights)
    except Exception:
        weights_gb = 0.0
    if weights_gb > 0:
        out["model_weights_gb"] = round(weights_gb, 3)
    elif "model_probe_error" not in out:
        out["model_probe_error"] = f"No model weight files found in: {path}"
    return out

def setup_hwfit_routes():
    # Admin-gated like the Cookbook routes this backs (/api/cookbook/gpus does
    # the same SSH host probing): `host`/`ssh_port` make the server SSH out with
    # its own key to any syntactically valid host, and `model_path` probes the
    # host filesystem. Single-user mode and internal tool calls still pass.
    router = APIRouter(prefix="/api/hwfit", tags=["hwfit"], dependencies=[Depends(require_admin)])

    @router.get("/system")
    def get_system(host: str = "", ssh_port: str = "", platform: str = "", fresh: bool = False):
        """Detect and return current system hardware info. Pass host=user@server for remote.
        fresh=true bypasses the per-host cache (the Rescan button)."""
        from services.hwfit.hardware import detect_system
        host, ssh_port = _validate_detection_target(host, ssh_port)
        return detect_system(host=host, ssh_port=ssh_port, platform=platform, fresh=fresh)

    @router.get("/models")
    def get_models(use_case: str = "", sort: str = "newest", limit: int = 50, search: str = "", host: str = "", quant: str = "", ctx: str = "", gpu_count: str = "", gpu_group: str = "", ssh_port: str = "", platform: str = "", fresh: bool = False, refresh_catalog: bool = False, manual_mode: str = "", manual_gpu_count: str = "", manual_vram_gb: str = "", manual_ram_gb: str = "", manual_backend: str = "", ignore_detected_gpu: bool = False, ignore_detected_ram: bool = False, fit_only: bool = False, official_only: bool = False):
        """Rank LLM models against detected hardware and return scored results.
        gpu_count: override GPU count (0 = CPU only, 1-N = simulate N GPUs of the
            active group). gpu_group: index into system.gpu_groups (the homogeneous
            pools) to target — empty/auto = the largest pool. vLLM can only
            tensor-parallel across identical GPUs, so we never mix pools.
        fresh=true bypasses the hardware-detection cache."""
        from services.hwfit.hardware import detect_system
        from services.hwfit.fit import rank_models
        from services.hwfit.models import get_models, refresh_dynamic_catalogs
        host, ssh_port = _validate_detection_target(host, ssh_port)
        system = deepcopy(detect_system(host=host, ssh_port=ssh_port, platform=platform, fresh=fresh))
        if system.get("error"):
            return {"system": system, "models": [], "error": system["error"]}
        catalog_refresh = None
        if refresh_catalog:
            try:
                catalog_refresh = refresh_dynamic_catalogs(force=True)
            except Exception as e:
                catalog_refresh = {"error": str(e)}
        if not get_models():
            return {
                "system": system,
                "models": [],
                "error": "Model catalog is empty. Use Rescan in Cookbook while online to populate runtime model data; offline recommendations require a previously populated cache.",
                **({"catalog_refresh": catalog_refresh} if catalog_refresh is not None else {}),
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

        system = _apply_manual_hardware(system, manual_mode, manual_gpu_count, manual_vram_gb, manual_ram_gb, manual_backend)

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

        rank_kwargs = {
            "use_case": use_case or None,
            "limit": limit,
            "search": search or None,
            "sort": sort,
            "quant": quant or None,
            "fit_only": fit_only,
        }
        if target_context is not None:
            rank_kwargs["target_context"] = target_context
        try:
            import inspect
            supported = set(inspect.signature(rank_models).parameters)
            rank_kwargs = {k: v for k, v in rank_kwargs.items() if k in supported}
        except Exception:
            rank_kwargs.pop("target_context", None)
            rank_kwargs.pop("fit_only", None)
        results = rank_models(system, **rank_kwargs)
        if official_only:
            results = [m for m in results if _is_official_model(m)]
        payload = {"system": system, "models": results}
        if catalog_refresh is not None:
            payload["catalog_refresh"] = catalog_refresh
        return payload

    @router.get("/profiles")
    def get_serve_profiles(model: str = "", model_path: str = "", host: str = "", ssh_port: str = "", platform: str = "", fresh: bool = False, serve_weights_gb: float = 0.0, serve_quant: str = ""):
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
            for suffix in ("-gguf", "_gguf", ".gguf", "gguf"):
                if s.endswith(suffix):
                    s = s[: -len(suffix)]
                    break
            cut_at = None
            for idx, ch in enumerate(s):
                if ch not in "-_." or idx + 1 >= len(s):
                    continue
                suffix = s[idx + 1:]
                if (
                    suffix in {"fp8", "bf16", "f16"}
                    or suffix.startswith(("awq", "gptq", "iq"))
                    or (suffix.startswith("q") and len(suffix) > 1 and suffix[1].isdigit())
                ):
                    cut_at = idx
            if cut_at is not None:
                s = s[:cut_at]
            return s

        m = catalog.get(model)
        if m is None and model:
            want = _norm(model)
            for name, entry in catalog.items():
                nn = _norm(name)
                if nn and (nn == want or want.endswith(nn) or nn.endswith(want)):
                    m = entry
                    break
        from services.hwfit.models import kv_gb_per_token
        path_meta = _inspect_model_path(model_path or model, host=host, ssh_port=ssh_port)
        arch_keys = (
            "num_hidden_layers", "num_key_value_heads", "num_attention_heads",
            "head_dim", "hidden_size", "kv_lora_rank", "qk_rope_head_dim",
        )
        path_arch = {k: path_meta[k] for k in arch_keys if path_meta.get(k)}
        if m is None and not path_arch and not path_meta.get("model_weights_gb") and not serve_weights_gb:
            return {
                "system": system,
                "profiles": [],
                "error": "model not in catalog",
                "model_ctx_max": int(path_meta.get("model_ctx_max") or 0),
                "model_weights_gb": float(path_meta.get("model_weights_gb") or 0),
                "model_probe_error": path_meta.get("model_probe_error") or "",
            }
        effective_model = {**(m or {"name": model or "custom"}), **path_arch}
        if path_meta.get("model_ctx_max") and not effective_model.get("context_length"):
            effective_model["context_length"] = int(path_meta["model_ctx_max"])
        model_ctx_max = 0
        for k in ("context_length", "max_position_embeddings", "n_ctx_train", "context"):
            v = effective_model.get(k)
            if isinstance(v, (int, float)) and v > 0:
                model_ctx_max = int(v)
                break
        path_ctx_max = int(path_meta.get("model_ctx_max") or 0)
        if path_ctx_max > 0:
            model_ctx_max = max(model_ctx_max, path_ctx_max)
        model_weights_gb = float(path_meta.get("model_weights_gb") or 0)
        if model_weights_gb <= 0:
            for k in ("min_vram_gb", "required_gb", "size_gb", "recommended_ram_gb", "min_ram_gb"):
                v = effective_model.get(k)
                if isinstance(v, (int, float)) and v > 0:
                    model_weights_gb = float(v)
                    break
        effective_weights = serve_weights_gb or (model_weights_gb if m is None else None)
        merged_arch = {k: effective_model[k] for k in arch_keys if effective_model.get(k)}
        return {
            "system": system,
            "profiles": compute_serve_profiles(
                system, effective_model,
                serve_weights_gb=(effective_weights or None),
                serve_quant=(serve_quant or None),
            ),
            "model_ctx_max": model_ctx_max,
            "model_weights_gb": model_weights_gb,
            "kv_gb_per_token_f16": kv_gb_per_token(effective_model, cache_type="f16") if merged_arch else 0.0,
            "arch": merged_arch,
            "model_probe_error": path_meta.get("model_probe_error") or "",
        }

    @router.get("/image-models")
    def get_image_models(sort: str = "fit", search: str = "", host: str = "", gpu_count: str = "", ssh_port: str = "", platform: str = "", fresh: bool = False, manual_mode: str = "", manual_gpu_count: str = "", manual_vram_gb: str = "", manual_ram_gb: str = "", manual_backend: str = "", ignore_detected_gpu: bool = False, ignore_detected_ram: bool = False, official_only: bool = False):
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
        try:
            requested_gpu_count = int(gpu_count) if gpu_count != "" else None
        except ValueError:
            requested_gpu_count = None
        if requested_gpu_count == 0:
            # Respect the UI's RAM toggle. Before this route always rewrote the
            # system to best-single-GPU VRAM, so image rows never changed when
            # switching RAM/GPU.
            system["has_gpu"] = False
            system["gpu_vram_gb"] = 0
            system["gpu_count"] = 0
            system["gpu_only"] = False
        else:
            # Image diffusion backends generally use one device per pipeline,
            # so rank GPU mode against the best single GPU rather than total
            # multi-GPU VRAM.
            gpu_vrams = [float(g.get("vram_gb") or 0) for g in (system.get("gpus") or []) if isinstance(g, dict)]
            single_vram = max(gpu_vrams) if gpu_vrams else ((system.get("gpu_vram_gb") or 0) / max(system.get("gpu_count") or 1, 1))
            system["gpu_vram_gb"] = single_vram
            system["gpu_count"] = 1 if single_vram > 0 else 0
            system["gpu_only"] = True if single_vram > 0 else False
        results = rank_image_models(system, search=search or None, sort=sort)
        if official_only:
            results = [m for m in results if _is_official_model(m)]
        return {"system": system, "models": results}

    return router
