"""Durable append-only effect log for one root run lineage.

This is the Wave 4 semantic store: claims, outcomes and observations only. It
is not a resource database, a process/containment store or an authority source.
A claim is fsynced before the backend is invoked; if that fails, the caller must
refuse the invocation. Later records are appended; nothing is rewritten.

On reload, a claim without a settled outcome becomes an appended INTERRUPTED
outcome with possible impact. Reload never manufactures success and never
upgrades an old report to fresh state.

Several writers may append to one log (another ``EffectLog`` object, thread or
process settling a background launch). Each append takes an exclusive advisory
lock on the file, merges every durable record other writers appended, allocates
the next position from that merged tail, checks the record against the merged
history, then appends and fsyncs before releasing the lock. Positions therefore
stay unique and a settled outcome is never appended twice. Cross-process
exclusion relies on POSIX ``flock``; directory fsync relies on POSIX directory
semantics. Neither is claimed where the platform does not provide it.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import threading
import weakref
from typing import Any, Callable

try:  # POSIX only; elsewhere exclusion is per process.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX hosts
    fcntl = None

from src.constants import DATA_DIR
from src.agent_runtime.effects import (
    EffectAssessment, EffectClaim, EffectHistory, EffectOutcome, ExecutionOutcome, Observation, assess_all,
    replay_interrupted,
)
from src.agent_runtime.resources import ResourceIdentityError


EFFECTS_DIR = os.path.join(DATA_DIR, "effects")
_RUN_ID = re.compile(r"[a-f0-9]{32}")
_TYPES = {"claim": EffectClaim, "outcome": EffectOutcome, "observation": Observation}
_VERSION = 1


def _fsync_directory(directory: str | os.PathLike) -> None:
    """Make a directory's entries durable. POSIX only; a no-op elsewhere."""
    if os.name != "posix":
        return
    descriptor = os.open(os.fspath(directory), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_directory(directory: Path) -> None:
    """Create ``directory`` and make every newly created entry durable."""
    missing = []
    current = directory
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for created in reversed(missing):
        _fsync_directory(created.parent)


class EffectPersistenceError(ResourceIdentityError):
    """A pre-invocation claim could not be made durable; do not invoke."""


def effects_dir() -> Path:
    return Path(EFFECTS_DIR)


def _parse(line: bytes) -> tuple[str, Any]:
    entry = json.loads(line.decode("utf-8"))
    if (not isinstance(entry, dict) or set(entry) != {"v", "type", "record"}
            or entry["v"] != _VERSION or entry["type"] not in _TYPES):
        raise ValueError("unsupported effect record")
    return entry["type"], _TYPES[entry["type"]].from_dict(entry["record"])


class _Index:
    """Incremental consistency of an append-ordered record stream.

    At least as strict as ``EffectHistory`` validation for records appended in
    position order, so a record it accepts never makes the history invalid.
    """

    def __init__(self) -> None:
        self.positions: set[int] = set()
        self.claims: dict[str, int] = {}
        self.settled: set[str] = set()
        self.last: dict[str, int] = {}

    def accepts(self, kind: str, record: Any) -> bool:
        if record.sequence in self.positions:
            return False
        if kind == "claim":
            return record.effect_id not in self.claims
        if kind == "outcome":
            effect = record.effect_id
            return (effect in self.claims and record.sequence > self.claims[effect]
                    and effect not in self.settled and record.sequence > self.last.get(effect, -1))
        return True

    def add(self, kind: str, record: Any) -> None:
        self.positions.add(record.sequence)
        if kind == "claim":
            self.claims[record.effect_id] = record.sequence
        elif kind == "outcome":
            self.last[record.effect_id] = record.sequence
            if record.execution is not ExecutionOutcome.RUNNING:
                self.settled.add(record.effect_id)


def _records() -> dict[str, list]:
    return {"claim": [], "outcome": [], "observation": []}


class EffectLog:
    # Logs still owned by a live run in this process. A later turn appends to
    # the same object rather than a second copy of the same history.
    _LIVE: "weakref.WeakValueDictionary[tuple[str, str], EffectLog]" = weakref.WeakValueDictionary()
    # Serializes the _LIVE check-and-load in ``open``.
    _OPEN_LOCK = threading.Lock()

    def __init__(self, run_id: str, *, durable: bool = True, directory: str | os.PathLike | None = None) -> None:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError("Effect log requires a server-generated run identifier")
        self.run_id = run_id
        self.path = (Path(directory) if directory is not None else effects_dir()) / f"{run_id}.jsonl" if durable else None
        if self.path is not None:
            self._LIVE[(str(self.path.parent), run_id)] = self
        # Records known to be on disk, in file order, and the bytes they span.
        self._durable, self._durable_index = _records(), _Index()
        self._offset = 0
        # Records this process holds that are not on disk: a failed non-claim
        # write, or an observation of a run that has no durable file yet.
        self._volatile: list[tuple[str, Any]] = []
        # Durable records plus every volatile record still consistent with
        # them. A volatile record that collides with a durable one (another
        # writer took its position or settled the same effect) is hidden:
        # losing an unpersisted outcome or observation is conservative.
        self._view, self._view_index = _records(), _Index()
        self._max_sequence = 0
        self._descriptor: int | None = None
        self._directory_synced = False
        # A non-claim record failed to persist. In-memory history stays
        # truthful for this process; replay may lack the later record.
        self.degraded = False
        self._lock = threading.RLock()

    # -- merged view ---------------------------------------------------------

    def _rebuild_view(self) -> None:
        self._view, self._view_index = _records(), _Index()
        durable = sorted(((kind, r) for kind, records in self._durable.items() for r in records),
                         key=lambda pair: pair[1].sequence)
        for kind, record in durable:
            self._view[kind].append(record)
            self._view_index.add(kind, record)
        for kind, record in self._volatile:
            if self._view_index.accepts(kind, record):
                self._view_index.add(kind, record)
                self._view[kind].append(record)

    def _add_durable(self, kind: str, record: Any) -> None:
        self._durable[kind].append(record)
        self._durable_index.add(kind, record)
        self._view[kind].append(record)
        self._view_index.add(kind, record)
        self._max_sequence = max(self._max_sequence, record.sequence)

    def _add_volatile(self, kind: str, record: Any) -> None:
        self._volatile.append((kind, record))
        self._view[kind].append(record)
        self._view_index.add(kind, record)
        self._max_sequence = max(self._max_sequence, record.sequence)

    def _merged_history(self) -> EffectHistory:
        return EffectHistory(tuple(self._view["claim"]), tuple(self._view["outcome"]),
                             tuple(self._view["observation"]))

    # -- persistence -------------------------------------------------------

    def _read_tail(self, descriptor: int, *, repair: bool) -> None:
        """Merge complete records other writers appended after our offset.

        A trailing partial line is a write that never returned to its caller
        (a crash or a failed write), so no backend invocation followed it.
        With ``repair`` (exclusive lock held) it is truncated so the next
        append starts on a record boundary.
        """
        info = os.fstat(descriptor)
        if info.st_nlink != 1 or not stat.S_ISREG(info.st_mode):
            raise OSError("Effect log is aliased")
        if info.st_size < self._offset:
            raise OSError("Effect log shrank under its writer")
        if info.st_size == self._offset:
            return
        os.lseek(descriptor, self._offset, os.SEEK_SET)
        remaining, chunks = info.st_size - self._offset, []
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        complete = data[:data.rfind(b"\n") + 1]
        if repair and len(complete) != len(data):
            os.ftruncate(descriptor, self._offset + len(complete))
            os.fsync(descriptor)
        added: list[tuple[str, Any]] = []
        for line in complete.splitlines():
            try:
                kind, record = _parse(line)
            except (ValueError, TypeError, KeyError, UnicodeDecodeError) as error:
                raise OSError("Effect log tail is corrupt") from error
            if not self._durable_index.accepts(kind, record):
                raise OSError("Effect log tail is inconsistent")
            self._durable[kind].append(record)
            self._durable_index.add(kind, record)
            self._max_sequence = max(self._max_sequence, record.sequence)
            added.append((kind, record))
        self._offset += len(complete)
        if added:
            self._rebuild_view()

    @contextmanager
    def _locked(self):
        """Hold the file exclusively with every durable record merged."""
        assert self.path is not None
        _ensure_directory(self.path.parent)
        flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(self.path, flags, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            self._read_tail(descriptor, repair=True)
            self._descriptor = descriptor
            yield
        finally:
            self._descriptor = None
            os.close(descriptor)  # releases the lock

    def _write(self, kind: str, record: Any) -> None:
        """Append one record under the held lock and make it durable."""
        descriptor = self._descriptor
        assert descriptor is not None
        line = json.dumps({"v": _VERSION, "type": kind, "record": record.to_dict()},
                          sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        data = line.encode("utf-8")
        start = os.fstat(descriptor).st_size
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(descriptor, view):]
            os.fsync(descriptor)
            if not self._directory_synced:
                # The file's own fsync does not make its directory entry
                # durable. Sync it before the first claim returns, still under
                # the lock, so no writer can invoke a backend against a log a
                # crash could lose.
                _fsync_directory(self.path.parent)
                self._directory_synced = True
        except OSError:
            # Unacknowledged: take the record back so the file ends on a
            # record boundary and no writer later merges it as durable.
            try:
                os.ftruncate(descriptor, start)
                os.fsync(descriptor)
            except OSError:
                pass
            raise
        self._offset = start + len(data)

    def _append(self, kind: str, build: Callable[[int, "EffectLog"], Any], *, required: bool):
        """Allocate, validate and persist one record; ``None`` if it no longer applies.

        ``build(sequence, view)`` may return ``None`` when its precondition no
        longer holds against the merged view (``self``).
        """
        with self._lock:
            durable = self.path is not None and (kind != "observation" or self._view["claim"]
                                                 or self.path.exists())
            if not durable:
                return self._append_volatile(kind, build, required=required)
            try:
                with self._locked():
                    record = build(self._max_sequence + 1, self)
                    if record is None:
                        return None
                    if not (self._view_index.accepts(kind, record) and self._durable_index.accepts(kind, record)):
                        # Another writer already settled it, or it collides.
                        if required:
                            raise ValueError("Effect record conflicts with the durable history")
                        return None
                    try:
                        self._write(kind, record)
                    except OSError:
                        if required:
                            raise
                        self.degraded = True
                        self._add_volatile(kind, record)
                        return record
                    self._add_durable(kind, record)
                    return record
            except (OSError, ValueError) as error:
                if required:
                    raise EffectPersistenceError("Effect claim could not be persisted durably") from error
                self.degraded = True
                # The lock or tail could not be taken: keep this process
                # truthful without touching the file.
                return self._append_volatile(kind, build, required=False)

    def _append_volatile(self, kind: str, build, *, required: bool):
        record = build(self._max_sequence + 1, self)
        if record is None:
            return None
        if not self._view_index.accepts(kind, record):
            if required:
                raise ValueError("Effect record conflicts with the history")
            return None
        self._add_volatile(kind, record)
        return record

    # -- records -----------------------------------------------------------

    def claim(self, **fields: Any) -> EffectClaim:
        """Persist a claim before invocation; raises if it is not durable."""
        run_id = fields.pop("run_id", self.run_id)
        return self._append("claim", lambda seq, _h: EffectClaim(sequence=seq, run_id=run_id, **fields),
                            required=True)

    def outcome(self, **fields: Any) -> EffectOutcome | None:
        """Append an outcome; ``None`` if the effect was already settled."""
        return self._append("outcome", lambda seq, _h: EffectOutcome(sequence=seq, **fields), required=False)

    def observe(self, **fields: Any) -> Observation | None:
        return self._append("observation", lambda seq, _h: Observation(sequence=seq, **fields), required=False)

    def refresh(self) -> None:
        """Merge records other writers appended (shared lock, no repair)."""
        if self.path is None:
            return
        with self._lock:
            try:
                descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                                     | getattr(os, "O_CLOEXEC", 0))
            except FileNotFoundError:
                return
            except OSError:
                self.degraded = True
                return
            try:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_SH)
                self._read_tail(descriptor, repair=False)
            except OSError:
                self.degraded = True
            finally:
                os.close(descriptor)

    def history(self) -> EffectHistory:
        self.refresh()
        with self._lock:
            return self._merged_history()

    def assessments(self) -> tuple[EffectAssessment, ...]:
        return assess_all(self.history())

    # -- replay ------------------------------------------------------------

    @classmethod
    def load(cls, run_id: str, *, directory: str | os.PathLike | None = None) -> "EffectLog":
        """Reload a persisted log. A malformed record fails closed.

        A torn final line (no newline) is the only tolerated damage: it was a
        write interrupted by a crash, so its claim never returned to a caller
        and no backend invocation followed it.
        """
        log = cls(run_id, directory=directory)
        assert log.path is not None
        try:
            descriptor = os.open(log.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        except FileNotFoundError:
            return log
        except OSError as error:
            raise EffectPersistenceError("Effect log is unreadable") from error
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_SH)
            info = os.fstat(descriptor)
            if info.st_nlink != 1 or not stat.S_ISREG(info.st_mode):
                raise EffectPersistenceError("Effect log is aliased")
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                raw = stream.read()
        except OSError as error:
            raise EffectPersistenceError("Effect log is unreadable") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        complete = raw[:raw.rfind(b"\n") + 1]  # drop a torn final write
        for line in complete.splitlines():
            try:
                kind, record = _parse(line)
            except (ValueError, TypeError, KeyError, UnicodeDecodeError) as error:
                raise EffectPersistenceError("Effect log is corrupt") from error
            if not log._durable_index.accepts(kind, record):
                raise EffectPersistenceError("Effect log history is inconsistent")
            log._durable[kind].append(record)
            log._durable_index.add(kind, record)
            log._max_sequence = max(log._max_sequence, record.sequence)
        try:
            log._rebuild_view()
            log._merged_history()
        except ValueError as error:
            raise EffectPersistenceError("Effect log history is inconsistent") from error
        log._offset = len(complete)
        return log

    @classmethod
    def open(cls, run_id: str, *, directory: str | os.PathLike | None = None) -> "EffectLog":
        """The live log for a run, or its replayed durable history.

        A log that is not live belongs to a finished or crashed run, so its
        unsettled claims are recovered as interrupted before any append.
        """
        base = Path(directory) if directory is not None else effects_dir()
        with cls._OPEN_LOCK:
            live = cls._LIVE.get((str(base), run_id))
            if live is not None:
                return live
            log = cls.load(run_id, directory=base)
            log.recover_interrupted()
            return log

    # -- background launch lineage ------------------------------------------

    def index_launch(self, generation: str, effect_id: str) -> None:
        """Durably map an exact Wave 3 launch generation to its claim."""
        if self.path is None:
            return
        if not _RUN_ID.fullmatch(generation or ""):
            raise ValueError("Malformed launch generation")
        target = self.path.parent / f"launch-{generation}.json"
        temporary = target.with_suffix(".tmp")
        data = json.dumps({"run_id": self.run_id, "effect_id": effect_id}, sort_keys=True).encode()
        _ensure_directory(target.parent)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(descriptor, view):]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, target)
        _fsync_directory(target.parent)

    @staticmethod
    def launch_owner(generation: str, *, directory: str | os.PathLike | None = None) -> tuple[str, str] | None:
        if not _RUN_ID.fullmatch(generation or ""):
            return None
        base = Path(directory) if directory is not None else effects_dir()
        try:
            descriptor = os.open(base / f"launch-{generation}.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                if os.fstat(stream.fileno()).st_nlink != 1:
                    return None
                value = json.loads(stream.read(4096))
        except (OSError, ValueError):
            return None
        if (not isinstance(value, dict) or set(value) != {"run_id", "effect_id"}
                or not isinstance(value["run_id"], str) or not _RUN_ID.fullmatch(value["run_id"])
                or not isinstance(value["effect_id"], str)):
            return None
        return value["run_id"], value["effect_id"]

    def recover_interrupted(self) -> tuple[EffectOutcome, ...]:
        """Append INTERRUPTED outcomes for claims that never settled.

        Each claim is rechecked against the merged history under the lock, so
        a claim another writer settled (or marked running) meanwhile is left
        alone.
        """
        with self._lock:
            appended = []
            for pending in replay_interrupted(self.history(), self._max_sequence + 1):
                def build(seq: int, log: "EffectLog", o: EffectOutcome = pending) -> EffectOutcome | None:
                    if o.effect_id in log._view_index.last or o.effect_id in log._durable_index.last:
                        return None
                    return EffectOutcome(o.effect_id, seq, o.execution, o.impact, replayed=True)
                record = self._append("outcome", build, required=False)
                if record is not None:
                    appended.append(record)
            return tuple(appended)
