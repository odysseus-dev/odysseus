from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_copies_app_as_runtime_user():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY --chown=1000:1000 . ." in dockerfile
    assert "chown 1000:1000 /app" in dockerfile
    assert "services/cache/search services/cache/content" in dockerfile


def test_entrypoint_does_not_recursively_chown_app_tree():
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

    assert "for dir in /app /app/data /app/logs" not in entrypoint
    assert 'find "$dir" -not -uid "$PUID"' in entrypoint
    assert "repair_tree /app/logs" in entrypoint
    assert "repair_tree /app/.ssh" in entrypoint


def test_entrypoint_repairs_service_cache_for_non_default_puid():
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

    assert "/app/services" in entrypoint
    assert "/app/services/cache" in entrypoint
    assert "repair_tree /app/services/cache" in entrypoint


def test_entrypoint_prunes_large_cache_trees_from_data_repair():
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

    assert "ODYSSEUS_CHOWN_CACHE_TREES" in entrypoint
    assert "-path /app/data/local" in entrypoint
    assert "-path /app/data/huggingface" in entrypoint
    assert "repair_tree /app/.local" in entrypoint
    assert "repair_tree /app/.cache/huggingface" in entrypoint
