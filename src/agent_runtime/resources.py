"""Inert server-owned resource identities, independent of operation authority.

Filesystem observations detect replacement; they are not held kernel handles or
content/effect evidence. Other producers must supply their own incarnations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import os
from pathlib import Path
import stat
import sys

from src.agent_runtime.path_policy import _is_sensitive_path
from src.path_confinement import canonical_root, confine


def _text(value, label, *, optional=False):
    if (not isinstance(value, str) or (not value and not optional)
            or any(c in value for c in ("\0", "\n", "\r"))):
        raise ValueError(f"Invalid resource {label}")


def _absolute(value):
    _text(value, "path")
    if not os.path.isabs(value) or os.path.normpath(value) != value:
        raise ValueError("Resource path must be canonical and absolute")


def _effect_store_dirs():
    from src import constants
    directories = {canonical_root(os.path.join(constants.DATA_DIR, "effects"))}
    effect_log = sys.modules.get("src.agent_runtime.effect_log")
    if effect_log is not None:
        directories.add(canonical_root(effect_log.EFFECTS_DIR))
    return directories


def _aliases_effect_store(candidate, directories):
    """Whether ``candidate`` (an ``os.stat`` result) is a hardlink into the effect store.

    The effect log and launch index refuse any file with more than one link,
    and the store is flat. So only a multiply linked regular file on the
    store's device can alias store state, and only then is the store listed,
    one directory level, by inode. Ordinary single-link files cost nothing,
    and the cost never depends on recursive store size. Uninspectable store
    state fails closed.
    """
    if not stat.S_ISREG(candidate.st_mode) or candidate.st_nlink < 2:
        return False
    for directory in directories:
        try:
            if os.stat(directory).st_dev != candidate.st_dev:
                continue
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.inode() != candidate.st_ino:
                        continue
                    observed = entry.stat(follow_symlinks=False)
                    if (observed.st_dev, observed.st_ino) == (candidate.st_dev, candidate.st_ino):
                        return True
        except FileNotFoundError:
            continue
        except OSError:
            return True
    return False


def _control_plane_snapshot():
    # Execution snapshots/receipts are server state, even if a workspace root
    # contains the data directory. A writable user file cannot mint authority.
    from src import constants
    protected = {canonical_root(getattr(constants, name)) for name in (
        "BG_JOBS_FILE", "CONTAINMENT_STATE_FILE", "APP_DB", "AUTH_FILE",
        "SETTINGS_FILE", "SESSIONS_FILE", "USER_PREFS_FILE", "VAULT_FILE",
        "SCHEDULED_EMAILS_DB", "EMAIL_CACHE_DB", "MEMORY_FILE", "INTEGRATIONS_FILE",
    )}
    job_dirs = {canonical_root(constants.BG_JOBS_DIR), canonical_root(constants.PROCESS_RESOURCES_DIR),
                canonical_root(constants.BROWSER_RESOURCES_DIR)}
    browser = sys.modules.get("src.browser_identity")
    if browser is not None:
        job_dirs.add(canonical_root(browser.STATE_ROOT))
    processes = sys.modules.get("src.agent_runtime.process_resources")
    if processes is not None:
        job_dirs.add(canonical_root(processes._LAUNCH_DIR))
    # Durable effect claims/outcomes/observations are server evidence state.
    # They are prefix-protected below, but never inventoried: the store grows
    # with every run. Hardlink aliases are caught by ``_aliases_effect_store``.
    effect_dirs = _effect_store_dirs()
    # Producers may have configured paths different from the default constants.
    # Inspect already-loaded server metadata without initializing a store here.
    bg = sys.modules.get("src.bg_jobs")
    if bg is not None:
        for name, targets in (("_STORE", protected), ("_JOBS_DIR", job_dirs)):
            value = getattr(bg, name, None)
            if isinstance(value, (str, os.PathLike)):
                targets.add(canonical_root(value))
    containment = sys.modules.get("src.containment")
    if containment is not None:
        value = containment._store_path()
        if isinstance(value, (str, os.PathLike)):
            protected.add(canonical_root(value))
    database = sys.modules.get("core.database")
    url = getattr(getattr(database, "engine", None), "url", None)
    if url is not None and url.get_backend_name() == "sqlite":
        location = url.database
        if isinstance(location, str) and location not in {"", ":memory:"}:
            from urllib.parse import unquote
            if location.startswith("file:"):
                location = unquote(location[5:].split("?", 1)[0])
            protected.update(canonical_root(location + suffix) for suffix in ("", "-wal", "-shm", "-journal"))
    from src.tool_utils import get_upload_handler
    uploader = get_upload_handler()
    if uploader is not None and isinstance(getattr(uploader, "upload_dir", None), (str, os.PathLike)):
        protected.add(canonical_root(Path(uploader.upload_dir) / "uploads.json"))
    # Prefix protection covers the complete runtime trees. Public policy denies
    # every regular hardlink alias, so collecting all child inodes here would
    # add an unbounded recursive state inventory without widening protection.
    protected.update(canonical_root(getattr(constants, name) + suffix)
                     for name in ("APP_DB", "SCHEDULED_EMAILS_DB", "EMAIL_CACHE_DB")
                     for suffix in ("-wal", "-shm", "-journal"))
    protected.add(canonical_root(Path(constants.DATA_DIR) / ".app_key"))
    protected.add(canonical_root(Path(constants.UPLOAD_DIR) / "uploads.json"))
    identities = set()
    for control in protected:
        try:
            observed = os.stat(control)
        except FileNotFoundError:
            continue
        identities.add((observed.st_dev, observed.st_ino))
    # Effect directories are protected by prefix without inventorying children.
    return frozenset(job_dirs | effect_dirs), frozenset(protected), frozenset(identities)


def _control_plane_path(path, *, snapshot=None):
    # A scan-local snapshot bounds repeated hardlink checks. Ordinary resource
    # resolution always observes fresh state. Neither form is an atomic kernel
    # access policy, and snapshots must never survive a workspace guard call.
    # Public state and hardlink denial also applies to sealed resources. Lazy
    # import avoids coupling inert identity definitions to dispatcher startup.
    from src.tool_execution import _is_app_state_path, _is_hardlinked_regular_file
    if _is_sensitive_path(path) or _is_app_state_path(path) or _is_hardlinked_regular_file(path):
        return True
    directories, protected, identities = _control_plane_snapshot() if snapshot is None else snapshot
    if any(Path(path).is_relative_to(directory) for directory in directories) or path in protected:
        return True
    try:
        candidate = os.stat(path)
    except FileNotFoundError:
        return False
    if (candidate.st_dev, candidate.st_ino) in identities:
        return True
    # Only a multiply linked file can alias the (uninventoried) effect store.
    return candidate.st_nlink > 1 and _aliases_effect_store(candidate, directories & _effect_store_dirs())


class FilesystemScope(str, Enum):
    WORKSPACE = "workspace"
    SCRATCH = "scratch"
    EXTERNAL = "external"
    PRIVATE = "private"


class ResourceIdentityError(ValueError):
    """An observed execution resource has changed or cannot be resolved."""


@dataclass(frozen=True)
class BrowserSessionObservation:
    producer_namespace: str
    producer_version: str
    platform: str
    binary_sha256: str
    configuration_digest: str
    session_key: str
    daemon: "ProcessIdentity"
    browser_instance_digest: str
    session_incarnation: str

    def __post_init__(self):
        from src.process_lifecycle import ProcessIdentity
        from src.browser_identity import PRODUCER_HASHES, incarnation
        if (self.producer_namespace != "native:agent-browser"
                or self.producer_version != "0.35.0"
                or PRODUCER_HASHES.get(self.platform) != self.binary_sha256
                or not isinstance(self.daemon, ProcessIdentity)
                or type(self.daemon.pid) is not int or self.daemon.pid <= 0
                or (self.daemon.pgid is not None and (type(self.daemon.pgid) is not int or self.daemon.pgid <= 0))):
            raise ValueError("Unsupported browser producer observation")
        import re
        _text(self.daemon.start_token, "daemon incarnation")
        if not re.fullmatch(r"ody-[a-f0-9]{24}", self.session_key):
            raise ValueError("Malformed browser session selector")
        for value in (self.configuration_digest, self.browser_instance_digest, self.session_incarnation):
            if not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError("Malformed browser digest")
        if incarnation(self) != self.session_incarnation:
            raise ValueError("Browser incarnation digest changed")

    def to_dict(self):
        return {**asdict(self), "daemon": self.daemon.to_record()}

    @classmethod
    def from_dict(cls, value):
        from src.process_lifecycle import ProcessIdentity
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("Malformed browser observation snapshot")
        daemon = value["daemon"]
        if not isinstance(daemon, dict) or set(daemon) != {"pid", "start_token", "pgid"}:
            raise ValueError("Malformed browser daemon observation")
        return cls(**{**value, "daemon": ProcessIdentity(**daemon)})


@dataclass(frozen=True)
class BrowserSessionResource:
    owner: str
    thread_id: str
    observation: BrowserSessionObservation

    def __post_init__(self):
        _text(self.owner, "browser owner")
        _text(self.thread_id, "browser thread")
        if not isinstance(self.observation, BrowserSessionObservation):
            raise ValueError("Missing browser session observation")

    def validate(self):
        from src.browser_identity import validate_session
        validate_session(self)

    def to_dict(self):
        return {"owner": self.owner, "thread_id": self.thread_id, "observation": self.observation.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"owner", "thread_id", "observation"}:
            raise ValueError("Malformed browser resource snapshot")
        return cls(value["owner"], value["thread_id"], BrowserSessionObservation.from_dict(value["observation"]))


@dataclass(frozen=True)
class BrowserPageResource:
    session: BrowserSessionResource
    target_id: str
    loader_id: str
    resolved_alias: str = ""
    observed_url: str = ""
    scope: str = "document"

    def __post_init__(self):
        import re
        if not isinstance(self.session, BrowserSessionResource) or not re.fullmatch(r"[A-F0-9]{32}", self.target_id):
            raise ValueError("Malformed browser page identity")
        if self.scope not in {"page", "document"}:
            raise ValueError("Malformed browser page scope")
        _text(self.loader_id, "document loader", optional=self.scope == "page")
        _text(self.observed_url, "observed URL", optional=True)
        if self.resolved_alias and not re.fullmatch(r"t[1-9][0-9]*", self.resolved_alias):
            raise ValueError("Malformed browser alias metadata")

    def authority_key(self):
        return (self.session, self.target_id, self.loader_id if self.scope == "document" else None)

    def validate(self):
        from src.browser_identity import validate_page
        validate_page(self)

    def to_dict(self):
        return {**asdict(self), "session": self.session.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("Malformed browser page snapshot")
        return cls(**{**value, "session": BrowserSessionResource.from_dict(value["session"])})


@dataclass(frozen=True)
class FileObjectIdentity:
    device: int
    inode: int
    kind: str

    def __post_init__(self):
        if (type(self.device) is not int or self.device < 0
                or type(self.inode) is not int or self.inode <= 0
                or self.kind not in {"file", "directory"}):
            raise ValueError("Malformed filesystem object identity")

    @classmethod
    def observe(cls, path):
        info = os.stat(path, follow_symlinks=False)
        kind = ("file" if stat.S_ISREG(info.st_mode) else
                "directory" if stat.S_ISDIR(info.st_mode) else None)
        if kind is None:
            raise ValueError("Filesystem resource must be a regular file or directory")
        return cls(info.st_dev, info.st_ino, kind)


@dataclass(frozen=True)
class FilesystemRoot:
    path: str
    scope: FilesystemScope
    identity: FileObjectIdentity
    owner: str = ""

    def __post_init__(self):
        _absolute(self.path)
        _text(self.owner, "owner", optional=True)
        if (not isinstance(self.scope, FilesystemScope)
                or not isinstance(self.identity, FileObjectIdentity)
                or self.identity.kind != "directory"
                or os.path.dirname(self.path) == self.path
                or _is_sensitive_path(self.path)
                or (self.scope is FilesystemScope.PRIVATE and not self.owner)):
            raise ValueError("Malformed filesystem root identity")

    @classmethod
    def seal(cls, path, *, scope=FilesystemScope.WORKSPACE, owner=""):
        root = canonical_root(path)
        return cls(root, scope, FileObjectIdentity.observe(root), owner)

    def validate(self):
        try:
            if canonical_root(self.path) != self.path or FileObjectIdentity.observe(self.path) != self.identity:
                raise ResourceIdentityError("Filesystem root identity changed")
        except (OSError, RuntimeError) as error:
            raise ResourceIdentityError("Filesystem root identity is unresolved") from error

    def to_dict(self):
        return {**asdict(self), "scope": self.scope.value}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"path", "scope", "identity", "owner"}:
            raise ValueError("Malformed filesystem root snapshot")
        return cls(value["path"], FilesystemScope(value["scope"]),
                   FileObjectIdentity(**value["identity"]), value["owner"])


@dataclass(frozen=True)
class PathObservation:
    path: str
    identity: FileObjectIdentity

    def __post_init__(self):
        _absolute(self.path)
        if not isinstance(self.identity, FileObjectIdentity) or self.identity.kind != "directory":
            raise ValueError("Malformed filesystem ancestor identity")


@dataclass(frozen=True)
class FilesystemResource:
    root: FilesystemRoot
    path: str
    identity: FileObjectIdentity | None
    ancestors: tuple[PathObservation, ...]

    def __post_init__(self):
        _absolute(self.path)
        if (not isinstance(self.root, FilesystemRoot)
                or not Path(self.path).is_relative_to(self.root.path)
                or (self.identity is not None and not isinstance(self.identity, FileObjectIdentity))
                or not isinstance(self.ancestors, tuple)
                or any(not isinstance(a, PathObservation) for a in self.ancestors)
                or not self.ancestors
                or self.ancestors[0] != PathObservation(self.root.path, self.root.identity)):
            raise ValueError("Malformed filesystem resource identity")
        parent = Path(self.root.path)
        expected = [str(parent)]
        for part in Path(self.path).relative_to(self.root.path).parts[:-1]:
            parent /= part
            expected.append(str(parent))
        if ([a.path for a in self.ancestors] != expected[:len(self.ancestors)]
                or (self.identity is not None and len(self.ancestors) != len(expected))):
            raise ValueError("Malformed filesystem ancestor chain")

    @classmethod
    def resolve(cls, root, selector, *, allow_missing=False):
        root.validate()
        # Only this server-owned workspace root supplies the virtual alias.
        if not isinstance(selector, str):
            raise ValueError("Resource path must be a string")
        value = selector.strip()
        if root.scope is FilesystemScope.WORKSPACE:
            if value == "/workspace":
                value = root.path
            elif value.startswith("/workspace/"):
                value = os.path.join(root.path, value[len("/workspace/"):])
        path = confine(root.path, value)
        if _is_sensitive_path(path) or _control_plane_path(path):
            raise ValueError("Resource path is sensitive")
        ancestors = [PathObservation(root.path, root.identity)]
        relative = Path(path).relative_to(root.path)
        parent = Path(root.path)
        missing_parent = False
        for part in relative.parts[:-1]:
            parent /= part
            try:
                observed = FileObjectIdentity.observe(parent)
            except FileNotFoundError:
                missing_parent = True
                break
            ancestors.append(PathObservation(str(parent), observed))
        try:
            identity = None if missing_parent else FileObjectIdentity.observe(path)
        except FileNotFoundError:
            identity = None
        if identity is None and not allow_missing:
            raise ValueError("Filesystem resource is unresolved or missing")
        return cls(root, path, identity, tuple(ancestors))

    def validate(self):
        try:
            if self.resolve(self.root, self.path, allow_missing=self.identity is None) != self:
                raise ResourceIdentityError("Filesystem resource identity changed")
        except (ValueError, OSError, RuntimeError) as error:
            raise ResourceIdentityError("Filesystem resource identity changed or is unresolved") from error

    def to_dict(self):
        return asdict(self)


def intersect_roots(parent, child):
    """Keep the narrower root only when the observed parent's identity agrees."""
    result = []
    for left in parent:
        for right in child:
            if (left.scope, left.owner) != (right.scope, right.owner):
                continue
            try:
                left.validate()
                right.validate()
                if left == right:
                    result.append(left)
                    continue
                if Path(right.path).is_relative_to(left.path):
                    # A newly sealed child may not renew a replaced parent root.
                    result.append(right)
                elif Path(left.path).is_relative_to(right.path):
                    result.append(left)
            except (OSError, ValueError, RuntimeError):
                continue
    return tuple(dict.fromkeys(result))


@dataclass(frozen=True)
class ProcessResource:
    namespace: str
    owner: str
    request_id: str
    thread_id: str
    identity: "ProcessIdentity"
    role: str
    job_id: str = ""
    containment_id: str = ""

    def __post_init__(self):
        from src.process_lifecycle import ProcessIdentity
        for name in ("namespace", "request_id", "thread_id"):
            _text(getattr(self, name), name)
        for name in ("owner", "job_id", "containment_id"):
            _text(getattr(self, name), name, optional=True)
        if (not isinstance(self.identity, ProcessIdentity)
                or type(self.identity.pid) is not int or self.identity.pid <= 0
                or (self.identity.pgid is not None and (type(self.identity.pgid) is not int or self.identity.pgid <= 0))
                or self.role not in {"supervisor", "leader", "namespace_init", "manager", "pty", "service"}):
            raise ValueError("Malformed process resource identity")
        supported_roles = {"native:containment": {"leader", "namespace_init"},
                           "native:bg_jobs": {"supervisor"}}
        if self.role not in supported_roles.get(self.namespace, set()):
            raise ValueError("Unsupported process producer or role")
        _text(self.identity.start_token, "process start token")

    def validate(self):
        if not self.identity.owned() or self.identity.exited():
            raise ResourceIdentityError("Process resource is stale or unverifiable")

    def to_dict(self):
        return {"namespace": self.namespace, "owner": self.owner, "request_id": self.request_id,
                "thread_id": self.thread_id, "identity": self.identity.to_record(), "role": self.role,
                "job_id": self.job_id, "containment_id": self.containment_id}

    @classmethod
    def from_dict(cls, value):
        from src.process_lifecycle import ProcessIdentity
        if not isinstance(value, dict) or set(value) != {"namespace", "owner", "request_id", "thread_id", "identity", "role", "job_id", "containment_id"}:
            raise ValueError("Malformed process resource snapshot")
        identity = value["identity"]
        if not isinstance(identity, dict) or set(identity) != {"pid", "start_token", "pgid"}:
            raise ValueError("Malformed lifecycle identity snapshot")
        return cls(**{**value, "identity": ProcessIdentity(**identity)})


@dataclass(frozen=True)
class ProcessLaunchScope:
    backend: "NativeBackendResource"
    root: FilesystemRoot
    required: frozenset[str]
    runtime_roots: tuple[PathObservation, ...] = ()
    network: str = "inherit"
    max_runtime_s: int = 3600

    def __post_init__(self):
        if (not isinstance(self.backend, NativeBackendResource) or not isinstance(self.root, FilesystemRoot)
                or not isinstance(self.required, frozenset) or not self.required
                or any(not isinstance(v, str) or not v for v in self.required)):
            raise ValueError("Malformed process launch scope")
        if self.backend.tool_id not in {"bash", "python"}:
            raise ValueError("Unsupported native launch producer")
        if (not isinstance(self.runtime_roots, tuple) or any(not isinstance(r, PathObservation) for r in self.runtime_roots)
                or self.network not in {"inherit", "none"}
                or type(self.max_runtime_s) is not int or self.max_runtime_s <= 0):
            raise ValueError("Malformed launch boundary selectors")

    def validate(self):
        self.root.validate()
        for runtime in self.runtime_roots:
            if canonical_root(runtime.path) != runtime.path or FileObjectIdentity.observe(runtime.path) != runtime.identity:
                raise ResourceIdentityError("Launch runtime root changed")

    def to_dict(self):
        return {"backend": self.backend.to_dict(), "root": self.root.to_dict(), "required": sorted(self.required),
                "runtime_roots": [{"path": r.path, "identity": asdict(r.identity)} for r in self.runtime_roots],
                "network": self.network, "max_runtime_s": self.max_runtime_s}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"backend", "root", "required", "runtime_roots", "network", "max_runtime_s"} or not isinstance(value["required"], list) or not isinstance(value["runtime_roots"], list):
            raise ValueError("Malformed launch scope snapshot")
        return cls(backend_from_dict(value["backend"]), FilesystemRoot.from_dict(value["root"]), frozenset(value["required"]),
                   tuple(PathObservation(r["path"], FileObjectIdentity(**r["identity"])) for r in value["runtime_roots"]),
                   value["network"], value["max_runtime_s"])


@dataclass(frozen=True)
class ProcessLaunchResource:
    namespace: str
    owner: str
    request_id: str
    thread_id: str
    generation: str
    tool: str
    input_digest: str
    scope: ProcessLaunchScope
    ceiling_digest: str

    def __post_init__(self):
        for name in ("namespace", "request_id", "thread_id", "generation", "tool", "input_digest", "ceiling_digest"):
            _text(getattr(self, name), name)
        _text(self.owner, "owner", optional=True)
        if not isinstance(self.scope, ProcessLaunchScope) or self.tool != self.scope.backend.tool_id:
            raise ValueError("Malformed launch resource")
        import re
        if (self.namespace != "native:containment" or not re.fullmatch(r"[a-f0-9]{32}", self.generation)
                or any(not re.fullmatch(r"[a-f0-9]{64}", v) for v in (self.input_digest, self.ceiling_digest))):
            raise ValueError("Malformed native launch producer or generation")

    def validate(self):
        self.scope.validate()

    def to_dict(self):
        return {**{k: getattr(self, k) for k in ("namespace", "owner", "request_id", "thread_id", "generation", "tool", "input_digest", "ceiling_digest")},
                "scope": self.scope.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"namespace", "owner", "request_id", "thread_id", "generation", "tool", "input_digest", "scope", "ceiling_digest"}:
            raise ValueError("Malformed launch resource snapshot")
        return cls(**{**value, "scope": ProcessLaunchScope.from_dict(value["scope"])})


@dataclass(frozen=True)
class BackgroundJobResource:
    namespace: str
    job_id: str
    generation: str
    owner: str
    request_id: str
    thread_id: str
    containment_id: str
    processes: tuple[ProcessResource, ...]

    def __post_init__(self):
        for name in ("namespace", "job_id", "generation", "request_id", "thread_id", "containment_id"):
            _text(getattr(self, name), name)
        _text(self.owner, "owner", optional=True)
        import re
        if (not re.fullmatch(r"[A-Za-z0-9_-]+", self.job_id)
                or not re.fullmatch(r"[a-f0-9]{32}", self.generation)):
            raise ValueError("Malformed job selector or launch generation")
        if (not isinstance(self.processes, tuple) or not self.processes
                or any(not isinstance(p, ProcessResource) or (p.owner, p.request_id, p.thread_id, p.job_id, p.containment_id)
                       != (self.owner, self.request_id, self.thread_id, self.job_id, self.containment_id) for p in self.processes)
                or len({p.role for p in self.processes}) != len(self.processes)):
            raise ValueError("Malformed background job resource")
        if self.namespace != "native:bg_jobs" or any(p.namespace != "native:bg_jobs" or p.role != "supervisor" for p in self.processes):
            raise ValueError("Unsupported job producer or process role")

    def to_dict(self):
        return {**{k: getattr(self, k) for k in ("namespace", "job_id", "generation", "owner", "request_id", "thread_id", "containment_id")},
                "processes": [p.to_dict() for p in self.processes]}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"namespace", "job_id", "generation", "owner", "request_id", "thread_id", "containment_id", "processes"} or not isinstance(value["processes"], list):
            raise ValueError("Malformed background resource snapshot")
        return cls(**{**value, "processes": tuple(ProcessResource.from_dict(p) for p in value["processes"])})


@dataclass(frozen=True)
class ExternalResource:
    namespace: str
    endpoint_id: str
    server_id: str
    tool_id: str
    incarnation: str
    external: bool = True
    contained: bool = False
    owner: str = ""

    def __post_init__(self):
        for name in ("namespace", "endpoint_id", "server_id", "tool_id", "incarnation"):
            _text(getattr(self, name), name)
        if self.external is not True or self.contained is not False:
            raise ValueError("External resource cannot attest local containment")
        _text(self.owner, "external owner", optional=True)

    def to_dict(self):
        return {"kind": "external", **asdict(self)}


@dataclass(frozen=True)
class NativeBackendResource:
    tool_id: str
    namespace: str = "native"
    external: bool = False
    contained: bool = False

    def __post_init__(self):
        _text(self.tool_id, "native tool")
        if self.namespace != "native" or self.external is not False or self.contained is not False:
            raise ValueError("Malformed native backend identity")

    def to_dict(self):
        return {"kind": "native", **asdict(self)}


def backend_from_dict(value):
    if not isinstance(value, dict):
        raise ValueError("Malformed backend snapshot")
    fields = dict(value)
    kind = fields.pop("kind", None)
    if kind not in {"native", "external"}:
        raise ValueError("Malformed backend kind")
    return (NativeBackendResource if kind == "native" else ExternalResource)(**fields)


@dataclass(frozen=True)
class OwnedResource:
    namespace: str
    owner: str
    thread_id: str
    collection: str
    record_id: str
    revision: str = ""
    record_thread_id: str = ""

    def __post_init__(self):
        for name in ("namespace", "owner", "thread_id", "collection", "record_id"):
            _text(getattr(self, name), name)
        _text(self.revision, "revision", optional=True)
        _text(self.record_thread_id, "record thread", optional=True)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class OwnedScope:
    namespace: str
    owner: str
    thread_id: str
    record_ids: frozenset[str] | None = None

    def __post_init__(self):
        for name in ("namespace", "owner", "thread_id"):
            _text(getattr(self, name), name)
        if self.record_ids is not None:
            if not isinstance(self.record_ids, frozenset):
                raise ValueError("Owned scope must be immutable")
            for identifier in self.record_ids:
                _text(identifier, "record identifier")
                if identifier == "*":
                    raise ValueError("Collection authority must be explicit")

    def permits(self, resource):
        return (isinstance(resource, OwnedResource)
                and (self.namespace, self.owner, self.thread_id) ==
                    (resource.namespace, resource.owner, resource.thread_id)
                and resource.collection == self.namespace
                and (self.record_ids is None or resource.record_id in self.record_ids))

    def intersect(self, other):
        if (self.namespace, self.owner, self.thread_id) != (other.namespace, other.owner, other.thread_id):
            return None
        ids = (other.record_ids if self.record_ids is None else self.record_ids if other.record_ids is None
               else self.record_ids & other.record_ids)
        return OwnedScope(self.namespace, self.owner, self.thread_id, ids)

    def to_dict(self):
        return {"namespace": self.namespace, "owner": self.owner, "thread_id": self.thread_id,
                "record_ids": None if self.record_ids is None else sorted(self.record_ids)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"namespace", "owner", "thread_id", "record_ids"}:
            raise ValueError("Malformed owned scope snapshot")
        ids = value["record_ids"]
        if ids is not None and (not isinstance(ids, list) or any(not isinstance(v, str) for v in ids)):
            raise ValueError("Malformed owned record limits")
        return cls(value["namespace"], value["owner"], value["thread_id"],
                   None if ids is None else frozenset(ids))


OWNED_TOOL_NAMESPACES = {
    **{name: "documents" for name in ("create_document", "edit_document", "update_document", "suggest_document", "manage_documents")},
    **{name: "threads" for name in ("create_session", "list_sessions", "manage_session", "send_to_session", "search_chats")},
    **{name: "attachments" for name in ("extract_text", "inspect_media", "transcribe_media")},
    "manage_notes": "notes",
    "manage_memory": "memory",
    **{name: "vault" for name in ("vault_get", "vault_search", "vault_unlock")},
}


def seal_owned_scopes(owner, thread_id, tools):
    if not owner or not thread_id:
        return ()
    return tuple(OwnedScope(namespace, owner, thread_id)
                 for namespace in sorted({OWNED_TOOL_NAMESPACES[t] for t in tools if t in OWNED_TOOL_NAMESPACES}))
