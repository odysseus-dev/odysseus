# src/runtime_installer.py
"""Find, install, start and feed the Ollama local model runtime.

All functions block; callers run them in a thread. Every RuntimeInstallError
message is plain language, safe to show to a non-technical user.
"""
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
# Observed chain: ollama.com -> github.com release redirect -> GitHub release asset host.
ALLOWED_HOSTS = frozenset({"ollama.com", "github.com", "release-assets.githubusercontent.com"})
# Organization in the Authenticode subject of official Ollama builds.
PUBLISHER_ORG = "Ollama Inc."
REGISTRY = "https://registry.ollama.ai/v2/library"
DEFAULT_PORT = 11434
INSTALL_TIMEOUT_S = 600
START_TIMEOUT_S = 30

# Local model runtimes setup knows. Keys match what ModelDiscovery can report, plus "ollama".
# install_on: sys.platform values on which setup installs the runtime by itself.
RUNTIMES = {
    "ollama": {"name": "Ollama", "install_on": ("win32",), "download_page": "https://ollama.com/download"},
    "lmstudio": {"name": "LM Studio", "install_on": (), "download_page": "https://lmstudio.ai/download"},
    "llamacpp": {"name": "llama.cpp", "install_on": (), "download_page": "https://github.com/ggml-org/llama.cpp/releases"},
}


def runtime_name(provider: str | None) -> str | None:
    return RUNTIMES.get(provider or "", {}).get("name")


# (min VRAM GB, min RAM GB, tag, fallback size, license). A row matches on VRAM or RAM.
# All are official library tags with tool-calling support, Apache 2.0 per the model license layer.
_MODELS = (
    (8, 24, "qwen3:8b", 5225388164, "Apache 2.0"),
    (4, 8, "qwen3:4b", 2497293931, "Apache 2.0"),
    (0, 0, "qwen3:1.7b", 1359293444, "Apache 2.0"),
)


class RuntimeInstallError(Exception):
    """str(e) is a plain-language message safe to show to a non-technical user."""


def _platform() -> str:
    return sys.platform


def _is_windows() -> bool:
    return _platform() == "win32"


def _client() -> httpx.Client:
    return httpx.Client(follow_redirects=False, timeout=httpx.Timeout(15.0, read=60.0))


def model_dir() -> str:
    """Where Ollama keeps model files: OLLAMA_MODELS when set, else .ollama/models in the home folder."""
    return os.environ.get("OLLAMA_MODELS", "").strip() or os.path.join(os.path.expanduser("~"), ".ollama", "models")


def find_ollama() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    if _is_windows():
        base = os.environ.get("LOCALAPPDATA")
        cands = [Path(base) / "Programs" / "Ollama" / "ollama.exe"] if base else []
    elif sys.platform == "darwin":
        cands = [Path("/Applications/Ollama.app/Contents/Resources/ollama"), Path("/usr/local/bin/ollama")]
    else:
        cands = [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]
    return next((str(p) for p in cands if p.is_file()), None)


# ---- Windows installer download ------------------------------------------------

def _check_host(url: str) -> str:
    u = urlparse(url)
    if u.scheme != "https" or (u.hostname or "") not in ALLOWED_HOSTS:
        raise RuntimeInstallError(
            "The download link pointed somewhere unexpected, so it was stopped for safety. "
            "Please try again later or install Ollama from ollama.com yourself."
        )
    return url


def _resolve(client: httpx.Client) -> tuple[str, int]:
    """Follow redirects by hand, checking every hop. Returns (final url, total bytes)."""
    url = INSTALLER_URL
    try:
        for _ in range(6):
            r = client.get(_check_host(url), headers={"Range": "bytes=0-0"})
            if r.is_redirect:
                url = str(r.url.join(r.headers["location"]))
                continue
            if r.status_code == 206:
                total = int(r.headers["content-range"].rsplit("/", 1)[1])
            elif r.status_code == 200:
                total = int(r.headers["content-length"])
            else:
                break
            return url, total
    except (httpx.HTTPError, KeyError, ValueError):
        pass
    raise RuntimeInstallError(
        "Could not reach ollama.com to get the installer. Check your internet connection and try again."
    )


def ollama_install_offer() -> dict | None:
    entry = RUNTIMES["ollama"]
    if _platform() not in entry["install_on"]:
        return None
    with _client() as c:
        _, total = _resolve(c)
    return {
        "name": entry["name"],
        "download_page": entry["download_page"],
        "source_url": INSTALLER_URL,
        "size_bytes": total,
        "license": "MIT (Ollama)",
        "cost": "Free. Runs on this computer, no account needed.",
    }


def _cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    d = Path(base) / "Odysseus" / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download(client: httpx.Client, url: str, total: int, part: Path, progress) -> None:
    """Resumable download into `part` (HTTP Range); the partial file is kept on failure."""
    have = part.stat().st_size if part.exists() else 0
    if have > total:
        part.unlink()
        have = 0
    if have == total:
        return
    headers = {"Range": f"bytes={have}-"} if have else {}
    last = -1
    try:
        with client.stream("GET", _check_host(url), headers=headers) as r:
            if r.status_code == 200:
                have = 0  # server ignored Range, start over
            elif r.status_code != 206:
                raise httpx.HTTPError("unexpected status")
            with open(part, "ab" if have else "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    have += len(chunk)
                    pct = have * 100 // total
                    if pct != last:
                        last = pct
                        progress(f"Downloading Ollama... {pct}%")
    except httpx.HTTPError as e:
        raise RuntimeInstallError(
            "The download was interrupted. Press Try Again to continue where it stopped."
        ) from e
    if part.stat().st_size != total:
        raise RuntimeInstallError(
            "The download was interrupted. Press Try Again to continue where it stopped."
        )


def _signature_info(path: str) -> tuple[str, str]:
    """(status, signer certificate subject) from Windows Authenticode."""
    script = (
        "$s = Get-AuthenticodeSignature -LiteralPath $env:RT_FILE; "
        "[Console]::Out.Write($s.Status.ToString() + '|' + $s.SignerCertificate.Subject)"
    )
    # Full path: the verifier itself must not be resolved through PATH.
    powershell = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
    )
    try:
        out = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "RT_FILE": path}, capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "Unknown", ""
    status, _, subject = out.partition("|")
    return status.strip(), subject.strip()


def _verify_signature(path: str) -> None:
    status, subject = _signature_info(path)
    if status != "Valid" or not re.search(
        r"(?:^|, )O=" + re.escape(PUBLISHER_ORG) + r"(?:,|$)", subject
    ):
        raise RuntimeInstallError(
            "The downloaded installer could not be verified as genuine, so it was not run "
            "and has been deleted. Please try again, or install Ollama from ollama.com yourself."
        )


def install_ollama(progress) -> str:
    if not _is_windows():
        raise RuntimeInstallError("Automatic install is only available on Windows.")
    existing = find_ollama()
    if existing:
        return existing
    progress("Contacting ollama.com...")
    with _client() as c:
        url, total = _resolve(c)
        # The version is part of the path, so a new release never resumes an old partial file.
        key = hashlib.sha256(urlparse(url).path.encode()).hexdigest()[:12]
        part = _cache_dir() / f"OllamaSetup-{key}.part"
        _download(c, url, total, part, progress)

    work = tempfile.mkdtemp(prefix="odysseus-")
    try:
        exe = os.path.join(work, "OllamaSetup.exe")
        os.replace(part, exe)
        progress("Checking the installer is genuine...")
        try:
            _verify_signature(exe)
        except RuntimeInstallError:
            os.remove(exe)
            raise
        progress("Installing Ollama...")
        try:
            # Inno Setup flags; the installer is per-user (no admin prompt).
            r = subprocess.run(
                [exe, "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"],
                timeout=INSTALL_TIMEOUT_S, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeInstallError(
                "The installation took too long and was stopped. Please try again."
            ) from e
        except OSError as e:
            raise RuntimeInstallError("The installer could not be started. Please try again.") from e
        if r.returncode != 0:
            raise RuntimeInstallError("The installation did not finish. Please try again.")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    found = find_ollama()
    if not found:
        raise RuntimeInstallError(
            "Ollama was installed but could not be found. Please restart the app and try again."
        )
    return found


# ---- Running server ------------------------------------------------------------

def _base_url() -> str:
    raw = os.environ.get("OLLAMA_HOST", "").strip()
    if not raw:
        return f"http://127.0.0.1:{DEFAULT_PORT}"
    u = urlparse(raw if "://" in raw else "//" + raw)
    host = u.hostname or ""
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    try:
        port = u.port or DEFAULT_PORT
    except ValueError:
        port = DEFAULT_PORT
    return f"{u.scheme if u.scheme in ('http', 'https') else 'http'}://{host}:{port}"


def _answers(base: str) -> bool:
    try:
        return httpx.get(base + "/api/version", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def ensure_ollama_running(exe: str) -> str:
    base = _base_url()
    if _answers(base):
        return base
    kw = {"start_new_session": True} if not _is_windows() else {
        "creationflags": subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
    }
    try:
        subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **kw)
    except OSError as e:
        raise RuntimeInstallError("Ollama could not be started. Try restarting your computer.") from e
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if _answers(base):
            return base
        time.sleep(0.5)
    raise RuntimeInstallError("Ollama did not start in time. Press Try Again, or restart your computer.")


def list_ollama_models(base_url: str) -> list[str]:
    try:
        r = httpx.get(base_url + "/api/tags", timeout=10.0)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except (httpx.HTTPError, ValueError, KeyError) as e:
        raise RuntimeInstallError("Could not read the list of installed models from Ollama.") from e


# ---- Models --------------------------------------------------------------------

def recommend_model(ram_gb: float, vram_gb: float) -> dict:
    _, _, tag, size, lic = next(r for r in _MODELS if vram_gb >= r[0] or ram_gb >= r[1])
    name, _, ver = tag.partition(":")
    try:
        r = httpx.get(f"{REGISTRY}/{name}/manifests/{ver}", timeout=10.0)
        r.raise_for_status()
        m = r.json()
        size = m["config"]["size"] + sum(layer["size"] for layer in m["layers"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass  # registry unreachable: keep the built-in size
    return {"model": tag, "size_bytes": size, "license": lic}


# ---- Agent readiness -----------------------------------------------------------

MIN_AGENT_CONTEXT = 8192  # smallest context that agent mode needs


def ollama_root(base_url: str) -> str | None:
    """Server root when base_url is an Ollama OpenAI-compatible URL (ends in /v1), else None."""
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        return None
    root = url[: -len("/v1")]
    try:
        r = httpx.get(root + "/api/version", timeout=2.0)
        if r.status_code == 200 and isinstance(r.json().get("version"), str):
            return root
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, AttributeError):
        pass
    return None


def model_details(root: str, model: str) -> dict:
    """Tool support and explicit context size of a model: {"tools": bool, "num_ctx": int | None}."""
    try:
        r = httpx.post(root + "/api/show", json={"model": model}, timeout=10.0)
        r.raise_for_status()
        data = r.json()
        found = re.search(r"^\s*num_ctx\s+(\d+)",data.get("parameters") or "", re.MULTILINE)
        return {
            "tools": "tools" in (data.get("capabilities") or []),
            "num_ctx": int(found.group(1)) if found else None,
        }
    except (httpx.HTTPError, ValueError, KeyError, AttributeError, TypeError) as e:
        raise RuntimeInstallError("Could not read the details of the model from Ollama.") from e


def loaded_context(root: str, model: str) -> int | None:
    """Load the model, then return the context length the server really gave it (None if unknown)."""
    try:
        r = httpx.post(root + "/api/generate", json={"model": model}, timeout=300.0)
        r.raise_for_status()
        r = httpx.get(root + "/api/ps", timeout=10.0)
        r.raise_for_status()
        for m in r.json().get("models", []):
            if m.get("name") in (model, model + ":latest") or m.get("model") in (model, model + ":latest"):
                value = m.get("context_length")
                return value if isinstance(value, int) else None
        return None
    except (httpx.HTTPError, ValueError, KeyError, AttributeError, TypeError) as e:
        raise RuntimeInstallError("Could not check the memory size of the model in Ollama.") from e


def agent_context_size(ram_gb: float, vram_gb: float) -> int:
    return 16384 if max(ram_gb, vram_gb) >= 12 else 8192


def context_variant_name(model: str, num_ctx: int) -> str:
    return f"{model}-ctx{num_ctx // 1024}k"


def create_context_variant(root: str, model: str, num_ctx: int) -> str:
    """Make a copy of the model with a longer context (shares the weights, no download)."""
    name = context_variant_name(model, num_ctx)
    if name in list_ollama_models(root):
        raise RuntimeInstallError("A model with the longer memory already exists, so nothing was changed.")
    try:
        r = httpx.post(
            root + "/api/create",
            json={"model": name, "from": model, "parameters": {"num_ctx": num_ctx}, "stream": False},
            timeout=120.0,
        )
        if r.status_code == 200 and r.json().get("status") == "success":
            return name
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    raise RuntimeInstallError("Ollama could not make a copy of the model with a longer memory.")


def probe_tool_call(root: str, model: str) -> bool:
    """True when the model answers a small request with a real tool call. Never raises."""
    tool = {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file and return its content.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "File path"}},
                "required": ["path"],
            },
        },
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Read the file note.txt with the tool."}],
        "tools": [tool],
        "think": False,
        "max_tokens": 256,
        "stream": False,
    }
    try:
        r = httpx.post(root + "/v1/chat/completions", json=body, timeout=120.0)
        return r.status_code == 200 and bool(r.json()["choices"][0]["message"].get("tool_calls"))
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return False


def pull_model(base_url: str, model: str, progress) -> None:
    layers: dict[str, tuple[int, int]] = {}
    best_done = 0
    try:
        with httpx.stream("POST", base_url + "/api/pull", json={"model": model, "stream": True},
                          timeout=httpx.Timeout(15.0, read=120.0)) as r:
            if r.status_code != 200:
                raise RuntimeInstallError("Ollama could not download this model. Please try again.")
            for line in r.iter_lines():
                if not line:
                    continue
                msg = json.loads(line)
                if msg.get("error"):
                    raise RuntimeInstallError("Ollama could not download this model. Please try again.")
                if msg.get("digest") and msg.get("total"):
                    layers[msg["digest"]] = (msg.get("completed", 0), msg["total"])
                    best_done = max(best_done, sum(c for c, _ in layers.values()))
                    progress(best_done, max(best_done, sum(t for _, t in layers.values())))
    except (httpx.HTTPError, ValueError) as e:
        raise RuntimeInstallError(
            "The download was interrupted. Press Try Again to continue where it stopped."
        ) from e
