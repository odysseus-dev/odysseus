"""Concurrency stress tests for AuthManager._config_lock.

Verifies that concurrent create/delete/rename operations don't lose data
or corrupt auth.json. If someone removes the lock, these tests should fail
with missing users or assertion errors.
"""

import json
import threading
import time
import contextlib
import sys
import types
from concurrent.futures import ThreadPoolExecutor, as_completed

import bcrypt
import pytest

from tests.helpers.import_state import clear_module


class _OwnerColumn:
    def __eq__(self, other):
        return ("owner ==", other)


class _FakeApiToken:
    owner = _OwnerColumn()


class _FakeQuery:
    def filter(self, *_conds):
        return self

    def delete(self, *args, **kwargs):
        return 0


class _FakeSession:
    def query(self, model):
        assert model is _FakeApiToken
        return _FakeQuery()


@pytest.fixture(autouse=True)
def _stub_api_token_purge(monkeypatch):
    @contextlib.contextmanager
    def _fake_db_session():
        yield _FakeSession()

    db_stub = types.ModuleType("core.database")
    db_stub.get_db_session = _fake_db_session
    db_stub.ApiToken = _FakeApiToken
    monkeypatch.setitem(sys.modules, "core.database", db_stub)


@pytest.fixture(autouse=True)
def _fast_bcrypt_work_factor(monkeypatch):
    """Reduce bcrypt hashing cost to work factor 4 for concurrency tests.

    Work factor 4 is the minimum standard bcrypt cost. It preserves genuine
    bcrypt password hashing, salt generation, and verification semantics
    while reducing CPU time from ~200ms per hash to sub-millisecond.

    Only bcrypt's implicit default work factor (12) is replaced with 4.
    Explicitly supplied rounds and prefix arguments are preserved.

    Patching bcrypt.gensalt at the library level ensures all dynamically
    reloaded AuthManager instances (via clear_module) inherit work factor 4
    without modifying core/auth.py or leaking into unrelated test files.
    """
    orig_gensalt = bcrypt.gensalt

    def fast_gensalt(*args, **kwargs):
        if not args and "rounds" not in kwargs:
            kwargs["rounds"] = 4
        return orig_gensalt(*args, **kwargs)

    monkeypatch.setattr(bcrypt, "gensalt", fast_gensalt)


class ContentionCoordinator:
    """Test-only coordinator that verifies observable contention on AuthManager._config_lock."""

    def __init__(self, mgr, timeout=3.0):
        self.mgr = mgr
        self.timeout = timeout
        self.lead_thread = None
        self.lead_in_cs = threading.Event()
        self.contender_at_lock = threading.Event()
        self.aborted = threading.Event()
        self.workers = []
        self._real_lock = mgr._config_lock

        coord = self

        class _LockProxy:
            def acquire(self, *args, **kwargs):
                if threading.current_thread() != coord.lead_thread:
                    coord.contender_at_lock.set()
                return coord._real_lock.acquire(*args, **kwargs)

            def release(self):
                return coord._real_lock.release()

            def __enter__(self):
                if threading.current_thread() != coord.lead_thread:
                    coord.contender_at_lock.set()
                return coord._real_lock.__enter__()

            def __exit__(self, exc_type, exc_val, exc_tb):
                return coord._real_lock.__exit__(exc_type, exc_val, exc_tb)

            def locked(self):
                return coord._real_lock.locked()

        self._proxy = _LockProxy()
        mgr._config_lock = self._proxy

    def has_active_workers(self):
        return any(t.is_alive() for t in self.workers)

    def restore(self):
        """Restore the original lock on AuthManager.

        Ensures test instrumentation is not restored while relevant workers remain active.
        """
        if self.has_active_workers():
            return False
        self.mgr._config_lock = self._real_lock
        return True

    def abort(self):
        """Release waiting workers on failure or timeout paths."""
        self.aborted.set()
        self.lead_in_cs.set()
        self.contender_at_lock.set()

    def wait_in_lead(self):
        """Called by lead thread from inside critical section to await contender lock attempt."""
        self.lead_in_cs.set()
        signaled = self.contender_at_lock.wait(timeout=self.timeout)
        if not signaled:
            self.abort()
            raise AssertionError(
                f"Contender thread did not attempt to acquire _config_lock within {self.timeout}s"
            )
        if self.aborted.is_set():
            raise RuntimeError("Contention coordination aborted due to worker failure")

    def wait_for_lead(self):
        """Called by contender thread before executing operation."""
        signaled = self.lead_in_cs.wait(timeout=self.timeout)
        if not signaled:
            self.abort()
            raise AssertionError(
                f"Lead thread did not enter critical section within {self.timeout}s"
            )
        if self.aborted.is_set():
            raise RuntimeError("Contention coordination aborted due to worker failure")


def run_coordinated_workers(*targets, timeout=5.0, coordinator=None):
    """Run worker targets in threads, verify clean termination, and propagate exceptions.

    Enforces a single overall monotonic deadline for all worker threads.
    If a worker fails or does not terminate, synchronization waiters are released
    and test instrumentation is not restored while threads remain active.
    """
    errors = []
    threads = []

    def make_runner(target, name):
        def runner():
            try:
                target()
            except BaseException as exc:
                errors.append((name, exc))
                if coordinator is not None:
                    coordinator.abort()
        return runner

    for i, target in enumerate(targets):
        name = getattr(target, "__name__", f"worker_{i}")
        t = threading.Thread(target=make_runner(target, name), name=name)
        threads.append(t)

    if coordinator is not None:
        coordinator.workers = list(threads)

    for t in threads:
        t.start()

    deadline = time.monotonic() + timeout
    for t in threads:
        remaining = max(0.0, deadline - time.monotonic())
        t.join(timeout=remaining)

    unjoined = [t.name for t in threads if t.is_alive()]
    if unjoined:
        if coordinator is not None:
            coordinator.abort()
        grace_deadline = time.monotonic() + 1.0
        for t in threads:
            if t.is_alive():
                remaining = max(0.0, grace_deadline - time.monotonic())
                t.join(timeout=remaining)
        unjoined = [t.name for t in threads if t.is_alive()]

    if coordinator is not None and not unjoined:
        coordinator.restore()

    if unjoined:
        diag = f"Worker thread(s) did not terminate within {timeout}s: {unjoined}."
        if errors:
            diag += f" Prior errors: {errors}"
        raise AssertionError(diag)

    if errors:
        root_errors = [e for e in errors if not (isinstance(e[1], RuntimeError) and "aborted" in str(e[1]))]
        primary_error = root_errors[0] if root_errors else errors[0]
        names = ", ".join(f"{name}: {type(exc).__name__}: {exc}" for name, exc in errors)
        raise AssertionError(f"Worker thread(s) failed: {names}") from primary_error[1]


def _fresh_auth_manager(tmp_path):
    clear_module("core.auth")
    from core.auth import AuthManager

    return AuthManager(str(tmp_path / "auth.json"))


class TestConcurrentCreateUser:
    """Concurrent create_user calls must not lose accounts."""

    @pytest.mark.slow
    def test_parallel_creates_no_lost_users(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        num_users = 50

        def create(i):
            return mgr.create_user(f"user{i}", f"password{i}")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(create, i) for i in range(num_users)]
            results = [f.result() for f in as_completed(futures)]

        assert all(results), "Some create_user calls returned False unexpectedly"
        assert len(mgr.users) == num_users

        mgr2 = _fresh_auth_manager(tmp_path)
        mgr2.auth_path = mgr.auth_path
        mgr2._load()
        assert len(mgr2.users) == num_users

    def test_parallel_creates_same_username_only_one_wins(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        coord = ContentionCoordinator(mgr)
        results = []
        res_lock = threading.Lock()

        orig_gensalt = bcrypt.gensalt

        def coordinated_gensalt(*args, **kwargs):
            if threading.current_thread().name == "lead_create":
                coord.wait_in_lead()
            return orig_gensalt(*args, **kwargs)

        def lead_create():
            coord.lead_thread = threading.current_thread()
            res = mgr.create_user("contested", "password123")
            with res_lock:
                results.append(res)

        def make_contender(idx):
            def contender():
                coord.wait_for_lead()
                res = mgr.create_user("contested", "password123")
                with res_lock:
                    results.append(res)

            contender.__name__ = f"contender_{idx}"
            return contender

        contenders = [make_contender(i) for i in range(19)]

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(bcrypt, "gensalt", coordinated_gensalt)
            try:
                run_coordinated_workers(lead_create, *contenders, coordinator=coord)
            finally:
                coord.restore()

        assert results.count(True) == 1
        assert results.count(False) == 19
        assert len(mgr.users) == 1


class TestConcurrentDeleteUser:
    """Concurrent deletes must not corrupt state."""

    @pytest.mark.slow
    def test_parallel_deletes_no_corruption(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        mgr.create_user("admin", "adminpw", is_admin=True)
        num_users = 30
        for i in range(num_users):
            mgr.create_user(f"target{i}", f"pw{i}")

        assert len(mgr.users) == num_users + 1

        def delete(i):
            return mgr.delete_user(f"target{i}", "admin")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(delete, i) for i in range(num_users)]
            results = [f.result() for f in as_completed(futures)]

        assert all(results)
        assert len(mgr.users) == 1
        with open(mgr.auth_path, "r") as f:
            data = json.load(f)
        assert len(data["users"]) == 1
        assert "admin" in data["users"]

    def test_parallel_deletes_same_user_only_one_wins(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        mgr.create_user("admin", "adminpw", is_admin=True)
        mgr.create_user("victim", "pw")
        coord = ContentionCoordinator(mgr)
        results = []
        res_lock = threading.Lock()

        orig_query = _FakeSession.query

        def coordinated_query(session_self, model):
            if threading.current_thread().name == "lead_delete":
                coord.wait_in_lead()
            return orig_query(session_self, model)

        def lead_delete():
            coord.lead_thread = threading.current_thread()
            res = mgr.delete_user("victim", "admin")
            with res_lock:
                results.append(res)

        def make_contender(idx):
            def contender():
                coord.wait_for_lead()
                res = mgr.delete_user("victim", "admin")
                with res_lock:
                    results.append(res)

            contender.__name__ = f"contender_del_{idx}"
            return contender

        contenders = [make_contender(i) for i in range(4)]

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(_FakeSession, "query", coordinated_query)
            try:
                run_coordinated_workers(lead_delete, *contenders, coordinator=coord)
            finally:
                coord.restore()

        assert results.count(True) == 1
        assert results.count(False) == 4
        assert "victim" not in mgr.users


class TestConcurrentRenameUser:
    """Concurrent renames must not lose or duplicate users."""

    @pytest.mark.slow
    def test_parallel_renames_no_lost_users(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        mgr.create_user("admin", "adminpw", is_admin=True)
        num_users = 20
        for i in range(num_users):
            mgr.create_user(f"old{i}", f"pw{i}")

        def rename(i):
            return mgr.rename_user(f"old{i}", f"new{i}", "admin")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(rename, i) for i in range(num_users)]
            results = [f.result() for f in as_completed(futures)]

        assert all(results)
        for i in range(num_users):
            assert f"new{i}" in mgr.users
            assert f"old{i}" not in mgr.users

        assert len(mgr.users) == num_users + 1

    def test_parallel_renames_same_user_only_one_wins(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        mgr.create_user("admin", "adminpw", is_admin=True)
        mgr.create_user("old0", "pw")
        mgr.create_user("old1", "pw")
        coord = ContentionCoordinator(mgr)
        results = {}

        class _CoordinatedUsersDict(dict):
            def get(self, key, default=None):
                if key == "admin" and threading.current_thread().name == "lead_rename":
                    coord.wait_in_lead()
                return super().get(key, default)

        mgr._config["users"] = _CoordinatedUsersDict(mgr._config["users"])

        def lead_rename():
            coord.lead_thread = threading.current_thread()
            results["lead"] = mgr.rename_user("old0", "shared_new", "admin")

        def contender_rename():
            coord.wait_for_lead()
            results["contender"] = mgr.rename_user("old1", "shared_new", "admin")

        try:
            run_coordinated_workers(lead_rename, contender_rename, coordinator=coord)
        finally:
            coord.restore()

        outcomes = list(results.values())
        assert outcomes.count(True) == 1
        assert outcomes.count(False) == 1
        assert len(mgr.users) == 3
        assert "shared_new" in mgr.users


class TestConcurrentMixedOperations:
    """Mixed create/delete/rename at the same time."""

    @pytest.mark.slow
    def test_mixed_operations_no_corruption(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        mgr.create_user("admin", "adminpw", is_admin=True)

        for i in range(20):
            mgr.create_user(f"existing{i}", f"pw{i}")

        def create_batch():
            for i in range(20):
                mgr.create_user(f"newuser{i}", f"pw{i}")

        def delete_batch():
            for i in range(10):
                mgr.delete_user(f"existing{i}", "admin")

        def rename_batch():
            for i in range(10, 20):
                mgr.rename_user(f"existing{i}", f"renamed{i}", "admin")

        run_coordinated_workers(create_batch, delete_batch, rename_batch, timeout=10.0)

        assert "admin" in mgr.users
        for i in range(10):
            assert f"existing{i}" not in mgr.users
        for i in range(10, 20):
            assert f"renamed{i}" in mgr.users
            assert f"existing{i}" not in mgr.users
        for i in range(20):
            assert f"newuser{i}" in mgr.users

        with open(mgr.auth_path, "r") as f:
            data = json.load(f)
        assert set(data["users"].keys()) == set(mgr.users.keys())

    def test_concurrent_rename_and_delete_same_user(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        mgr.create_user("admin", "adminpw", is_admin=True)
        mgr.create_user("target", "pw")
        coord = ContentionCoordinator(mgr)
        results = {}

        orig_query = _FakeSession.query

        def coordinated_query(session_self, model):
            if threading.current_thread().name == "lead_del_target":
                coord.wait_in_lead()
            return orig_query(session_self, model)

        def lead_del_target():
            coord.lead_thread = threading.current_thread()
            results["delete"] = mgr.delete_user("target", "admin")

        def contender_rename_target():
            coord.wait_for_lead()
            results["rename"] = mgr.rename_user("target", "renamed_target", "admin")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(_FakeSession, "query", coordinated_query)
            try:
                run_coordinated_workers(lead_del_target, contender_rename_target, coordinator=coord)
            finally:
                coord.restore()

        assert results.get("delete") is True
        assert results.get("rename") is False
        assert "target" not in mgr.users
        assert "renamed_target" not in mgr.users


class TestDiskConsistency:
    """Verify auth.json is never in a corrupt state during concurrent writes."""

    @pytest.mark.slow
    def test_file_always_valid_json_during_concurrent_ops(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        assert mgr.create_user("admin", "adminpw", is_admin=True)

        stop_event = threading.Event()
        reader_ready = threading.Event()
        corruption_found = []
        observations = {"reads": 0, "with_writes": 0}

        def reader():
            while not stop_event.is_set():
                try:
                    with open(mgr.auth_path, "r") as f:
                        content = f.read()
                    data = json.loads(content)
                    observations["reads"] += 1
                    if any(name.startswith("stress") for name in data["users"]):
                        observations["with_writes"] += 1
                    reader_ready.set()
                except json.JSONDecodeError as e:
                    corruption_found.append(str(e))
                    break
                time.sleep(0.001)

        def writer():
            assert reader_ready.wait(5), "reader never observed the initial file"
            results = []
            for i in range(50):
                results.append(mgr.create_user(f"stress{i}", f"pw{i}"))
            return results

        # Futures propagate reader/writer exceptions to the test thread.
        with ThreadPoolExecutor(max_workers=2) as pool:
            reading = pool.submit(reader)
            writing = pool.submit(writer)
            try:
                results = writing.result(timeout=120)
            finally:
                stop_event.set()
            reading.result(timeout=5)

        assert not corruption_found, f"Corrupt JSON detected: {corruption_found[0]}"
        assert len(results) == 50 and all(results), "writer operations did not succeed"
        assert observations["reads"] > 0
        assert observations["with_writes"] > 0, "reader saw no persisted writer activity"
        expected = {"admin"} | {f"stress{i}" for i in range(50)}
        with open(mgr.auth_path) as persisted:
            assert set(json.load(persisted)["users"]) == expected
        assert set(_fresh_auth_manager(tmp_path).users) == expected
