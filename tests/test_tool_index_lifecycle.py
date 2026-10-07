"""ToolIndex startup prewarm and readiness lifecycle regression tests."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from src import tool_index as tool_index_module


class _FakeLane:
    name = "fastembed"

    def stats(self):
        return {
            "name": self.name,
            "model": "fake-embedder",
            "dimension": 8,
            "fingerprint": "lane-fingerprint",
            "count": 3,
            "healthy": True,
            "url": "secret-or-internal-value-must-not-be-reported",
        }


@pytest.fixture
def clean_tool_index(monkeypatch):
    tool_index_module.reset_tool_index()
    yield monkeypatch
    tool_index_module.reset_tool_index()


def _good_index_class(*, delay: float = 0.0):
    class GoodIndex:
        constructions = 0

        def __init__(self):
            type(self).constructions += 1
            if delay:
                time.sleep(delay)
            self._healthy = True
            self._fingerprint = "tool-fingerprint"
            self._lanes = [_FakeLane()]
            self.index_calls = 0
            self.probe_queries = []

        @property
        def healthy(self):
            return self._healthy

        def index_builtin_tools(self):
            self.index_calls += 1

        def retrieve(self, query, k=8):
            self.probe_queries.append((query, k))
            return ["bash", "read_file"][:k]

    return GoodIndex


def test_prewarm_initializes_and_exercises_semantic_retrieval(clean_tool_index):
    GoodIndex = _good_index_class()
    clean_tool_index.setattr(tool_index_module, "ToolIndex", GoodIndex)

    status = tool_index_module.prewarm_tool_index("inspect the repository")

    assert status["state"] == "ready"
    assert status["ready"] is True
    assert status["attempts"] == 1
    assert status["builtin_tools"] == len(tool_index_module.BUILTIN_TOOL_DESCRIPTIONS)
    assert status["fingerprint"] == "tool-fingerprint"
    assert status["probe_tools"] == ["bash", "read_file"]
    assert status["lanes"] == [{
        "name": "fastembed",
        "model": "fake-embedder",
        "dimension": 8,
        "fingerprint": "lane-fingerprint",
        "count": 3,
        "healthy": True,
    }]
    assert GoodIndex.constructions == 1
    assert tool_index_module.get_ready_tool_index() is not None


def test_concurrent_first_requests_share_one_initialization(clean_tool_index):
    GoodIndex = _good_index_class(delay=0.05)
    clean_tool_index.setattr(tool_index_module, "ToolIndex", GoodIndex)

    with ThreadPoolExecutor(max_workers=6) as pool:
        indexes = list(pool.map(lambda _: tool_index_module.get_tool_index(), range(6)))

    assert GoodIndex.constructions == 1
    assert all(index is indexes[0] for index in indexes)
    assert tool_index_module.get_tool_index_status()["state"] == "ready"


def test_failed_initialization_is_reported_and_retry_throttled(clean_tool_index):
    class BrokenIndex:
        constructions = 0

        def __init__(self):
            type(self).constructions += 1
            raise RuntimeError("embedding backend unavailable")

    clean_tool_index.setattr(tool_index_module, "ToolIndex", BrokenIndex)

    assert tool_index_module.get_tool_index() is None
    first = tool_index_module.get_tool_index_status()
    assert first["state"] == "degraded"
    assert first["ready"] is False
    assert first["attempts"] == 1
    assert first["error_type"] == "RuntimeError"
    assert 0 < first["retry_after_seconds"] <= tool_index_module._RETRY_INTERVAL

    assert tool_index_module.get_tool_index() is None
    assert BrokenIndex.constructions == 1
    assert tool_index_module.get_tool_index_status()["attempts"] == 1


def test_empty_retrieval_probe_marks_index_degraded(clean_tool_index):
    GoodIndex = _good_index_class()
    clean_tool_index.setattr(tool_index_module, "ToolIndex", GoodIndex)
    index = tool_index_module.get_tool_index()
    clean_tool_index.setattr(index, "retrieve", lambda *_args, **_kwargs: [])

    status = tool_index_module.prewarm_tool_index()

    assert status["state"] == "degraded"
    assert status["ready"] is False
    assert status["error_type"] == "RetrievalProbeEmpty"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("1", True),
        ("true", True),
        ("0", False),
        ("false", False),
        ("off", False),
    ],
)
def test_tool_index_prewarm_defaults_on_and_is_explicitly_opt_out(value, expected):
    environ = {} if value is None else {"ODYSSEUS_TOOL_INDEX_PREWARM": value}
    assert tool_index_module.tool_index_prewarm_enabled(environ) is expected


def test_reset_clears_lifecycle_state(clean_tool_index):
    GoodIndex = _good_index_class()
    clean_tool_index.setattr(tool_index_module, "ToolIndex", GoodIndex)
    assert tool_index_module.prewarm_tool_index()["ready"] is True

    tool_index_module.reset_tool_index()

    assert tool_index_module.get_tool_index_status() == {
        "state": "idle",
        "ready": False,
        "attempts": 0,
        "started_at": None,
        "completed_at": None,
        "duration_ms": None,
        "builtin_tools": 0,
        "fingerprint": "",
        "lanes": [],
        "error_type": None,
    }
