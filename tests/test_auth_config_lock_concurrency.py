"""Concurrency stress tests for AuthManager._config_lock.

Verifies that concurrent create/delete/rename operations don't lose data
or corrupt auth.json. If someone removes the lock, these tests should fail
with missing users or assertion errors.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from tests.helpers.import_state import clear_module


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
        num_attempts = 20

        def create(_):
            return mgr.create_user("contested", "password123")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(create, i) for i in range(num_attempts)]
            results = [f.result() for f in as_completed(futures)]

        assert results.count(True) == 1
        assert results.count(False) == num_attempts - 1
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

        threads = [
            threading.Thread(target=create_batch),
            threading.Thread(target=delete_batch),
            threading.Thread(target=rename_batch),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

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


class TestDiskConsistency:
    """Verify auth.json is never in a corrupt state during concurrent writes."""

    @pytest.mark.slow
    def test_file_always_valid_json_during_concurrent_ops(self, tmp_path):
        mgr = _fresh_auth_manager(tmp_path)
        mgr.create_user("admin", "adminpw", is_admin=True)

        stop_event = threading.Event()
        corruption_found = []

        def reader():
            while not stop_event.is_set():
                try:
                    with open(mgr.auth_path, "r") as f:
                        content = f.read()
                    json.loads(content)
                except json.JSONDecodeError as e:
                    corruption_found.append(str(e))
                    break
                except FileNotFoundError:
                    pass
                time.sleep(0.001)

        def writer():
            for i in range(50):
                mgr.create_user(f"stress{i}", f"pw{i}")

        reader_thread = threading.Thread(target=reader)
        writer_thread = threading.Thread(target=writer)

        reader_thread.start()
        writer_thread.start()
        writer_thread.join()
        stop_event.set()
        reader_thread.join()

        assert not corruption_found, f"Corrupt JSON detected: {corruption_found[0]}"
