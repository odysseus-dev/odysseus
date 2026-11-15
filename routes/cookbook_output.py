"""Pure helpers for shaping cookbook task output for the status response.

Kept dependency-free (no FastAPI / SQLAlchemy imports) so the behavior can be
unit-tested without standing up the whole app.
"""

import re

_FETCHING_ZERO_FILES_RE = re.compile(r"Fetching\s+0\s+files", re.IGNORECASE)

# Shared source embedded in local/remote inventory and status probes. A blank
# retry marker beside its completed, nonempty content-addressed blob is stale;
# a blank marker alone may be a download that has not received its first byte.
HF_PARTIAL_BLOB_HELPER = """def is_partial_blob(path):
    if not path.endswith('.incomplete'):
        return False
    try:
        completed = path[:-len('.incomplete')]
        return (os.path.getsize(path) != 0 or not os.path.isfile(completed)
                or os.path.getsize(completed) == 0)
    except OSError:
        return True
"""

# Probe scripts for the dead-session download check, run as
# `python3 -c <PROBE> <repo_id> <cache_root>` (locally or over SSH).
# cache_root is the task's custom download dir, '' for the default HF cache.
# It has to be passed explicitly: the download runner exports
# HF_HOME=<local_dir>, so that task's cache lives under <local_dir>/hub, and
# the probe process's own environment knows nothing about it.
HF_CACHE_COMPLETE_PROBE = (
    "import os,sys\n" + HF_PARTIAL_BLOB_HELPER +
    "repo=sys.argv[1];"
    "root=os.path.expanduser(sys.argv[2]) if len(sys.argv)>2 and sys.argv[2] else '';"
    "base=os.path.join(root,'hub') if root else (os.environ.get('HUGGINGFACE_HUB_CACHE') or os.path.join(os.environ.get('HF_HOME', os.path.expanduser('~/.cache/huggingface')), 'hub'));"
    "d=os.path.join(base,'models--'+repo.replace('/','--'));"
    "snap=os.path.join(d,'snapshots');"
    "ok=os.path.isdir(snap) and any(os.path.isdir(os.path.join(snap,x)) and os.listdir(os.path.join(snap,x)) for x in os.listdir(snap));"
    "inc=False;"
    "blobs=os.path.join(d,'blobs');"
    "inc=os.path.isdir(blobs) and any(is_partial_blob(os.path.join(blobs,x)) for x in os.listdir(blobs));"
    "sys.exit(0 if ok and not inc else 1)"
)

HF_CACHE_INCOMPLETE_PROBE = (
    "import os,sys\n" + HF_PARTIAL_BLOB_HELPER +
    "repo=sys.argv[1];"
    "root=os.path.expanduser(sys.argv[2]) if len(sys.argv)>2 and sys.argv[2] else '';"
    "base=os.path.join(root,'hub') if root else (os.environ.get('HUGGINGFACE_HUB_CACHE') or os.path.join(os.environ.get('HF_HOME', os.path.expanduser('~/.cache/huggingface')), 'hub'));"
    "d=os.path.join(base,'models--'+repo.replace('/','--'));"
    "blobs=os.path.join(d,'blobs');"
    "inc=os.path.isdir(blobs) and any(is_partial_blob(os.path.join(blobs,x)) for x in os.listdir(blobs));"
    "sys.exit(0 if inc else 1)"
)


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


_PREFLIGHT_FAIL_RE = re.compile(
    r"ODYSSEUS_PREFLIGHT_EXIT=|ERROR:\s+.+\s+is not installed|command not found",
    re.IGNORECASE,
)


def diagnose_task_output(
    task_type: str,
    full_snapshot: str,
    *,
    status: str = "error",
    exit_code: int | None = None,
    zero_files: bool = False,
    cmd: str = "",
    log_path: str = "",
    base_diagnosis: dict | None = None,
) -> dict | None:
    """Build actionable diagnostics (message, category, suggestions, cmd, log_path, output_tail)
    for failed downloads, dependency installs, preflight checks, and serving jobs.
    """
    t = (task_type or "serve").strip().lower()
    text = full_snapshot or ""
    tail = text[-6000:]
    is_preflight = t == "preflight" or bool(_PREFLIGHT_FAIL_RE.search(tail))
    category = "preflight" if is_preflight else t

    diag: dict | None = dict(base_diagnosis) if isinstance(base_diagnosis, dict) else None

    if diag is None:
        if t == "download":
            if zero_files or _FETCHING_ZERO_FILES_RE.search(tail):
                diag = {
                    "message": (
                        "No matching files were downloaded. The model repo or filename/quant pattern "
                        "may be wrong (for example a ':Q4_K_M' tag that does not exist in the repo)."
                    ),
                    "suggestions": [
                        {"label": "verify the HuggingFace repo ID and quantization/include pattern", "op": "manual"},
                        {"label": "list available files in the repository before retrying", "op": "manual"},
                    ],
                }
            elif re.search(r"401|403|GatedRepoError|RepositoryNotFoundError|Invalid user token|Access to model .* is restricted", tail, re.I):
                diag = {
                    "message": "HuggingFace authentication or gated repository access failed.",
                    "suggestions": [
                        {"label": "configure a valid HF_TOKEN in Cookbook Settings", "op": "settings"},
                        {"label": "accept the model license agreement on HuggingFace and retry", "op": "manual"},
                    ],
                }
            elif re.search(r"No space left on device|ENOSPC|Disk quota exceeded", tail, re.I):
                diag = {
                    "message": "Insufficient disk space in the target download directory.",
                    "suggestions": [
                        {"label": "free disk space or choose a drive with more capacity in Cookbook Settings", "op": "settings"},
                    ],
                }
            elif re.search(r"ReadTimeout|ConnectTimeout|ConnectionError|MaxRetryError|502 Bad Gateway|503 Service Unavailable|504 Gateway", tail, re.I):
                diag = {
                    "message": "Download interrupted by a network or HuggingFace Hub timeout.",
                    "suggestions": [
                        {"label": "retry the download (HuggingFace Hub resumes completed blobs automatically)", "op": "retry"},
                    ],
                }
        elif t in {"dependency", "install"}:
            if re.search(r"Python\.h: No such file|gcc: error|clang: error|cmake: command not found|ninja: build stopped|Failed building wheel", tail, re.I):
                diag = {
                    "message": "Native C/C++ extension build failed while installing dependency.",
                    "suggestions": [
                        {"label": "install OS build tools: build-essential cmake ninja-build python3-dev", "op": "manual"},
                        {"label": "retry using prebuilt binary wheels for your CUDA/Python version", "op": "retry"},
                    ],
                }
            elif re.search(r"externally-managed-environment|PermissionError|Permission denied", tail, re.I):
                diag = {
                    "message": "Package installation blocked by environment permissions or PEP 668 externally-managed Python.",
                    "suggestions": [
                        {"label": "install into the managed Odysseus virtual environment rather than system Python", "op": "manual"},
                    ],
                }
            elif re.search(r"No matching distribution found|ResolutionImpossible|Could not find a version that satisfies", tail, re.I):
                diag = {
                    "message": "Package dependency resolution failed for the current Python or CUDA platform.",
                    "suggestions": [
                        {"label": "check Python version and CUDA/ROCm wheel index compatibility", "op": "manual"},
                    ],
                }

    failed = status == "error" or (exit_code is not None and exit_code != 0) or zero_files
    if diag is None and failed:
        if is_preflight:
            diag = {
                "message": "Runtime preflight check failed before launching the job.",
                "suggestions": [
                    {"label": "install or repair the missing runtime in Cookbook Dependencies", "op": "dependency"},
                    {"label": "inspect the trailing command output below for the missing binary or library", "op": "manual"},
                ],
            }
        else:
            code_note = f" (exit code {exit_code})" if exit_code not in (None, 0) else ""
            diag = {
                "message": f"{t.capitalize()} job failed{code_note}. Review the command output and logs below.",
                "suggestions": [
                    {"label": "inspect the last 50 lines of command output for the root exception", "op": "manual"},
                    {"label": "retry the job after correcting configuration or environment issues", "op": "retry"},
                ],
            }

    if diag is None:
        return None

    diag.setdefault("suggestions", [])
    diag["category"] = category
    if exit_code is not None:
        diag["exit_code"] = exit_code
    if cmd:
        diag["cmd"] = cmd
    if log_path:
        diag["log_path"] = log_path
    diag["output_tail"] = error_aware_output_tail(text, "error")
    return diag
