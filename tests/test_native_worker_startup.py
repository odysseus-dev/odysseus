"""Application startup enrollment, without running app services or a model."""
import ast
from pathlib import Path

import pytest

from src import native_workers


@pytest.mark.asyncio
async def test_first_start_initializes_once_without_certifying_legacy_idle(tmp_path):
    registry = native_workers.WorkerRegistry(tmp_path / 'process_resources' / 'native_workers' / 'index.json')
    await native_workers.initialize_producers(registry=registry)
    generation = registry._read()['generation']
    await native_workers.initialize_producers(registry=registry)
    assert registry._read()['generation'] == generation
    assert len(registry._read()['epochs']) == 1
    snapshot = registry.snapshot()
    assert snapshot['initialized'] and not snapshot['unknown']
    assert snapshot['workers'] == []
    assert snapshot['legacy_inventory_verified'] is False
    assert snapshot['signal_authorized'] is False


@pytest.mark.asyncio
async def test_startup_does_not_replace_lost_initialized_index(tmp_path):
    registry = native_workers.WorkerRegistry(tmp_path / 'index.json')
    registry.initialize()
    registry.path.unlink()
    with pytest.raises(native_workers.WorkerStateUnknown):
        await native_workers.initialize_producers(registry=registry)
    assert not registry.path.exists()


@pytest.mark.asyncio
async def test_startup_does_not_steal_a_live_fence(tmp_path):
    registry = native_workers.WorkerRegistry(tmp_path / 'index.json')
    registry.initialize()
    token = registry.fence()
    try:
        with pytest.raises(native_workers.AdmissionClosed):
            await native_workers.initialize_producers(registry=registry)
        assert registry.snapshot()['draining']
    finally:
        registry.resume(token)


def test_app_initializes_producers_before_starting_background_services():
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8'))
    startup = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_startup_event')
    awaits = [n for n in ast.walk(startup) if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)]
    enrolled = [n for n in awaits if isinstance(n.value.func, ast.Name) and n.value.func.id == 'initialize_producers']
    assert enrolled, 'Ordinary Uvicorn startup must enroll producers, not just RunOnly startup'
    webhook = next(n for n in ast.walk(startup) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute) and n.func.attr == 'set_loop')
    assert enrolled[0].lineno < webhook.lineno
