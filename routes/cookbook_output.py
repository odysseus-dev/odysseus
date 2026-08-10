"""Helpers for shaping cookbook task output for the status response.

Mostly pure; the cache/snapshot probes here shell out (locally or over SSH) and
own the probe scripts they run. Kept dependency-free (stdlib only — no FastAPI /
SQLAlchemy imports) so the behavior can be unit-tested without standing up the
whole app.
"""

import re
import shlex
import subprocess
import time

_FETCHING_ZERO_FILES_RE = re.compile(r"Fetching\s+0\s+files", re.IGNORECASE)

# Probe scripts for the dead-session download check, run as
# `python3 -c <PROBE> <repo_id> <cache_root>` (locally or over SSH).
# cache_root is the task's custom download dir, '' for the default HF cache.
# It has to be passed explicitly: the download runner exports
# HF_HOME=<local_dir>, so that task's cache lives under <local_dir>/hub, and
# the probe process's own environment knows nothing about it.
HF_CACHE_COMPLETE_PROBE = (
    "import os,sys;"
    "repo=sys.argv[1];"
    "root=os.path.expanduser(sys.argv[2]) if len(sys.argv)>2 and sys.argv[2] else '';"
    "base=os.path.join(root,'hub') if root else (os.environ.get('HUGGINGFACE_HUB_CACHE') or os.path.join(os.environ.get('HF_HOME', os.path.expanduser('~/.cache/huggingface')), 'hub'));"
    "d=os.path.join(base,'models--'+repo.replace('/','--'));"
    "snap=os.path.join(d,'snapshots');"
    "ok=os.path.isdir(snap) and any(os.path.isdir(os.path.join(snap,x)) and os.listdir(os.path.join(snap,x)) for x in os.listdir(snap));"
    "inc=False;"
    "blobs=os.path.join(d,'blobs');"
    "inc=os.path.isdir(blobs) and any(x.endswith('.incomplete') for x in os.listdir(blobs));"
    "sys.exit(0 if ok and not inc else 1)"
)

HF_CACHE_INCOMPLETE_PROBE = (
    "import os,sys;"
    "repo=sys.argv[1];"
    "root=os.path.expanduser(sys.argv[2]) if len(sys.argv)>2 and sys.argv[2] else '';"
    "base=os.path.join(root,'hub') if root else (os.environ.get('HUGGINGFACE_HUB_CACHE') or os.path.join(os.environ.get('HF_HOME', os.path.expanduser('~/.cache/huggingface')), 'hub'));"
    "d=os.path.join(base,'models--'+repo.replace('/','--'));"
    "blobs=os.path.join(d,'blobs');"
    "inc=os.path.isdir(blobs) and any(x.endswith('.incomplete') for x in os.listdir(blobs));"
    "sys.exit(0 if inc else 1)"
)

# Lists the newest snapshot's file names, one per line, for the MLX bundle
# check below. Same cache-root contract as the two probes above.
HF_SNAPSHOT_LISTING_PROBE = (
    "import os,sys;"
    "repo=sys.argv[1];"
    "root=os.path.expanduser(sys.argv[2]) if len(sys.argv)>2 and sys.argv[2] else '';"
    "base=os.path.join(root,'hub') if root else (os.environ.get('HUGGINGFACE_HUB_CACHE') or os.path.join(os.environ.get('HF_HOME', os.path.expanduser('~/.cache/huggingface')), 'hub'));"
    "snap=os.path.join(base,'models--'+repo.replace('/','--'),'snapshots');"
    "dirs=[os.path.join(snap,x) for x in os.listdir(snap)] if os.path.isdir(snap) else [];"
    "dirs=[d for d in dirs if os.path.isdir(d)];"
    "sys.exit(1) if not dirs else None;"
    "newest=max(dirs,key=os.path.getmtime);"
    "print('\\n'.join(sorted(os.listdir(newest))))"
)

# An MLX *text* model is a folder bundle, not a single file: config.json is the
# manifest, the weights are safetensors shards, and the tokenizer ships
# alongside. A repo missing any of the three loads fine as far as HuggingFace
# is concerned and then fails deep inside mlx-lm.
#
# Weights can also arrive as a *.npz archive (the older mlx-examples container),
# so "no *.safetensors" is not on its own a missing-weights verdict.
_MLX_WEIGHT_SUFFIXES = (".safetensors", ".npz")
_MLX_TOKENIZER_FILES = {"tokenizer.json", "tokenizer_config.json", "tokenizer.model"}

_MLX_BUNDLE_REQUIREMENTS = (
    ("config.json", lambda names: "config.json" in names),
    (
        "a weights file (*.safetensors or *.npz)",
        lambda names: any(n.endswith(_MLX_WEIGHT_SUFFIXES) for n in names),
    ),
    (
        "a tokenizer (tokenizer.json / tokenizer_config.json)",
        lambda names: bool(names & _MLX_TOKENIZER_FILES),
    ),
)

# A diffusers-style pipeline (image, audio) is a manifest plus per-component
# SUBDIRECTORIES — its weights and tokenizer live one level down, so none of the
# top-level requirements above apply to it.
_MLX_PIPELINE_MANIFESTS = {"model_index.json", "pipeline.json"}
_MLX_PIPELINE_COMPONENT_DIRS = {
    "tokenizer", "tokenizer_2", "tokenizer_3",
    "text_encoder", "text_encoder_2", "text_encoder_3",
    "transformer", "unet", "vae", "vae_decoder", "vae_encoder",
    "scheduler", "image_encoder", "feature_extractor", "safety_checker",
}

# Files that only ever ship with a HuggingFace *text* model — the one shape
# mlx-lm loads. Requiring one of these is what lets the check say "this is an
# MLX LLM bundle and it is broken" instead of guessing at a layout it doesn't
# understand: mlx-community also hosts whisper builds (config.json + a
# weights.npz, no tokenizer at all) and mflux/diffusers image bundles, and both
# of those are complete and loadable exactly as they are.
_MLX_LLM_MARKERS = {
    "tokenizer.json", "tokenizer_config.json", "tokenizer.model",
    "special_tokens_map.json", "added_tokens.json", "vocab.json", "merges.txt",
    "chat_template.jinja", "chat_template.json", "generation_config.json",
    "model.safetensors.index.json",
}


_MLX_REPO_RE = re.compile(r"^mlx-community/|\bmlx\b|[-_]mlx(?:[-_]|$)", re.IGNORECASE)


def looks_like_mlx_repo(repo_id: str) -> bool:
    """Whether a downloaded repo id is an MLX build, so the bundle check below
    stays scoped to MLX and never fires on GGUF/safetensors downloads."""
    return bool(repo_id) and bool(_MLX_REPO_RE.search(repo_id))


def _listing_names(names) -> set[str]:
    """Snapshot listing (files AND directories) as a set of bare names."""
    return {str(n).strip() for n in (names or []) if str(n).strip()}


def looks_like_mlx_llm_bundle(names) -> bool:
    """Whether a snapshot listing is positively an MLX *text* LLM bundle.

    The requirement check below is only meaningful for the transformers-style
    text bundle mlx-lm loads, so it only ever runs on a listing that carries a
    text-model marker and isn't a diffusers-style pipeline. Everything else
    under mlx-community — whisper, image pipelines, bare adapters, layouts we
    have never seen — is left alone rather than declared broken.
    """
    have = _listing_names(names)
    if not have:
        return False
    if have & _MLX_PIPELINE_MANIFESTS:
        return False
    if have & _MLX_PIPELINE_COMPONENT_DIRS:
        return False
    return bool(have & _MLX_LLM_MARKERS)


def missing_mlx_bundle_files(names) -> list[str]:
    """Which required MLX bundle members are absent from a downloaded snapshot.

    `names` is the snapshot directory listing (bare file and directory names).
    Returns [] for a complete bundle, for an empty listing ("no files at all" is
    already reported by the download markers), and for any listing that isn't
    positively an MLX LLM text bundle.
    """
    have = _listing_names(names)
    if not looks_like_mlx_llm_bundle(have):
        return []
    return [label for label, present in _MLX_BUNDLE_REQUIREMENTS if not present(have)]


def mlx_bundle_diagnosis(repo_id: str, names) -> dict | None:
    """Post-download shape check for an MLX repo, phrased as a serve diagnosis."""
    missing = missing_mlx_bundle_files(names)
    if not missing:
        return None
    return {
        "message": (
            f"{repo_id} downloaded but is not a loadable MLX bundle — missing "
            f"{', '.join(missing)}. MLX models are folder bundles with config.json "
            f"as the manifest; re-download the repo or pick an mlx-community build."
        ),
        "suggestions": [{"label": "re-download this model (or choose an mlx-community build)", "op": "manual"}],
    }


# Memoized MLX post-download bundle checks, keyed by
# (download session id, repo_id, host, cache root). The status endpoint polls
# every few seconds and a finished download's snapshot doesn't change, so probe
# it once — but key on the download SESSION, which is generated fresh per
# download, so re-downloading a repo that was reported broken re-probes instead
# of replaying the stale gap until the server restarts.
#
# Values are (expires_at, gap). A successful probe never expires (the snapshot
# it read is final for that session); a probe that could not read the snapshot
# expires after _MLX_BUNDLE_PROBE_RETRY_S, so a completed download on an
# unreachable remote host doesn't re-run a 12s SSH subprocess on every poll but
# still recovers once the host comes back.
_MLX_BUNDLE_CHECK_MAX = 64
_MLX_BUNDLE_PROBE_RETRY_S = 300.0
_mlx_bundle_checks: dict[tuple[str, str, str, str], tuple[float, dict | None]] = {}


def _remember_mlx_bundle_check(key: tuple, gap: dict | None, ttl: float | None = None) -> None:
    """Memoize one bundle-check result, keeping the memo bounded.

    ttl=None means "never re-probe for this session"; a float means retry after
    that many seconds. Re-inserting moves the key to the end, so the eviction
    below drops the least recently written entry.
    """
    _mlx_bundle_checks.pop(key, None)
    _mlx_bundle_checks[key] = (float("inf") if ttl is None else time.monotonic() + ttl, gap)
    while len(_mlx_bundle_checks) > _MLX_BUNDLE_CHECK_MAX:
        _mlx_bundle_checks.pop(next(iter(_mlx_bundle_checks)))


def mlx_bundle_gap(
    session_id: str,
    repo_id: str,
    remote_host: str = "",
    ssh_port: str = "",
    cache_root: str = "",
) -> dict | None:
    """Diagnose a finished MLX download whose bundle isn't loadable.

    An MLX repo can complete cleanly and still be missing config.json, its
    weights, or its tokenizer — mlx-lm then fails at load time with an opaque
    error. Only a listing that is positively an MLX LLM text bundle is ever
    reported (see looks_like_mlx_llm_bundle); whisper- and diffusers-shaped MLX
    repos pass through untouched.
    """
    if not repo_id or "/" not in repo_id or not looks_like_mlx_repo(repo_id):
        return None
    key = (session_id or "", repo_id, remote_host or "", cache_root or "")
    cached = _mlx_bundle_checks.get(key)
    if cached is not None and time.monotonic() < cached[0]:
        return cached[1]
    cmd = ["python3", "-c", HF_SNAPSHOT_LISTING_PROBE, repo_id, cache_root or ""]
    try:
        if remote_host:
            ssh_base = ["ssh"]
            if ssh_port and ssh_port != "22":
                ssh_base.extend(["-p", str(ssh_port)])
            shell_cmd = " ".join(shlex.quote(x) for x in cmd)
            proc = subprocess.run(ssh_base + [remote_host, shell_cmd], timeout=12, capture_output=True, text=True)
        else:
            proc = subprocess.run(cmd, timeout=12, capture_output=True, text=True)
        if proc.returncode != 0:
            # Snapshot unreadable (host down, cache moved, no python3 there).
            # Don't guess — but don't pay for the probe on every poll either.
            _remember_mlx_bundle_check(key, None, ttl=_MLX_BUNDLE_PROBE_RETRY_S)
            return None
        gap = mlx_bundle_diagnosis(repo_id, (proc.stdout or "").splitlines())
    except Exception:
        _remember_mlx_bundle_check(key, None, ttl=_MLX_BUNDLE_PROBE_RETRY_S)
        return None
    _remember_mlx_bundle_check(key, gap)
    return gap


def classify_dead_download(full_snapshot: str):
    """Resolve a dead download session's status from its runner markers.

    The runner prints DOWNLOAD_OK only after exiting 0 (and DOWNLOAD_FAILED
    otherwise), so the markers stay trustworthy after the tmux pane is gone.
    Returns (status, zero_files), or None when the snapshot carries no marker
    and the caller has to fall back to the cache probe. Same precedence as
    the live-session branch: DOWNLOAD_OK wins, except a "Fetching 0 files"
    run is an error (nothing matched the include/quant pattern).
    """
    if not full_snapshot:
        return None
    if "DOWNLOAD_OK" in full_snapshot:
        if _FETCHING_ZERO_FILES_RE.search(full_snapshot):
            return ("error", True)
        return ("completed", False)
    if "DOWNLOAD_FAILED" in full_snapshot:
        return ("error", False)
    return None


def error_aware_output_tail(full_snapshot: str, status: str) -> str:
    """Return the trailing slice of a task log for the status response.

    Failed tasks return the last 50 lines so the "Copy last 50 lines" action
    surfaces the actual error context (stack traces, build output). Running and
    other non-error tasks keep the cheaper 12-line tail to limit the payload on
    the 10s polling interval.
    """
    if not full_snapshot:
        return ""
    tail_lines = 50 if status == "error" else 12
    return "\n".join(full_snapshot.splitlines()[-tail_lines:])
