import json, os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from odysseus.trust.gate import RegistrySnapshot
from odysseus.trust.validator import TrustRegistryValidator, TrustValidationError

if os.name == 'nt':
    import msvcrt
else:
    import fcntl

class TrustRegistryStoreError(Exception):
    pass

class AppendOnlyTrustStore:
    def __init__(self, ledger_path: Path, validator: TrustRegistryValidator, *, clock: Callable[[], datetime] | None = None) -> None:
        self._ledger_path = Path(ledger_path)
        self._lock_path = self._ledger_path.with_suffix(self._ledger_path.suffix + '.lock')
        self._validator = validator
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._ledger_path.exists():
            self._ledger_path.touch()

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, 'a+b') as lf:
            if os.name == 'nt':
                lf.seek(0)
                if lf.tell() == 0:
                    lf.write(b'\0')
                    lf.flush()
                lf.seek(0)
                msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lf.seek(0)
                    msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def _read_all_entries_unlocked(self) -> list[dict[str, Any]]:
        if not self._ledger_path.exists():
            return []
        entries = []
        for ln, line in enumerate(self._ledger_path.read_text(encoding='utf-8').splitlines(), start=1):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise TrustRegistryStoreError(f'Corrupted JSONL at line {ln}') from exc
        return entries

    def append_entry(self, entry: dict[str, Any]) -> None:
        with self._exclusive_lock():
            existing = self._read_all_entries_unlocked()
            prev, seen_ids, seen_nonces = None, set(), set()
            for item in existing:
                self._validator.validate_entry(item, previous_entry=prev)
                p = item['payload']
                seen_ids.add(p['entry_id'])
                if p['sequence_number'] == 1:
                    seen_nonces.add(p['subject_binding']['ops_v3_202_challenge_nonce'])
                prev = item
            self._validator.validate_entry(entry, previous_entry=prev)
            cand = entry['payload']
            if cand['entry_id'] in seen_ids:
                raise TrustValidationError('Duplicate entry_id')
            if prev is None and cand['subject_binding']['ops_v3_202_challenge_nonce'] in seen_nonces:
                raise TrustValidationError('Challenge nonce replay detected')
            with open(self._ledger_path, 'a', encoding='utf-8', newline='\n') as f:
                f.write(json.dumps(entry, separators=(',', ':'), sort_keys=True) + '\n')
                f.flush()
                os.fsync(f.fileno())

    def get_entries_for_certification(self, certification_id: str) -> list[dict[str, Any]]:
        with self._exclusive_lock():
            all_entries = self._read_all_entries_unlocked()
        prev, matched = None, []
        for entry in all_entries:
            self._validator.validate_entry(entry, previous_entry=prev)
            if entry['payload']['certification_id'] == certification_id:
                matched.append(entry)
            prev = entry
        return matched

    def get_snapshot(self, certification_id: str) -> RegistrySnapshot:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return RegistrySnapshot(now, now, self.get_entries_for_certification(certification_id))
