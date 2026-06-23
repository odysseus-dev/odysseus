import os
import time

from routes.email_helpers import sweep_compose_uploads, COMPOSE_UPLOADS_DIR


def test_sweep_removes_old_but_keeps_fresh():
    """A staged upload older than the TTL is removed; a fresh one survives."""
    COMPOSE_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    old = COMPOSE_UPLOADS_DIR / "test-sweep-old.bin"
    fresh = COMPOSE_UPLOADS_DIR / "test-sweep-fresh.bin"
    old.write_bytes(b"old")
    fresh.write_bytes(b"fresh")
    try:
        ten_days_ago = time.time() - 10 * 24 * 3600
        os.utime(old, (ten_days_ago, ten_days_ago))

        removed = sweep_compose_uploads(ttl_seconds=7 * 24 * 3600)

        assert removed >= 1
        assert not old.exists()
        assert fresh.exists()
    finally:
        old.unlink(missing_ok=True)
        fresh.unlink(missing_ok=True)


def test_sweep_keeps_files_within_ttl():
    """An old file is kept when the TTL window is wide enough to cover it."""
    COMPOSE_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    f = COMPOSE_UPLOADS_DIR / "test-sweep-keep.bin"
    f.write_bytes(b"x")
    old_time = time.time() - 10 * 24 * 3600
    os.utime(f, (old_time, old_time))
    try:
        sweep_compose_uploads(ttl_seconds=365 * 24 * 3600)
        assert f.exists()
    finally:
        f.unlink(missing_ok=True)
