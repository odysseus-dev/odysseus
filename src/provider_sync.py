"""Provider synchronization service: read-only discovery plus explicit, additive, opt-in mirroring.

Rules enforced here (and tested):
  * Credentials never enter this module (the one explicit exception is delegated: see copy_key). A mirrored provider is created WITHOUT a key; the
    target reports `requires_authentication` until the user supplies one in that application.
  * OAuth, subscription and process-transport providers are never mirrored.
  * Mirroring is additive only. Nothing is overwritten, renamed, disabled or deleted, and a
    default model is never touched (neither writer has a code path that could).
  * A provider the user deleted from the mirror target is tombstoned and never re-created.
  * Identity is the canonical endpoint URL, so a mirror can never be duplicated, and a mirrored
    provider found on the source side again is "in sync", not a new provider (no loops).
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import threading
import time

from src.provider_adapters import KNOWN_OPENAI_COMPATIBLE, hermes_entries, odysseus_entries, probe_local_models
from src.provider_catalog import (AUTH_KEY, AUTH_NONE, CONNECTED, OFFLINE, REQUIRES_AUTH, SyncState,
                                  build_catalog, canonical_url, connection_state, is_loopback, key_copy_status)

CACHE_SECONDS = 15
FAILURE_CACHE_SECONDS = 3


class SyncError(RuntimeError):
    pass


class ProviderSync:
    def __init__(self, load_state, save_state, odysseus_rows, hermes_fetch, create_odysseus, append_hermes,
                 probe=probe_local_models, clock=time.monotonic, on_odysseus_change=lambda: None, hermes_urls=lambda: {},
                 copy_key=None):
        self._load_state, self._save_state = load_state, save_state
        self._copy_key = copy_key
        self._hermes_urls = hermes_urls
        self._odysseus_rows, self._hermes_fetch = odysseus_rows, hermes_fetch
        self._create_odysseus, self._append_hermes = create_odysseus, append_hermes
        self._probe, self._clock, self._on_change = probe, clock, on_odysseus_change
        self._lock = threading.RLock()
        self._cache = None

    # ---- discovery (read-only) -------------------------------------------------
    def _gather(self):
        od = odysseus_entries(self._odysseus_rows())
        payload, hermes_state = self._hermes_fetch()
        he = hermes_entries(payload, self._hermes_urls()) if hermes_state == 'ready' else []
        return self._probed(od), self._probed(he), hermes_state

    def _probed(self, entries):
        """Probe loopback servers concurrently so one slow or dead server cannot stall a refresh."""
        local = [e for e in entries if e.canonical and is_loopback(e.canonical) and e.enabled]
        outcomes = {}
        urls = sorted({e.canonical for e in local})
        if urls:
            with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
                outcomes = dict(zip(urls, pool.map(self._probe, urls)))
        result = []
        for entry in entries:
            state, models = outcomes.get(entry.canonical, (None, ())) if entry in local else (None, ())
            if state == 'online':
                entry = replace(entry, reachable=True, models=tuple(dict.fromkeys(entry.models + models)))
            elif state == 'requires_authentication':
                entry = replace(entry, reachable=True, auth=AUTH_KEY)
            elif state == 'offline':
                entry = replace(entry, reachable=False)
            result.append(entry)
        return result

    def snapshot(self, refresh=False):
        with self._lock:
            now = self._clock()
            if not refresh and self._fresh(self._cache, now):
                return self._cache[1]
            od, he, hermes_state = self._gather()
            state = self._reconcile(od, he)
            result = {'providers': build_catalog(od, he, state), 'applications': {
                'odysseus': 'ready', 'hermes': hermes_state}, 'refreshed': True}
            self._cache = (now, result)
            return result

    def _fresh(self, entry, now):
        """A good answer is reused for CACHE_SECONDS; a Hermes that was unreachable or not ready only for FAILURE_CACHE_SECONDS, so a gateway that
        has just come back is seen quickly instead of after the full interval."""
        if not entry:
            return False
        age = now - entry[0]
        return age < (CACHE_SECONDS if entry[1]['applications']['hermes'] == 'ready' else FAILURE_CACHE_SECONDS)

    def _reconcile(self, od, he):
        """Detect mirrors the user deleted from the target; remember so they are never re-created."""
        state = SyncState.load(self._load_state())
        ids = {'odysseus': {e.identity for e in od}, 'hermes': {e.identity for e in he}}
        changed = False
        for identity, note in list(state.applied.items()):
            target, other = note.get('target'), 'hermes' if note.get('target') == 'odysseus' else 'odysseus'
            if target in ids and other in ids and identity not in ids[target] and identity in ids[other] \
                    and identity not in state.tombstones:
                state.tombstones[identity] = {'by': target, 'fingerprint': note.get('fingerprint', '')}
                changed = True
        if changed:
            self._save_state(state.dump())
        return state

    # ---- explicit user actions -------------------------------------------------
    def set_share(self, identity, enabled):
        with self._lock:
            row = self._row(self.snapshot(refresh=True), identity)
            if not row['portable']:
                raise SyncError('This provider cannot be shared: it uses a login or a transport that is not portable.')
            state = SyncState.load(self._load_state())
            state.share[identity] = bool(enabled)
            if enabled:
                # An explicit re-enable is the only way back after a delete; forget the old mirror record too,
                # otherwise the next reconcile would immediately tombstone it again.
                state.tombstones.pop(identity, None)
                state.applied.pop(identity, None)
            self._save_state(state.dump())
            self._cache = None
            return self._row(self.snapshot(refresh=True), identity)

    def apply(self, identity):
        """Create the missing side of one opted-in provider. Idempotent."""
        with self._lock:
            od, he, hermes_state = self._gather()
            state = self._reconcile(od, he)
            entries = {e.identity: e for e in od + he}
            source = entries.get(identity)
            if source is None or not source.portable:
                raise SyncError('That provider is unknown or not shareable.')
            if not state.share.get(identity):
                raise SyncError('Sharing is not enabled for this provider.')
            if identity in state.tombstones:
                raise SyncError('This provider was deleted on the other side; re-enable sharing to restore it.')
            has = {'odysseus': any(e.identity == identity for e in od), 'hermes': any(e.identity == identity for e in he)}
            if has['odysseus'] and has['hermes']:
                return {'result': 'already_present', 'id': identity}
            target = 'hermes' if has['odysseus'] else 'odysseus'
            if target == 'hermes' and hermes_state != 'ready':
                raise SyncError('Hermes is not reachable, so nothing was written.')
            builtin = {canonical_url(url) for url in KNOWN_OPENAI_COMPATIBLE.values()}
            if target == 'hermes' and source.canonical in builtin:
                raise SyncError('Hermes has a built-in provider for this service; add the credential in Hermes instead of mirroring it.')
            # Canonical URL: lower-cased, no default port, and 127.0.0.1 rather than `localhost`, which
            # Windows may resolve to IPv6 first while local servers listen on IPv4 only.
            definition = {'name': source.name, 'base_url': source.canonical, 'models': list(source.models),
                          'slug': 'ody-' + hashlib.sha256(identity.encode()).hexdigest()[:10]}
            outcome = self._append_hermes(definition) if target == 'hermes' else self._create_odysseus(definition)
            if outcome in ('added', 'created'):
                state = SyncState.load(self._load_state())
                state.applied[identity] = {'target': target, 'fingerprint': source.definition_fingerprint()}
                self._save_state(state.dump())
                if target == 'odysseus':
                    self._on_change()
            self._cache = None
            return {'result': outcome, 'id': identity, 'target': target}

    def copy_key(self, identity):
        """Explicit, user-confirmed copy of ONE Hermes API key into Odysseus.

        The key is read from Hermes's own store and written to Odysseus's encrypted store inside the injected
        callable, so it never enters this class, a return value, the cache or a log. Never overwrites a key
        Odysseus already has; Hermes keeps its own copy. OAuth/subscription logins are not eligible.
        """
        with self._lock:
            od, he, _ = self._gather()
            source = next((e for e in he if e.identity == identity), None)
            target = next((e for e in od if e.identity == identity), None)
            status = key_copy_status(target, source)
            if status == 'already':
                raise SyncError('Odysseus already has a key for this provider; it was not changed.')
            if status != 'available':
                raise SyncError('This key cannot be copied: the provider is unknown, login-based, or Hermes does not keep its key in its .env file.')
            if self._copy_key is None:
                raise SyncError('Key copying is not configured.')
            outcome = self._copy_key(source.native_id, {'name': source.name, 'base_url': source.canonical, 'models': list(source.models)})
            state = SyncState.load(self._load_state())
            if outcome == 'created':
                state.tombstones.pop(identity, None)
                state.applied[identity] = {'target': 'odysseus', 'fingerprint': source.definition_fingerprint()}
                self._save_state(state.dump())
            self._cache = None
            self._on_change()
            return {'result': outcome, 'id': identity}

    def apply_all(self):
        results = []
        for row in self.snapshot(refresh=True)['providers']:
            if row['sync'].startswith('pending_'):
                try:
                    results.append(self.apply(row['id']))
                except SyncError as exc:
                    results.append({'result': 'refused', 'id': row['id'], 'reason': str(exc)})
        return results

    def test(self, identity):
        """Credential-free connection test. Remote providers are never contacted: that would need their key."""
        with self._lock:
            row = self._row(self.snapshot(refresh=True), identity)
            canonical = identity[3:] if identity.startswith('ep:') else None
            if canonical and is_loopback(canonical):
                state, models = self._probe(canonical)
                return {'id': identity, 'state': state, 'models': list(models)}
            return {'id': identity, 'state': 'not_tested',
                    'message': 'Remote providers are tested from the application that holds their credential.',
                    'status': row['status']}

    @staticmethod
    def _row(snapshot, identity):
        for row in snapshot['providers']:
            if row['id'] == identity:
                return row
        raise SyncError('Unknown provider.')
