"""First-run automatic setup: take an install with no model endpoint to a verified working one.

One run walks five steps (check_computer, find_agent, install_components, connect,
test). Anything that downloads or installs waits for the user's consent first.
State lives in memory; every step checks before acting, so a re-run is safe.
"""
import asyncio
import json
import logging
import os
import platform
import re
import shutil
import sys
import traceback
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.constants import APP_VERSION, SETUP_LOG_FILE

logger = logging.getLogger(__name__)

STEP_IDS = ("check_computer", "find_agent", "install_components", "connect", "test")
MANUAL_HINT = "Open Settings > Models to connect a model manually."
NOTE_SHORT_MEMORY = "Agent tasks may stop early because this model has a short memory. Updating Ollama and pressing Repair Installation can fix this."
NOTE_NO_TOOLS = "This model did not pass the tools test, so agent tasks may not work. You can pick another model in Settings > Models."
NOTE_COPY_DECLINED = "Agent tasks may stop early because this model has a short memory. Press Repair Installation if you want Odysseus to add a copy with a longer memory."
NOTE_COPY_NAME_TAKEN = "Agent tasks may stop early because this model has a short memory. A model with the name Odysseus would use for a longer-memory copy already exists, but its memory is too short. Remove that model, then press Repair Installation."
RETRY_HINT = "Press Try Again."  # the button on the page that shows a failed setup
_SUPPORTED_ARCH = {"x86_64", "amd64", "arm64", "aarch64"}
_DISK_MARGIN = 1.2

_SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|authorization)\b\s*[=:]\s*\S+"),
    re.compile(r"\b(?:sk|hf|ghp|gho|xox[abp])[-_][A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b[A-Za-z0-9+/_\-]{40,}={0,2}"),
)


class SetupFailed(Exception):
    """A step failed in a way the user can read; message and hint are shown as-is."""

    def __init__(self, message: str, hint: str = RETRY_HINT, title: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.title = title  # page heading for an ending that is not a fault, such as the user's own "Not now"


def redact(text: str) -> str:
    """Hide the home directory and anything that looks like a key or token."""
    home = os.path.expanduser("~")
    for variant in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
        if len(variant) > 3:
            text = re.sub(re.escape(variant), "~", text, flags=re.IGNORECASE)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def _log_path() -> str:
    return SETUP_LOG_FILE


def _existing_parent(path: str) -> str:
    """The folder itself, or the nearest parent that exists (the model folder may not be created yet)."""
    path = os.path.abspath(path)
    while not os.path.exists(path) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    return path


def _volume(path: str) -> int:
    return os.stat(path).st_dev


def _runtime():
    import src.runtime_installer as runtime_installer
    return runtime_installer


def _enabled_llm_endpoints() -> List[Dict[str, Any]]:
    from core.database import SessionLocal, ModelEndpoint
    db = SessionLocal()
    try:
        rows = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()  # noqa: E712
        return [
            {"id": r.id, "name": r.name, "base_url": r.base_url, "api_key": r.api_key}
            for r in rows
            if (r.model_type or "llm") == "llm"
        ]
    finally:
        db.close()


def _probe_models(base_url: str, api_key: Optional[str]) -> List[str]:
    from routes.model_routes import _probe_endpoint
    return _probe_endpoint(base_url, api_key, timeout=5) or []


def _first_chat(models: List[str]) -> Optional[str]:
    from src.endpoint_resolver import _first_chat_model
    return _first_chat_model(models)


def _probe_chat(base_url: str, api_key: Optional[str], model: str) -> Dict[str, Any]:
    from routes.model_routes import _probe_single_model
    return _probe_single_model(base_url, api_key or "", model, timeout=120)


def _store_endpoint(target: Dict[str, Any], invalidate) -> str:
    """Reuse (and enable) the endpoint row for this URL or create one; then make sure a default exists."""
    from core.database import SessionLocal, ModelEndpoint
    from routes.model_routes import _assign_default_endpoint_if_needed
    from src.endpoint_resolver import _endpoint_cached_models
    models = target["models"]
    db = SessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(ModelEndpoint.base_url == target["base_url"]).first()
        if ep is None:
            ep = ModelEndpoint(
                id=str(uuid.uuid4())[:8],
                name=target["name"],
                base_url=target["base_url"],
                is_enabled=True,
                model_type="llm",
                endpoint_kind="local",
                model_refresh_mode="auto",
                cached_models=json.dumps(models),
            )
            db.add(ep)
        else:
            ep.is_enabled = True
            if models and models[0] not in _endpoint_cached_models(ep):
                ep.cached_models = json.dumps(models)  # the stored list is empty or lacks the chosen model
        db.commit()
        _assign_default_endpoint_if_needed(db, ep.id, models)
        if invalidate:
            invalidate()
        return ep.id
    finally:
        db.close()


def _mark_tools_supported(endpoint_id: str, invalidate) -> None:
    """Record a passed tool test on the endpoint row; a value the user set is never changed."""
    from core.database import SessionLocal, ModelEndpoint
    db = SessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
        if ep is not None and ep.supports_tools is None:
            ep.supports_tools = True
            db.commit()
        if invalidate:
            invalidate()
    finally:
        db.close()


def _current_default() -> Dict[str, str]:
    """The default chat endpoint id and model from the settings ('' when unset)."""
    from src.settings import load_settings
    settings = load_settings()
    return {"endpoint_id": settings.get("default_endpoint_id") or "", "model": settings.get("default_model") or ""}


def _set_default_model(model: str) -> None:
    from src.settings import load_settings, save_settings
    settings = load_settings()
    settings["default_model"] = model
    save_settings(settings)


def _scan_this_computer(model_discovery) -> List[Dict[str, Any]]:
    """Model servers answering on this computer only (no LAN, no Tailscale peers)."""
    from src.model_discovery import ModelDiscovery
    local = ModelDiscovery("127.0.0.1", getattr(model_discovery, "openai_api_key", None))
    local._get_hosts = lambda: ["127.0.0.1", "localhost"]
    return local.discover_models().get("items", [])


def _fresh_state() -> Dict[str, Any]:
    return {
        "state": "idle",
        "steps": [{"id": s, "status": "pending", "detail": ""} for s in STEP_IDS],
        "progress": {"done_bytes": 0, "total_bytes": 0},
        "consent": None,
        "error": None,
        "result": None,
    }


class AutoSetup:
    def __init__(self, model_discovery=None, invalidate_models_cache=None):
        self._discovery = model_discovery
        self._invalidate = invalidate_models_cache
        self._data = _fresh_state()
        self._task: Optional[asyncio.Task] = None
        self._consent_event: Optional[asyncio.Event] = None
        self._approved = False
        self._pulled_model = ""
        self._hw: Dict[str, Any] = {}
        self._notes: List[str] = []

    # ---- public API -------------------------------------------------------

    def needed(self) -> bool:
        try:
            return not _enabled_llm_endpoints()
        except Exception:
            return True

    async def status(self) -> Dict[str, Any]:
        needed = await asyncio.to_thread(self.needed)
        return {"needed": needed, **json.loads(json.dumps(self._data))}

    async def start(self) -> Dict[str, Any]:
        if self._data["state"] not in ("running", "needs_consent"):
            self._data = _fresh_state()
            self._data["state"] = "running"
            self._log("Setup started")
            self._task = asyncio.create_task(self._run())
        return await self.status()

    async def respond(self, approve: bool, kind: Optional[str] = None, name: Optional[str] = None) -> Dict[str, Any]:
        asked = self._data["consent"] or {}
        # An answer from a page that still shows another question (an old tab) is ignored.
        stale = (kind, name) != (None, None) and (kind, name) != (asked.get("kind"), asked.get("name"))
        if self._data["state"] == "needs_consent" and self._consent_event is not None and not stale:
            self._approved = bool(approve)
            self._data["consent"] = None
            self._data["state"] = "running"  # a declined install fails the run right after
            self._consent_event.set()
            await asyncio.sleep(0)
        return await self.status()

    def report(self) -> str:
        hw = self._hw or {}
        lines = [
            f"Odysseus {APP_VERSION}",
            f"OS: {platform.platform()}",
            f"CPU architecture: {platform.machine()}",
            f"RAM: {hw.get('total_ram_gb', 'unknown')} GB",
            f"GPU: {hw.get('gpu_name') or 'none detected'} ({hw.get('gpu_vram_gb') or 0} GB)",
            f"Setup state: {self._data['state']}",
        ]
        if self._data["error"]:
            lines.append(f"Last error: {self._data['error']['message']}")
        lines += ["", "Setup log:"]
        try:
            with open(_log_path(), encoding="utf-8") as f:
                lines.append(f.read())
        except OSError:
            lines.append("(no log yet)")
        return redact("\n".join(lines))

    # ---- internals --------------------------------------------------------

    def _log(self, message: str) -> None:
        line = f"{datetime.now().isoformat(timespec='seconds')} {redact(message)}\n"
        try:
            os.makedirs(os.path.dirname(_log_path()), exist_ok=True)
            with open(_log_path(), "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            logger.warning("Could not write the setup log")

    def _step(self, step_id: str, status: str, detail: str = "") -> None:
        for s in self._data["steps"]:
            if s["id"] == step_id:
                s["status"], s["detail"] = status, detail
        self._log(f"{step_id}: {status} {detail}".rstrip())

    def _progress(self, done: int, total: int) -> None:
        self._data["progress"] = {"done_bytes": int(done), "total_bytes": int(total)}

    async def _ask_consent(self, consent: Dict[str, Any], optional: bool = False) -> bool:
        """Wait for the user's answer. A declined install ends the run; a declined optional step returns False."""
        self._consent_event = asyncio.Event()
        self._approved = False
        self._data["consent"] = consent
        self._data["state"] = "needs_consent"
        self._log(f"Waiting for consent: {consent['kind']} ({consent['name']})")
        await self._consent_event.wait()
        if not self._approved and not optional:
            raise SetupFailed("Setup was cancelled. Nothing was installed.", "Press Try Again whenever you want to continue.",
                              title="Setup is not finished")
        return self._approved

    def _check_disk(self, model_bytes: int, installer_bytes: int = 0) -> None:
        """Models go to the model folder's drive, the installer to the home drive; one drive pays for both."""
        home = os.path.expanduser("~")
        models = _existing_parent(_runtime().model_dir())
        if _volume(models) == _volume(home):
            needs = [(home, model_bytes + installer_bytes)]
        else:
            needs = [(models, model_bytes), (home, installer_bytes)]
        for path, size_bytes in needs:
            free = shutil.disk_usage(path).free
            if size_bytes and free < size_bytes * _DISK_MARGIN:
                need_gb = size_bytes * _DISK_MARGIN / 1e9
                raise SetupFailed(
                    f"There is not enough free disk space. About {need_gb:.1f} GB is needed and {free / 1e9:.1f} GB is free.",
                    "Free up some space and press Try Again.",
                )

    async def _run(self) -> None:
        self._notes = []
        self._pulled_model = ""
        try:
            await self._check_computer()
            target = await self._find_agent()
            if target is None:
                target = await self._install_components()
            else:
                self._step("install_components", "done", "Nothing needed to be installed")
            await self._connect(target)
            await self._test(target)
            result = {"endpoint_name": target["name"], "model": target["model"]}
            if self._notes:
                result["note"] = "Chat is ready. " + " ".join(self._notes)
            self._data["result"] = result
            self._data["state"] = "done"
            self._log("Setup finished")
        except SetupFailed as e:
            self._fail(e.message, e.hint, e.title)
        except Exception as e:
            # The installer's own errors are already written for the user.
            if type(e).__name__ == "RuntimeInstallError":
                self._fail(str(e), RETRY_HINT)
                return
            self._log("Unexpected error: " + "".join(traceback.format_exception_only(type(e), e)).strip())
            self._log(traceback.format_exc())
            self._fail("Something unexpected went wrong during setup.", RETRY_HINT + " If it keeps happening, copy the report for support.")

    def _fail(self, message: str, hint: str, title: str = "") -> None:
        for s in self._data["steps"]:
            if s["status"] == "running":
                s["status"] = "failed"
        self._data["error"] = {"message": redact(message), "hint": hint, "title": title}
        self._data["consent"] = None
        self._data["state"] = "failed"
        self._log(f"Failed: {message}")

    async def _check_computer(self) -> None:
        self._step("check_computer", "running", "Looking at your computer")
        if not sys.platform.startswith(("win", "darwin", "linux")) or platform.machine().lower() not in _SUPPORTED_ARCH:
            raise SetupFailed("Automatic setup does not support this type of computer yet.", MANUAL_HINT)
        from services.hwfit.hardware import detect_system
        self._hw = await asyncio.to_thread(detect_system) or {}
        ram = self._hw.get("total_ram_gb") or 0
        self._step("check_computer", "done", f"{ram} GB memory" + (f", {self._hw['gpu_name']}" if self._hw.get("gpu_name") else ""))

    async def _find_agent(self) -> Optional[Dict[str, Any]]:
        self._step("find_agent", "running", "Looking for an AI model that already works")
        default = await asyncio.to_thread(_current_default)
        rows = await asyncio.to_thread(_enabled_llm_endpoints)
        rows.sort(key=lambda row: row["id"] != default["endpoint_id"])  # the endpoint in use is tried first
        for row in rows:
            models = await asyncio.to_thread(_probe_models, row["base_url"], row["api_key"])
            in_use = row["id"] == default["endpoint_id"] and default["model"] in models
            model = default["model"] if in_use else _first_chat(models)
            if model:
                self._step("find_agent", "done", f"Found {row['name']}")
                return {**row, "model": model, "models": models}
        items = await asyncio.to_thread(_scan_this_computer, self._discovery)
        ri = _runtime()
        for item in items:
            model = _first_chat(item["models"])
            if model:
                base_url = f"http://{item['host']}:{item['port']}/v1"
                provider = item.get("provider")
                if not ri.runtime_name(provider) and await asyncio.to_thread(ri.ollama_root, base_url):
                    provider = "ollama"
                runtime = ri.runtime_name(provider)
                where = f"on this computer (port {item['port']})"
                self._step("find_agent", "done", f"Found {runtime or 'a model server'} {where}")
                return {
                    "name": f"{runtime} {where}" if runtime else f"This computer (port {item['port']})",
                    "base_url": base_url,
                    "api_key": None,
                    "model": model,
                    "models": item["models"],
                }
        self._step("find_agent", "done", "No AI model found yet")
        return None

    async def _install_components(self) -> Dict[str, Any]:
        ri = _runtime()
        self._step("install_components", "running", "Preparing the AI model")
        rec = await asyncio.to_thread(ri.recommend_model, self._hw.get("total_ram_gb") or 0, self._hw.get("gpu_vram_gb") or 0)
        model_consent = {
            "kind": "download_model",
            "name": rec["model"],
            "source_url": f"https://ollama.com/library/{rec['model'].split(':')[0]}",
            "size_bytes": rec["size_bytes"],
            "license": rec["license"],
            "cost": "Free",
        }
        exe = await asyncio.to_thread(ri.find_ollama)
        if exe:
            base = await asyncio.to_thread(ri.ensure_ollama_running, exe)
            models = await asyncio.to_thread(ri.list_ollama_models, base)
            model = _first_chat(models)
            if not model:
                await self._ask_consent(model_consent)
                self._check_disk(rec["size_bytes"])
                model = await self._pull(ri, base, rec)
                models = [model]
        else:
            offer = await asyncio.to_thread(ri.ollama_install_offer)
            if offer is None:
                raise SetupFailed(
                    "Automatic setup cannot install the AI runtime on this computer.",
                    f"Install {ri.runtime_name('ollama')} from {ri.RUNTIMES['ollama']['download_page']} and start it. "
                    f"Then press Try Again and setup finds it by itself. {MANUAL_HINT}",
                )
            total = offer["size_bytes"] + rec["size_bytes"]
            await self._ask_consent({
                "kind": "install_runtime",
                "name": f"{offer['name']} and the {rec['model']} model",
                "source_url": offer["source_url"],
                "size_bytes": total,
                "license": offer["license"],
                "cost": offer["cost"],
            })
            self._check_disk(rec["size_bytes"], offer["size_bytes"])
            self._step("install_components", "running", f"Installing {offer['name']}")
            exe = await asyncio.to_thread(ri.install_ollama, lambda m: self._step("install_components", "running", m))
            base = await asyncio.to_thread(ri.ensure_ollama_running, exe)
            model = await self._pull(ri, base, rec)
            models = [model]
        self._step("install_components", "done", f"{model} is ready")
        return {
            "name": "This computer (Ollama)",
            "base_url": base.rstrip("/") + "/v1",
            "api_key": None,
            "model": model,
            "models": models,
        }

    async def _pull(self, ri, base: str, rec: Dict[str, Any]) -> str:
        self._step("install_components", "running", f"Downloading {rec['model']}")
        self._progress(0, rec["size_bytes"])
        await asyncio.to_thread(ri.pull_model, base, rec["model"], self._progress)
        self._pulled_model = rec["model"]  # every pull follows a question that also announces a longer-memory copy
        return rec["model"]

    async def _connect(self, target: Dict[str, Any]) -> None:
        self._step("connect", "running", "Connecting Odysseus to the model")
        await self._prepare_agent_model(target)
        target["endpoint_id"] = await asyncio.to_thread(_store_endpoint, {**target}, self._invalidate)
        self._step("connect", "done", target["name"])

    async def _test(self, target: Dict[str, Any]) -> None:
        self._step("test", "running", "Asking the model to say hello")
        result = await asyncio.to_thread(_probe_chat, target["base_url"], target.get("api_key"), target["model"])
        if result.get("status") != "ok" or not (result.get("reply") or "").strip():
            self._step("test", "failed", "The model did not answer")
            raise SetupFailed("The model is installed but did not answer a test message.", RETRY_HINT)
        root = target.get("ollama_root")
        if not root:
            self._step("test", "done", "The model answered")
            return
        self._step("test", "running", "Checking that the model can use tools")
        if await asyncio.to_thread(_runtime().probe_tool_call, root, target["model"]):
            if target.get("agent_ready"):
                await asyncio.to_thread(_mark_tools_supported, target["endpoint_id"], self._invalidate)
                self._log("Tool call test passed")
            else:
                # Native tool calls need more context than the smaller text form the unset value gives.
                self._log("Tool call test passed; the endpoint setting was left as it is because the model was not prepared for agent tasks")
            self._step("test", "done", "The model answered and can use tools")
        else:
            self._notes.append(NOTE_NO_TOOLS)
            self._log("Tool call test failed")
            self._step("test", "done", "The model answered")

    async def _prepare_agent_model(self, target: Dict[str, Any]) -> None:
        """On a local Ollama server: give the model in use (or, with no default yet, the best one) a context of at least MIN_AGENT_CONTEXT."""
        ri = _runtime()
        root = await asyncio.to_thread(ri.ollama_root, target["base_url"])
        if root is None:
            return
        default = await asyncio.to_thread(_current_default)
        rows = {row["id"]: row for row in await asyncio.to_thread(_enabled_llm_endpoints)}
        in_use = rows.get(default["endpoint_id"]) if default["model"] else None
        if in_use and in_use["base_url"] != target["base_url"]:
            self._log("The default model is on another endpoint, so the models on this server were left as they are")
            return
        target["ollama_root"] = root
        minimum = ri.MIN_AGENT_CONTEXT

        def choose(name: str) -> None:
            target["models"] = [name] + [m for m in target["models"] if m != name]
            target["model"] = name

        try:
            if in_use and default["model"] in target["models"]:
                choose(default["model"])  # only the model in use is prepared
                candidates = [target["model"]]
                names = [m for m in target["models"] if m == target["model"] or m.startswith(target["model"] + "-ctx")]
            else:
                names = candidates = list(dict.fromkeys([*target["models"], target["model"]]))
            details = {m: await asyncio.to_thread(ri.model_details, root, m) for m in names}
            pool = [m for m in candidates if details[m]["tools"]]
            if not pool:
                self._log("No model with tool support was found, so the models were left as they are")
                return
            pick = (next((m for m in pool if (details[m]["num_ctx"] or 0) >= minimum), None)
                    or next((m for m in pool if details[m]["num_ctx"] is None), pool[0]))
            choose(pick)
            ctx = details[pick]["num_ctx"]
            if ctx is None:
                self._step("connect", "running", "Checking the model's memory (this can take a minute)")
                ctx = await asyncio.to_thread(ri.loaded_context, root, pick)
            if (ctx or 0) < minimum:
                copy = next((m for m in names if m.startswith(pick + "-ctx") and (details[m]["num_ctx"] or 0) >= minimum), None)
                if copy is None:
                    size = ri.agent_context_size(self._hw.get("total_ram_gb") or 0, self._hw.get("gpu_vram_gb") or 0)
                    taken = ri.context_variant_name(pick, size)
                    if taken in target["models"]:
                        # Its context is too short (else it was chosen above), and a copy never overwrites a model.
                        self._log(f"A model named {taken} already exists with too short a memory, so {pick} was left as it is")
                        self._notes.append(NOTE_COPY_NAME_TAKEN)
                        return
                    # Any model that setup did not download in this run is the user's: ask before adding a copy of it.
                    if pick != self._pulled_model and not await self._ask_consent({
                        "kind": "add_model_copy",
                        "name": ri.context_variant_name(pick, size),
                        "source_url": f"Made on this computer from {pick}",
                        "size_bytes": 0,
                        "license": f"Same as {pick}",
                        "cost": "Free",
                    }, optional=True):
                        self._log(f"The longer-context copy of {pick} was declined, so the model was left as it is")
                        self._notes.append(NOTE_COPY_DECLINED)
                        return
                    self._step("connect", "running", "Giving the model a longer memory")
                    copy = await asyncio.to_thread(ri.create_context_variant, root, pick, size)
                choose(copy)
                self._log(f"Chose {copy}, a copy of {pick} with a longer context (context before: {ctx or 'unknown'})")
            else:
                self._log(f"Chose {pick} (context {ctx})")
            target["agent_ready"] = True
            if in_use and target["model"] != default["model"]:
                await asyncio.to_thread(_set_default_model, target["model"])
                self._log(f"The default model is now {target['model']} (before: {default['model']})")
        except Exception as e:
            self._log(f"Could not prepare the model for agent tasks: {e}")
            self._notes.append(NOTE_SHORT_MEMORY)
