"""Force a Settings save into the window between a handler's read and write.

A handler that reads the preference record, edits it, and writes the whole
record back loses any Settings save that lands in between. This helper makes
that interleaving deterministic, with events rather than sleeps:

1. The handler reads the store (the ``after_read``-th raw read).
2. A Settings save (a locked partial update) starts on its own thread.
3. If the handler's read was not covered by the store lock, the save is
   allowed to finish before the handler continues, which is exactly the
   lost-update window. If the read is covered, the save can only wait for the
   lock, so the handler continues straight away.

Either way both writes must be present afterwards. Counting reads lets a test
aim at the read that precedes the write, whatever happens before it.
"""
import os
import threading

import pytest


def store_lock_is_held(prefs_file) -> bool:
    """True when another file description holds the preference store's flock."""
    fcntl = pytest.importorskip("fcntl")
    with open(os.path.abspath(str(prefs_file)) + ".lock", "a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False


class SettingsSaveRace:
    def __init__(self, prefs_routes, prefs_file, monkeypatch, *, user, updates, after_read=1):
        self._prefs_routes = prefs_routes
        self._prefs_file = prefs_file
        self._user = user
        self._updates = updates
        self._after_read = after_read
        self._reads = 0
        self._read_done = threading.Event()
        self._save_done = threading.Event()
        self.errors = []
        self.fired = False
        real_load = prefs_routes._load

        def gated_load():
            data = real_load()
            self._reads += 1
            if self._reads == self._after_read:
                self.fired = True
                self._read_done.set()
                if not store_lock_is_held(self._prefs_file):
                    assert self._save_done.wait(timeout=5.0)
            return data

        monkeypatch.setattr(prefs_routes, "_load", gated_load)
        self._thread = threading.Thread(target=self._save, daemon=True)
        self._thread.start()

    def _save(self):
        try:
            self._read_done.wait(timeout=5.0)
            self._prefs_routes._update_for_user(self._user, self._updates)
        except BaseException as exc:  # reported by finish()
            self.errors.append(exc)
        finally:
            self._save_done.set()

    def finish(self):
        self._thread.join(timeout=5.0)
        assert self.fired, "the handler never reached the targeted preference read"
        assert not self.errors, self.errors
