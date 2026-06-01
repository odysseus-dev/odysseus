"""LlamaManager — provision binary, download GGUF, serve, track lifecycle.

Design goals (zero-degradation on a bare Windows box):
  * No C++ toolchain required. We download a PREBUILT llama-server from the
    ggml-org/llama.cpp GitHub releases (CUDA build on NVIDIA, CPU build
    otherwise). Extracted into data/llama/bin/.
  * GGUF models downloaded via huggingface_hub (already a dependency).
  * Servers launched as background processes, GPU-offloaded (--n-gpu-layers),
    each on its own port, health-checked via the OpenAI-compatible /v1/models.
  * Cross-platform: the same code path works on Windows, Linux, macOS — only
    the release asset name and exe extension differ.

State is intentionally process-local (a dict of running servers) plus on-disk
GGUF files; nothing here needs the DB. Endpoint registration into the chat
picker is done by the route layer using the existing ModelEndpoint model.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"
EXE = ".exe" if IS_WINDOWS else ""

# Pinned llama.cpp release. Prebuilt Windows binaries exist for this tag; we pin
# so behaviour is reproducible and we don't chase a moving 'latest'.
LLAMA_RELEASE_TAG = "b9444"
_GH_REL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_RELEASE_TAG}"

# Asset names per platform/backend (verified against the release).
# CUDA build needs the matching cudart runtime extracted alongside it.
_WIN_CUDA_MAIN = f"llama-{LLAMA_RELEASE_TAG}-bin-win-cuda-13.3-x64.zip"
_WIN_CUDA_RUNTIME = "cudart-llama-bin-win-cuda-13.3-x64.zip"
_WIN_CPU = f"llama-{LLAMA_RELEASE_TAG}-bin-win-cpu-x64.zip"


def _data_dir() -> Path:
    # Mirror the app's data/ root (BASE_DIR/data). manager.py lives at
    # <root>/services/llama/manager.py, so root is parents[2].
    root = Path(__file__).resolve().parents[2]
    d = root / "data" / "llama"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _free_port(start: int = 8090, end: int = 8190) -> int:
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free port for llama-server in range")


def _has_nvidia() -> bool:
    return shutil.which("nvidia-smi") is not None


class ServeHandle:
    """A running llama-server instance."""

    def __init__(self, model_id: str, gguf_path: str, port: int,
                 proc: subprocess.Popen, log_path: str, n_gpu_layers: int):
        self.model_id = model_id
        self.gguf_path = gguf_path
        self.port = port
        self.proc = proc
        self.log_path = log_path
        self.n_gpu_layers = n_gpu_layers
        self.started_at = time.time()

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "gguf_path": self.gguf_path,
            "port": self.port,
            "pid": self.proc.pid,
            "base_url": self.base_url,
            "n_gpu_layers": self.n_gpu_layers,
            "alive": self.alive,
            "uptime_s": round(time.time() - self.started_at, 1),
        }


class LlamaManager:
    def __init__(self):
        self._servers: dict[str, ServeHandle] = {}   # model_id -> handle
        self._lock = threading.Lock()
        self._provision_lock = threading.Lock()

    # ---- binary provisioning --------------------------------------------

    @property
    def bin_dir(self) -> Path:
        return _data_dir() / "bin"

    @property
    def models_dir(self) -> Path:
        d = _data_dir() / "models"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def server_path(self) -> Optional[Path]:
        """Return the llama-server path if already provisioned, else None."""
        # 1. Bundled/provisioned copy.
        cand = self.bin_dir / f"llama-server{EXE}"
        if cand.exists():
            return cand
        # 2. On PATH (user installed it themselves).
        which = shutil.which("llama-server")
        if which:
            return Path(which)
        return None

    def backend_kind(self) -> str:
        """'cuda' if we'll use/used a CUDA build, else 'cpu'. Windows-aware."""
        if IS_WINDOWS and _has_nvidia():
            return "cuda"
        return "cpu"

    def _download(self, url: str, dest: Path, progress: Optional[list] = None) -> None:
        logger.info("llama: downloading %s", url)
        req = urllib.request.Request(url, headers={"User-Agent": "odysseus-llama"})
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length", 0))
            read = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                read += len(chunk)
                if progress is not None and total:
                    progress[:] = [round(read / total * 100, 1)]

    def ensure_binary(self, force_cpu: bool = False,
                      progress: Optional[list] = None) -> Path:
        """Ensure a llama-server binary exists; download a prebuilt one if not.

        On non-Windows we only look on PATH (Linux/mac users install via their
        package manager or the build); auto-download targets Windows where the
        prebuilt zips are the pragmatic path.
        """
        existing = self.server_path()
        if existing:
            return existing

        with self._provision_lock:
            existing = self.server_path()
            if existing:
                return existing

            if not IS_WINDOWS:
                raise RuntimeError(
                    "llama-server not found on PATH. On Linux/macOS install "
                    "llama.cpp (e.g. `brew install llama.cpp` or build from "
                    "source) and ensure llama-server is on PATH."
                )

            self.bin_dir.mkdir(parents=True, exist_ok=True)
            use_cuda = (not force_cpu) and _has_nvidia()
            tmp = _data_dir() / "_dl"
            tmp.mkdir(parents=True, exist_ok=True)

            try:
                if use_cuda:
                    # Main CUDA build + matching cudart runtime.
                    main_zip = tmp / _WIN_CUDA_MAIN
                    rt_zip = tmp / _WIN_CUDA_RUNTIME
                    self._download(f"{_GH_REL}/{_WIN_CUDA_MAIN}", main_zip, progress)
                    self._download(f"{_GH_REL}/{_WIN_CUDA_RUNTIME}", rt_zip)
                    self._extract(main_zip, self.bin_dir)
                    self._extract(rt_zip, self.bin_dir)
                else:
                    cpu_zip = tmp / _WIN_CPU
                    self._download(f"{_GH_REL}/{_WIN_CPU}", cpu_zip, progress)
                    self._extract(cpu_zip, self.bin_dir)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

            srv = self.server_path()
            if not srv:
                # Some zips nest binaries in a subfolder; flatten.
                self._flatten_bin()
                srv = self.server_path()
            if not srv:
                raise RuntimeError("llama-server not found after extraction")
            logger.info("llama: provisioned %s (cuda=%s)", srv, use_cuda)
            return srv

    def _extract(self, zip_path: Path, dest: Path) -> None:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(dest)

    def _flatten_bin(self) -> None:
        """Move llama-server + DLLs out of any nested folder into bin_dir."""
        for p in self.bin_dir.rglob(f"llama-server{EXE}"):
            if p.parent != self.bin_dir:
                src_dir = p.parent
                for item in src_dir.iterdir():
                    target = self.bin_dir / item.name
                    if not target.exists():
                        shutil.move(str(item), str(target))
                break

    # ---- model download --------------------------------------------------

    def download_gguf(self, repo_id: str, filename: Optional[str] = None,
                      hf_token: Optional[str] = None) -> str:
        """Download a single GGUF file from a HF repo into models_dir.

        If filename is None, pick the smallest .gguf in the repo (a sane default
        for a quick local model). Returns the local path.
        """
        from huggingface_hub import hf_hub_download, list_repo_files

        token = hf_token or os.getenv("HF_TOKEN") or None
        if not filename:
            files = list_repo_files(repo_id, token=token)
            ggufs = [f for f in files if f.lower().endswith(".gguf")]
            if not ggufs:
                raise RuntimeError(f"No .gguf files in {repo_id}")
            # Prefer a Q4_K_M if present (good size/quality), else first.
            pick = next((f for f in ggufs if "q4_k_m" in f.lower()), ggufs[0])
            filename = pick

        local = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=str(self.models_dir / repo_id.replace("/", "__")),
            token=token,
        )
        return local

    def list_local_models(self) -> list[dict]:
        """Scan models_dir for downloaded GGUF files."""
        out = []
        for p in self.models_dir.rglob("*.gguf"):
            out.append({
                "path": str(p),
                "name": p.name,
                "size_mb": round(p.stat().st_size / (1024 * 1024), 1),
                "repo": p.parent.name.replace("__", "/"),
            })
        return out

    # ---- serve lifecycle -------------------------------------------------

    def serve(self, gguf_path: str, model_id: Optional[str] = None,
              n_gpu_layers: Optional[int] = None, ctx_size: int = 4096) -> ServeHandle:
        """Launch llama-server for a GGUF file. Returns a ServeHandle.

        n_gpu_layers: None -> auto (999 = offload all if CUDA, 0 if CPU build).
        """
        gguf = Path(gguf_path)
        if not gguf.exists():
            raise RuntimeError(f"GGUF not found: {gguf_path}")

        model_id = model_id or gguf.stem
        with self._lock:
            if model_id in self._servers and self._servers[model_id].alive:
                return self._servers[model_id]

        srv = self.ensure_binary()
        port = _free_port()
        if n_gpu_layers is None:
            n_gpu_layers = 999 if self.backend_kind() == "cuda" else 0

        log_path = _data_dir() / f"serve_{model_id.replace('/', '_')}.log"
        cmd = [
            str(srv),
            "-m", str(gguf),
            "--host", "127.0.0.1",
            "--port", str(port),
            "-c", str(ctx_size),
            "-ngl", str(n_gpu_layers),
        ]
        logger.info("llama: serving %s on :%d (ngl=%d)", model_id, port, n_gpu_layers)
        log_f = open(log_path, "w", encoding="utf-8")
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
        proc = subprocess.Popen(
            cmd, stdout=log_f, stderr=subprocess.STDOUT,
            cwd=str(self.bin_dir), creationflags=creationflags,
        )
        handle = ServeHandle(model_id, str(gguf), port, proc, str(log_path), n_gpu_layers)
        with self._lock:
            self._servers[model_id] = handle
        return handle

    def wait_until_ready(self, handle: ServeHandle, timeout: float = 120) -> bool:
        """Poll /v1/models until the server answers or dies/times out."""
        deadline = time.time() + timeout
        url = f"http://127.0.0.1:{handle.port}/v1/models"
        while time.time() < deadline:
            if not handle.alive:
                return False
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=2) as r:
                    if r.status == 200:
                        return True
            except Exception:
                pass
            time.sleep(1.0)
        return False

    def health(self, model_id: str) -> dict:
        with self._lock:
            handle = self._servers.get(model_id)
        if not handle:
            return {"model_id": model_id, "alive": False, "found": False}
        d = handle.to_dict()
        d["found"] = True
        # Quick reachability check.
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{handle.port}/v1/models")
            with urllib.request.urlopen(req, timeout=2) as r:
                d["ready"] = r.status == 200
        except Exception:
            d["ready"] = False
        return d

    def list_servers(self) -> list[dict]:
        with self._lock:
            handles = list(self._servers.values())
        return [h.to_dict() for h in handles]

    def stop(self, model_id: str) -> bool:
        with self._lock:
            handle = self._servers.pop(model_id, None)
        if not handle:
            return False
        try:
            handle.proc.terminate()
            try:
                handle.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                handle.proc.kill()
        except Exception:
            pass
        return True

    def stop_all(self) -> None:
        with self._lock:
            ids = list(self._servers.keys())
        for mid in ids:
            self.stop(mid)


_manager: Optional[LlamaManager] = None
_manager_lock = threading.Lock()


def get_llama_manager() -> LlamaManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = LlamaManager()
    return _manager
