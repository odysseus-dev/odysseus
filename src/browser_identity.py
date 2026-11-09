"""Trusted browser observations. No page execution capability is available.

0.35.0 local-launch CLI drops pin flags on `session info`; live Docker probes
proved destroyed-target retargeting. Observations are not permission to run a
page command. The future producer must atomically enforce expected identities.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace, field
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import struct
import tempfile
from typing import Any
from urllib.parse import urlsplit

from src.agent_runtime.resources import (
    BrowserPageResource, BrowserSessionObservation, BrowserSessionResource,
    NativeBackendResource, ResourceIdentityError,
)
from src.process_lifecycle import ProcessIdentity, observe
from src.constants import BROWSER_RESOURCES_DIR

PRODUCER_VERSION = "0.35.0"
# Wave 3 session metadata supports only these observed glibc Linux artifacts.
# macOS/Windows and other architectures fail closed before any producer call.
PRODUCER_HASHES = {
    "linux-x64": "b7a28c3a43a7008dd02585e2e60c391c08983f7a099149caed63c9f13f57b752",
    "linux-arm64": "92cd7d0897837ac648b9a6ab1965c69c5920e0f54df57e4295cdb1143b0541c8",
}
# Explicit release installation paths; PATH and npm caches are never searched.
PRODUCER_ROOT = Path("/usr/local/lib/node_modules/agent-browser/bin")
STATE_ROOT = Path(BROWSER_RESOURCES_DIR)
CLIENT_DEADLINE_S = 20  # Below 0.35.0's source-verified 30s read/resend floor.
CDP_DEADLINE_S = 3
CDP_METHODS = frozenset({"Target.getTargets", "Target.getTargetInfo", "Target.attachToTarget",
                         "Page.getFrameTree", "Target.detachFromTarget"})
PAGE_ACTIONS = frozenset({"open", "read", "snapshot", "find", "evaluate", "click", "fill",
    "press", "scroll", "wait", "screenshot", "navigate", "reload", "back", "forward",
    "select_page", "close_page", "network", "console", "new_page", "tabs"})
SESSION_ACTIONS = frozenset({"session_info"})
PAGE_FAILURE = "browser_page_authority_unavailable"
_ACTIVE = ContextVar("browser_resource_operation", default=None)
_REGISTRY: dict[tuple[str, str], "RegisteredBrowser"] = {}


def digest(domain, value):
    return hashlib.sha256((domain + "\0" + json.dumps(value, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()


def incarnation(observation):
    values = observation.to_dict() if hasattr(observation, "to_dict") else dict(observation)
    values.pop("session_incarnation", None)
    return digest("odysseus.browser.session.v1", values)


def browser_digest(url):
    # Never include the capability URL, raw GUID or exceptions containing them
    # in results/logs/persisted records.
    if not isinstance(url, str) or not re.fullmatch(
            r"ws://127\.0\.0\.1:[1-9][0-9]{0,4}/devtools/browser/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", url):
        raise ResourceIdentityError("Unverifiable browser endpoint")
    parsed = urlsplit(url)
    if parsed.port is None or parsed.port > 65535:
        raise ResourceIdentityError("Invalid browser endpoint port")
    return digest("odysseus.browser.guid.v1", parsed.path.rsplit("/", 1)[-1])


def page_unavailable():
    return {"error": "The configured producer cannot guarantee stable binding to the captured page in local-launch mode.",
            "exit_code": 1, "failure_kind": PAGE_FAILURE, "executed": False,
            "retryable": False, "producer_capability_unavailable": True}


def parse_operation(content):
    from src.agent_runtime.authority import ExactOperation
    operation = ExactOperation.normalize("private_browser", content)
    try:
        args = json.loads(operation.input)
    except (ValueError, TypeError):
        raise ResourceIdentityError("Browser arguments require a JSON object") from None
    if not isinstance(args, dict):
        raise ResourceIdentityError("Browser arguments require a JSON object")
    action = args.get("action")
    if not isinstance(action, str) or action not in PAGE_ACTIONS | SESSION_ACTIONS | {"close"}:
        raise ResourceIdentityError("Unsupported browser action; raw commands and batch are forbidden")
    allowed = {"action", "page", "url", "selector", "target", "ref", "key", "direction", "amount",
               "timeout_ms", "timeout_s", "text", "value", "script", "path", "find"}
    if set(args) - allowed:
        raise ResourceIdentityError("Browser flags, labels, configuration and raw targetIds are forbidden")
    if "page" in args and (not isinstance(args["page"], str) or not re.fullmatch(r"t[1-9][0-9]*", args["page"])):
        raise ResourceIdentityError("Browser page selector must be tN")
    if action in SESSION_ACTIONS and set(args) != {"action"}:
        raise ResourceIdentityError("Session metadata takes no page or CLI arguments")
    for key, value in args.items():
        if isinstance(value, str) and ("\0" in value or value.lstrip().startswith("-")):
            raise ResourceIdentityError("Model values cannot become browser flags")
    return operation, args


def native_browser(operation, backend):
    return operation.tool == "private_browser" and isinstance(backend, NativeBackendResource)


@dataclass(frozen=True)
class TrustedProducer:
    path: Path
    platform: str
    binary_sha256: str

    def validate(self):
        if (self.path != PRODUCER_ROOT / ("agent-browser-" + self.platform)
                or self.path.is_symlink() or not self.path.is_file()
                or self.path.stat().st_mode & 0o022
                or self.path.stat().st_uid != os.getuid() and self.path.stat().st_uid != 0
                or hashlib.sha256(self.path.read_bytes()).hexdigest() != PRODUCER_HASHES.get(self.platform)):
            raise ResourceIdentityError("Browser producer is not an allowlisted release binary")


async def trusted_producer():
    machine = {"x86_64": "x64", "aarch64": "arm64"}.get(platform.machine())
    key = platform.system().lower() + "-" + str(machine)
    if key not in PRODUCER_HASHES:
        raise ResourceIdentityError("Unsupported browser producer platform")
    producer = TrustedProducer(PRODUCER_ROOT / ("agent-browser-" + key), key, PRODUCER_HASHES[key])
    producer.validate()
    stdout, _ = await run_client([str(producer.path), "--version"], env={"PATH": "/usr/bin:/bin"}, cwd="/")
    if stdout.strip() != "agent-browser " + PRODUCER_VERSION:
        raise ResourceIdentityError("Unsupported browser producer version")
    return producer


async def run_client(argv, *, env, cwd):
    """One bounded invocation, never retry. Timeout/cancellation kills the client.

    Internal immediate EOF/reset retries cannot be eliminated by an outer
    deadline. Consequently no effect is authorized by this client wrapper.
    """
    process = None
    # Files avoid detached daemon pipe inheritance keeping communicate alive.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        spawn = None
        try:
            spawn = asyncio.create_task(asyncio.create_subprocess_exec(*argv, stdout=out, stderr=err,
                stdin=asyncio.subprocess.DEVNULL, env=env, cwd=cwd, start_new_session=True))
            process = await asyncio.shield(spawn)
            await asyncio.wait_for(process.wait(), CLIENT_DEADLINE_S)
            if process.returncode != 0:
                raise ResourceIdentityError("Browser producer command failed")
            out.seek(0); err.seek(0)
            raw = out.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ResourceIdentityError("Oversized producer response")
            return raw.decode("utf-8", errors="strict"), ""
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if process is None and spawn is not None:
                process = await asyncio.shield(spawn)
            if process is not None and process.returncode is None:
                process.kill()
                await asyncio.shield(process.wait())
            raise


def response(raw):
    from src.agent_runtime.authority import _pairs, _invalid_constant
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
    except (ValueError, TypeError):
        raise ResourceIdentityError("Malformed browser producer response") from None
    if (not isinstance(value, dict) or set(value) - {"success", "data", "error"} or value.get("success") is not True
            or value.get("error") is not None or not isinstance(value.get("data"), dict)):
        raise ResourceIdentityError("Unsuccessful browser producer response")
    return value["data"]


@dataclass
class RegisteredBrowser:
    owner: str
    thread_id: str
    producer: TrustedProducer
    key: str
    cwd: Path
    env: dict[str, str]
    config: Path
    config_identity: tuple[int, int]
    lock: asyncio.Lock
    session: BrowserSessionResource | None = None
    pages: tuple[BrowserPageResource, ...] = ()
    # A successful pin flag is NOT evidence this producer has armed its manager.
    pin_armed_for: str | None = None
    _endpoint: str = field(default="", repr=False)  # In memory only, never a snapshot.

    def validate_config(self):
        self.producer.validate()
        expected = owned_environment(self.cwd, self.key)
        if self.env != expected or self.config != self.cwd / "config.json":
            raise ResourceIdentityError("Browser producer configuration changed")
        info = self.config.lstat()
        if (self.cwd.is_symlink() or self.cwd.stat().st_mode & 0o077
                or self.config.is_symlink() or info.st_mode & 0o077
                or (info.st_dev, info.st_ino) != self.config_identity or self.config.read_text() != "{}"):
            raise ResourceIdentityError("Browser owned configuration changed")

    async def command(self, *args):
        self.validate_config()
        raw, _ = await run_client([str(self.producer.path), "--config", str(self.config),
            "--session", self.key, "--json", *args], env=self.env, cwd=self.cwd)
        return response(raw)

    def invalidate(self):
        self.session = None
        self.pages = ()
        self.pin_armed_for = None
        self._endpoint = ""


def owned_environment(cwd, key):
    # No ambient AGENT_BROWSER_*, XDG, proxy, provider, CDP, profile or state.
    return {"PATH": "/usr/bin:/bin", "HOME": str(cwd), "TMPDIR": str(cwd / "tmp"),
            "AGENT_BROWSER_SOCKET_DIR": str(cwd / "runtime"),
            "AGENT_BROWSER_EXECUTABLE_PATH": "/usr/bin/chromium",
            "AGENT_BROWSER_IDLE_TIMEOUT_MS": "300000"}


async def register_producer(owner, thread_id):
    """Server-only registration, not model discovery, restoration or lookup.

    Does not launch a daemon/browser. A future trusted launch producer must
    populate this exact owned runtime; legacy lifecycle entries are not adopted.
    """
    if not isinstance(owner, str) or not owner or not isinstance(thread_id, str) or not thread_id:
        raise ResourceIdentityError("Browser application ownership is required")
    if (owner, thread_id) in _REGISTRY:
        raise ResourceIdentityError("Browser producer is already registered")
    producer = await trusted_producer()
    key = "ody-" + digest("odysseus.browser.selector.v1", [owner, thread_id])[:24]
    STATE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    cwd = STATE_ROOT / key
    cwd.mkdir(mode=0o700)  # Existing unregistered state is not authoritative.
    for directory in ("tmp", "runtime"):
        (cwd / directory).mkdir(mode=0o700)
    config = cwd / "config.json"
    with config.open("x") as f:
        os.chmod(config, 0o600)
        f.write("{}")
        f.flush(); os.fsync(f.fileno())
    info = config.stat()
    record = RegisteredBrowser(owner, thread_id, producer, key, cwd, owned_environment(cwd, key),
        config, (info.st_dev, info.st_ino), asyncio.Lock())
    record.validate_config()
    _REGISTRY[(owner, thread_id)] = record
    return record


def registered(owner, thread_id):
    return _REGISTRY.get((owner, thread_id))  # Lookup never creates a session.


def daemon_observation(record, info):
    required = {"session", "active", "version", "pid", "runtimeError", "socketDir", "namespace", "runtime"}
    if (not isinstance(info, dict) or not required <= info.keys()
            or info.get("session") != record.key or info.get("active") is not True
            or info.get("version") != PRODUCER_VERSION or info.get("runtimeError") is not None
            or info.get("socketDir") != record.env["AGENT_BROWSER_SOCKET_DIR"]
            or info.get("namespace") is not None):
        raise ResourceIdentityError("Unregistered browser daemon")
    runtime = info.get("runtime")
    pid = info.get("pid")
    required_runtime = {"backgroundPid", "session", "engine", "browserLaunched",
                        "compatibilityStatus", "socketDir", "restoreKey"}
    if (type(pid) is not int or pid <= 0 or not isinstance(runtime, dict)
            or not required_runtime <= runtime.keys()
            or runtime.get("backgroundPid") != pid or runtime.get("session") != record.key
            or runtime.get("engine") != "chrome" or runtime.get("browserLaunched") is not True
            or runtime.get("compatibilityStatus") != "current"
            or runtime.get("socketDir") != info["socketDir"] or runtime.get("restoreKey") is not None):
        raise ResourceIdentityError("Malformed browser lifecycle observation")
    def executable(candidate):
        return Path(f"/proc/{candidate}/exe").resolve(strict=True)
    seen = observe(pid, executable)
    if seen is None or seen.facts != record.producer.path or not seen.identity.owned():
        raise ResourceIdentityError("Daemon does not match the trusted binary incarnation")
    return seen.identity


class CDPSidecar:
    """Minimal loopback websocket client for the five identity-only methods."""
    def __init__(self, url):
        browser_digest(url)
        self._url = url  # Ephemeral capability; never repr/serialize/log.
        self._counter = 0

    async def __aenter__(self):
        url = urlsplit(self._url)
        self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(url.hostname, url.port), CDP_DEADLINE_S)
        key = base64.b64encode(os.urandom(16)).decode()
        request = f"GET {url.path} HTTP/1.1\r\nHost: 127.0.0.1:{url.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        try:
            self.writer.write(request.encode())
            await asyncio.wait_for(self.writer.drain(), CDP_DEADLINE_S)
            header = await asyncio.wait_for(self.reader.readuntil(b"\r\n\r\n"), CDP_DEADLINE_S)
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
            headers = dict(line.split(b":", 1) for line in header.split(b"\r\n")[1:] if b":" in line)
            if not header.startswith(b"HTTP/1.1 101 ") or not any(k.lower() == b"sec-websocket-accept" and v.strip() == accept for k, v in headers.items()):
                raise ResourceIdentityError("Invalid CDP websocket handshake")
            return self
        except BaseException:
            self.writer.close()
            raise

    async def __aexit__(self, *args):
        self.writer.close()
        try:
            await asyncio.wait_for(self.writer.wait_closed(), CDP_DEADLINE_S)
        finally:
            self._url = ""

    async def _send(self, payload, opcode=1):
        mask = os.urandom(4)
        size = len(payload)
        if size > 65535 or opcode in {9, 10} and size > 125:
            raise ResourceIdentityError("Oversized CDP observation request")
        length = bytes([0x80 | size]) if size < 126 else b"\xfe" + struct.pack("!H", size)
        self.writer.write(bytes([0x80 | opcode]) + length + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        await self.writer.drain()

    async def _message(self):
        chunks = bytearray()
        for _ in range(64):
            first, second = await self.reader.readexactly(2)
            if second & 0x80 or first & 0x70:
                raise ResourceIdentityError("Invalid CDP websocket frame")
            size = second & 127
            if size in {126, 127}:
                size = struct.unpack("!H" if size == 126 else "!Q", await self.reader.readexactly(2 if size == 126 else 8))[0]
            if size + len(chunks) > 1024 * 1024:
                raise ResourceIdentityError("Oversized CDP response")
            payload = await self.reader.readexactly(size)
            opcode = first & 15
            if opcode == 9:
                await self._send(payload, 10)
                continue
            if opcode not in {0, 1}:
                raise ResourceIdentityError("Unexpected CDP websocket opcode")
            chunks.extend(payload)
            if first & 0x80:
                from src.agent_runtime.authority import _pairs, _invalid_constant
                return json.loads(chunks, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
        raise ResourceIdentityError("Unbounded CDP websocket response")

    async def call(self, method, params=None, session_id=None):
        if method not in CDP_METHODS:
            raise ResourceIdentityError("CDP method is outside the identity allowlist")
        self._counter += 1
        message = {"id": self._counter, "method": method, "params": params or {}}
        if session_id is not None:
            message["sessionId"] = session_id
        async def exchange():
            await self._send(json.dumps(message).encode())
            for _ in range(32):
                result = await self._message()
                if not isinstance(result, dict):
                    raise ResourceIdentityError("Malformed CDP identity envelope")
                if "id" in result and type(result["id"]) is not int:
                    raise ResourceIdentityError("Malformed CDP response identity")
                if result.get("id") == self._counter:
                    if "error" in result or not isinstance(result.get("result"), dict):
                        raise ResourceIdentityError("Unverifiable CDP identity response")
                    return result["result"]
            raise ResourceIdentityError("Unbounded CDP event stream")
        try:
            return await asyncio.wait_for(exchange(), CDP_DEADLINE_S)
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            raise ResourceIdentityError("CDP identity observation unavailable") from None


def tabs_schema(data):
    tabs = data.get("tabs")
    if not isinstance(tabs, list):
        raise ResourceIdentityError("Missing producer tab inventory")
    aliases, targets = set(), set()
    for row in tabs:
        if (not isinstance(row, dict) or set(row) != {"tabId", "targetId", "label", "title", "url", "type", "active"}
                or not isinstance(row.get("tabId"), str)
                or not re.fullmatch(r"t[1-9][0-9]*", row["tabId"])
                or not isinstance(row.get("targetId"), str) or not re.fullmatch(r"[A-F0-9]{32}", row["targetId"])
                or row.get("label") is not None or row.get("type") != "page"
                or type(row.get("active")) is not bool or not isinstance(row.get("url"), str)
                or not isinstance(row.get("title"), str)
                or row["tabId"] in aliases or row["targetId"] in targets):
            raise ResourceIdentityError("Malformed, labelled or ambiguous producer page")
        aliases.add(row["tabId"]); targets.add(row["targetId"])
    return tabs


async def observe_registered(record, alias=None):
    """Observe only an existing registered producer; never auto-launch/rearm.

    get cdp-url can launch when cold, so it is preceded by strict active runtime
    validation and followed by launch metadata rejection. No result reaches the
    model if the trusted observation cannot be established.
    """
    try:
        async with record.lock:
            return await _observe_registered_locked(record, alias)
    except BaseException:
        record.invalidate()
        raise


async def _observe_registered_locked(record, alias):
    try:
        first = daemon_observation(record, await record.command("session", "info"))
        endpoint = await record.command("get", "cdp-url")
        lifecycle = endpoint.get("lifecycle")
        if (not isinstance(lifecycle, dict) or any(lifecycle.get(k) is not False for k in
                ("launched", "relaunchedBrowser", "restartedBackground"))):
            raise ResourceIdentityError("Unexpected browser lifecycle launch")
        url = endpoint.get("cdpUrl")
        browser = browser_digest(url)
        values = dict(producer_namespace="native:agent-browser", producer_version=PRODUCER_VERSION,
            platform=record.producer.platform, binary_sha256=record.producer.binary_sha256,
            configuration_digest=digest("odysseus.browser.config.v1", [record.env, str(record.cwd), "{}"]),
            session_key=record.key, daemon=first.to_record(), browser_instance_digest=browser)
        observation = BrowserSessionObservation(**{**values, "daemon": first, "session_incarnation": incarnation(values)})
        session = BrowserSessionResource(record.owner, record.thread_id, observation)
        rows = tabs_schema(await record.command("tab", "list"))
        pages = []
        async with CDPSidecar(url) as cdp:
            targets = (await cdp.call("Target.getTargets")).get("targetInfos")
            if not isinstance(targets, list):
                raise ResourceIdentityError("Missing CDP target inventory")
            for row in rows:
                # Never select a page by targetId: even read dispatch is disabled.
                target = row["targetId"]
                if not any(t.get("targetId") == target and t.get("type") == "page" for t in targets if isinstance(t, dict)):
                    raise ResourceIdentityError("Producer/CDP target disagreement")
                attached = await cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True})
                sid = attached.get("sessionId")
                if not isinstance(sid, str) or not sid:
                    raise ResourceIdentityError("Missing CDP observation session")
                try:
                    tree = await cdp.call("Page.getFrameTree", session_id=sid)
                    frame = tree.get("frameTree", {}).get("frame", {})
                    if frame.get("id") != target or not isinstance(frame.get("loaderId"), str) or not frame["loaderId"]:
                        raise ResourceIdentityError("Unsupported main-frame/document invariant")
                    pages.append(BrowserPageResource(session, target, frame["loaderId"], row["tabId"], row["url"]))
                    info = (await cdp.call("Target.getTargetInfo", {"targetId": target})).get("targetInfo", {})
                    if info.get("targetId") != target or info.get("type") != "page":
                        raise ResourceIdentityError("Page disappeared during observation")
                finally:
                    await cdp.call("Target.detachFromTarget", {"sessionId": sid})
        last = daemon_observation(record, await record.command("session", "info"))
        final = await record.command("get", "cdp-url")
        if first != last or not first.owned() or browser_digest(final.get("cdpUrl")) != browser:
            raise ResourceIdentityError("Browser incarnation changed during observation")
        final_lifecycle = final.get("lifecycle", {})
        if any(final_lifecycle.get(k) is not False for k in ("launched", "relaunchedBrowser", "restartedBackground")):
            raise ResourceIdentityError("Unexpected browser replacement")
        if record.session != session:
            record.invalidate()
        record.session, record.pages = session, tuple(pages)
        record._endpoint = url
        if alias is not None:
            match = [p for p in pages if p.resolved_alias == alias]
            if len(match) != 1:
                raise ResourceIdentityError("Unresolved browser alias")
            return match[0]
        return session
    except BaseException:
        record.invalidate()
        raise


def validate_session(resource):
    record = registered(resource.owner, resource.thread_id)
    if record is None or record.session != resource or not resource.observation.daemon.owned():
        raise ResourceIdentityError("Browser observation is stale, replaced or unregistered")
    record.validate_config()


def validate_page(resource):
    resource.session.validate()
    record = registered(resource.session.owner, resource.session.thread_id)
    if not any(p.target_id == resource.target_id and (resource.scope == "page" or p.loader_id == resource.loader_id) for p in record.pages):
        raise ResourceIdentityError("Browser page/document observation changed")


def seal_browser_resources(authority):
    record = registered(authority.owner, authority.session_id)
    if record is None or record.session is None or not any(g.tool == "private_browser" for g in authority.grants):
        return (), ()
    try:
        record.session.validate()
    except ResourceIdentityError:
        return (), ()
    return (record.session,), record.pages


def intersect_browser(parent_sessions, parent_pages, child_sessions, child_pages):
    # Validate old observations before considering anything newly observed.
    for item in (*parent_sessions, *parent_pages, *child_sessions, *child_pages):
        item.validate()
    sessions = tuple(s for s in parent_sessions if s in child_sessions)
    pages = []
    for p in parent_pages:
        for c in child_pages:
            if p.session == c.session and p.target_id == c.target_id and (p.scope == "page" or p.loader_id == c.loader_id):
                pages.append(c if p.scope == "page" else replace(c, loader_id=p.loader_id, scope="document"))
    return sessions, tuple(pages)


@dataclass(frozen=True)
class BoundBrowserOperation:
    operation: Any
    request_id: str
    owner: str
    thread_id: str
    session: BrowserSessionResource
    page: BrowserPageResource | None = None
    exact_approval: Any = None

    def validate(self):
        if (self.session.owner, self.session.thread_id) != (self.owner, self.thread_id) or not self.request_id:
            raise ResourceIdentityError("Browser application binding changed")
        operation, args = parse_operation(self.operation.input)
        if operation != self.operation or self.operation.tool != "private_browser":
            raise ResourceIdentityError("Browser normalized operation changed")
        self.session.validate()
        if self.page is not None:
            if self.page.session != self.session:
                raise ResourceIdentityError("Browser page/session binding changed")
            self.page.validate()
        if args["action"] not in SESSION_ACTIONS and self.page is None:
            raise ResourceIdentityError("Missing proposal-bound page observation")

    def to_dict(self):
        return {"operation": {"tool": self.operation.tool, "input": self.operation.input,
                "action": self.operation.action, "transport_tool": self.operation.transport_tool},
                "request_id": self.request_id, "owner": self.owner, "thread_id": self.thread_id,
                "session": self.session.to_dict(), "page": self.page.to_dict() if self.page else None}


def resolve_browser_operation(authority, operation, *, approved=None, exact_admission=False):
    _, args = parse_operation(operation.input)
    if approved is not None:
        bound = approved
        if (bound.operation != operation or (bound.request_id, bound.owner, bound.thread_id) !=
                (authority.request_id, authority.owner, authority.session_id)):
            raise ResourceIdentityError("Approved browser operation binding changed")
    else:
        record = registered(authority.owner, authority.session_id)
        if record is None or record.session is None:
            raise ResourceIdentityError("No admitted browser session observation")
        page = None
        if args["action"] not in SESSION_ACTIONS:
            alias = args.get("page")
            matches = [p for p in record.pages if alias and p.resolved_alias == alias]
            if len(matches) != 1:
                raise ResourceIdentityError("An observed tN selector is required")
            page = matches[0]  # Alias is audit metadata after this single resolution.
        bound = BoundBrowserOperation(operation, authority.request_id, authority.owner,
            authority.session_id, record.session, page)
    bound.validate()
    if not (approved is not None and exact_admission and not authority.inherited):
        if bound.page is None and bound.session not in authority.browser_sessions:
            raise ResourceIdentityError("Browser session is outside admitted scope")
        if bound.page is not None and not any(p.session == bound.page.session and p.target_id == bound.page.target_id
                and (p.scope == "page" or p.loader_id == bound.page.loader_id) for p in authority.browser_pages):
            raise ResourceIdentityError("Browser page/document is outside admitted scope")
    return bound


async def revalidate_browser_operation(bound):
    bound.validate()
    record = registered(bound.owner, bound.thread_id)
    async with record.lock:
        try:
            # The existing capability connects to the captured browser only.
            # Never issue get cdp-url here: its CLI can auto-launch a replacement.
            if daemon_observation(record, await record.command("session", "info")) != bound.session.observation.daemon:
                raise ResourceIdentityError("Browser proposal daemon replaced")
            if browser_digest(record._endpoint) != bound.session.observation.browser_instance_digest:
                raise ResourceIdentityError("Browser proposal incarnation replaced")
            async with CDPSidecar(record._endpoint) as cdp:
                await cdp.call("Target.getTargets")
            bound.validate()
        except BaseException:
            record.invalidate()
            raise


@contextmanager
def bind_browser_operation(bound):
    if bound is not None:
        bound.validate()
    token = _ACTIVE.set(bound)
    try:
        yield bound
    finally:
        _ACTIVE.reset(token)


async def execute_browser(content, ctx):
    try:
        operation, args = parse_operation(content)
        # Unconditional capability denial, before producer selection, alias
        # lookup, spawning, approval claims or any page-specific data read.
        if args["action"] not in SESSION_ACTIONS:
            return page_unavailable()
        from src.agent_runtime.authority import active_request_authority
        authority, bound = active_request_authority(), _ACTIVE.get()
        if authority is None or bound is None or bound.operation != operation:
            raise ResourceIdentityError("Browser producer requires a normalized resource-bound operation")
        if (authority.owner, authority.request_id, authority.session_id) != (bound.owner, bound.request_id, bound.thread_id):
            raise ResourceIdentityError("Browser caller authority changed")
        if (str(ctx.get("owner") or "").casefold(), str(ctx.get("session_id") or "")) != (bound.owner, bound.thread_id):
            raise ResourceIdentityError("Browser producer caller changed")
        if not authority.permits(operation):
            approval = bound.exact_approval
            if (authority.inherited or approval is None or not approval._claimed
                    or approval.pending.browser_operation is None or approval.pending.browser_operation.to_dict() != bound.to_dict()):
                raise ResourceIdentityError("Browser operation lacks exact admission")
        bound.validate()
        record = registered(bound.owner, bound.thread_id)
        await revalidate_browser_operation(bound)
        async with record.lock:
            bound.validate()
            # Metadata only. Never return URL/title/content, raw CDP capability,
            # or producer lifecycle data as semantic verification.
            output = {"session_incarnation": bound.session.observation.session_incarnation,
                      "producer_version": PRODUCER_VERSION}
            return {"output": json.dumps(output), "exit_code": 0, "executed": True,
                    "browser_page_operations_supported": False}
    except asyncio.CancelledError:
        record = registered(str(ctx.get("owner") or "").casefold(), str(ctx.get("session_id") or ""))
        if record is not None:
            record.invalidate()
        raise
    except Exception:
        # No raw producer/CDP exception text: it can contain capability URLs.
        return {"error": "Trusted browser session metadata is unavailable.", "exit_code": 1,
                "executed": False, "retryable": False, "failure_kind": "browser_session_authority_unavailable"}
