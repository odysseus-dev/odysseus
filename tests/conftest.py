"""Shared test configuration - ensure project root is on sys.path and stub heavy deps."""
import sys
import os
import types
import importlib.util
from unittest.mock import MagicMock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Isolate import-time database and filesystem defaults before collection.
# File-backed databases remain fixture-owned. Cleanup restores the caller.
# Keep the existing generated environment-reference locations stable.
_database_environment = pytest.MonkeyPatch()
_database_environment.setenv("DATABASE_URL", "sqlite:///:memory:")
from tests.helpers.worker_runtime import bootstrap_runtime, configure_runtime
_runtime_environment = bootstrap_runtime()

# Pre-import real heavy modules BEFORE any test file's module-level stubs can
# replace them with MagicMock. Some test files (e.g. test_llm_core_sanitize_*)
# stub sqlalchemy/core.database at module scope with `if mod not in sys.modules`,
# which fires during collection. If the real module hasn't been imported yet,
# the stub wins and contaminates every subsequent test that needs the real ORM.
try:
    import sqlalchemy  # noqa: F401
    import sqlalchemy.orm  # noqa: F401
    import core.database  # noqa: F401
    import src.database
except ImportError:
    pass  # not installed - the stubs below will handle it

def _has_module(mod_name: str) -> bool:
    try:
        return importlib.util.find_spec(mod_name) is not None
    except (ImportError, ValueError):
        return False


# Stub optional dependencies only when they are not installed. Do not replace
# real FastAPI/Starlette/Pydantic modules: route tests import their subpackages.
for mod_name in [
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.types", "sqlalchemy.ext",
    "sqlalchemy.ext.hybrid", "sqlalchemy.sql", "sqlalchemy.sql.expression",
    "sqlalchemy.sql.sqltypes", "bcrypt", "pyotp",
    "httpx", "fastapi", "fastapi.responses", "fastapi.routing",
    "starlette", "starlette.responses", "starlette.middleware", "starlette.middleware.base",
    "pydantic",
]:
    if mod_name not in sys.modules and not _has_module(mod_name):
        sys.modules[mod_name] = MagicMock()

if "src.database" not in sys.modules:
    _db = types.ModuleType("src.database")
    _db.SessionLocal = MagicMock()
    _db.ModelEndpoint = MagicMock()
    sys.modules["src.database"] = _db

# Pre-import core.models before test_agent_loop.py's module-level stubs
# run (it replaces sys.modules['core.models'] with a MagicMock during
# collection, which breaks session import in subsequent tests).
import core.models  # noqa: E402

def pytest_addoption(parser):
    """Add ``--shard N/M`` so CI can run the suite as parallel sections."""
    group = parser.getgroup("sharding", "parallel test sharding")
    group.addoption(
        "--shard",
        action="store",
        default=None,
        metavar="N/M",
        help=(
            "run only shard N of M (1-based), e.g. --shard 1/4. Shards partition "
            "the suite by test file, so together they run every test exactly "
            "once. See tests/_shards.py."
        ),
    )


def _shard_spec(config):
    """Parse the ``--shard`` option into a ShardSpec, or None when unset."""
    from tests._shards import ShardSpecError, parse_shard_spec

    value = config.getoption("shard")
    if value is None:
        return None
    try:
        return parse_shard_spec(value)
    except ShardSpecError as error:
        # UsageError fails the run immediately rather than silently running a
        # subset nobody asked for - a dropped shard is invisible in a green CI.
        raise pytest.UsageError(str(error)) from error


def pytest_configure(config):
    """Register the dynamic taxonomy ``sub_*`` markers before collection.

    The stable ``area_*`` markers are declared in ``pyproject.toml``. The
    per-file ``sub_*`` markers are derived from the test filenames here so that
    unknown-mark warnings still surface genuine typos outside the taxonomy. This
    only registers marker names; it imports no production module.
    """
    config.add_cleanup(_database_environment.undo)

    import pathlib
    from tests._taxonomy import discover_markers

    tests_dir = pathlib.Path(__file__).parent
    paths = list(tests_dir.rglob("test_*.py")) + list(tests_dir.rglob("*_test.py"))
    for marker_name in discover_markers(paths):
        if marker_name.startswith("sub_"):
            config.addinivalue_line("markers", f"{marker_name}: taxonomy sub-area marker")

    # Validate --shard before collection so a bad selector fails the run up
    # front instead of after a few minutes of collecting.
    _shard_spec(config)


def pytest_collection_modifyitems(config, items):
    """Tag each collected test with its taxonomy markers, then apply ``--shard``.

    Tagging is collection-time only: it adds markers and nothing else. It does
    not skip, reorder, mutate fixtures or the environment, or import any
    production module. See ``tests/_taxonomy.py`` for the classification rules.

    Sharding deselects the test files that belong to another shard. It runs
    after collection, so every test module is still imported, in the same order,
    in every shard - the import-time stubbing above behaves identically whether
    the suite runs whole or as one section of it. Only the deselected tests'
    call phase is skipped. See ``tests/_shards.py`` for the partition.
    """
    from tests._taxonomy import markers_for_path

    for item in items:
        path = getattr(item, "path", None) or item.fspath
        for marker_name in markers_for_path(path):
            item.add_marker(getattr(pytest.mark, marker_name))

    spec = _shard_spec(config)
    if spec is None or spec.selects_everything:
        return

    from tests._shards import accumulate_file_weights, plan_shards, relative_file_key

    root = getattr(config, "rootpath", None)
    keys = [
        relative_file_key(getattr(item, "path", None) or item.fspath, root)
        for item in items
    ]
    weights = accumulate_file_weights(
        (key, item.get_closest_marker("slow") is not None)
        for key, item in zip(keys, items)
    )
    selected_files = plan_shards(weights, spec.count)[spec.index - 1]

    selected, deselected = [], []
    for key, item in zip(keys, items):
        (selected if key in selected_files else deselected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected


@pytest.fixture(scope="session", autouse=True)
def _serve_test_static():
    """Serve static assets on loopback for the browser integration tests.

    Binds an ephemeral port so several worktrees can run their own suite at the
    same time, and publishes the resulting origin through
    ``ODYSSEUS_TEST_STATIC_ORIGIN``.  The browser tests shell out to node, which
    inherits the environment, so the snippets read the origin from
    ``process.env`` instead of hardcoding a port.

    Set ``ODYSSEUS_TEST_STATIC_PORT`` to pin a specific port when something
    outside pytest has to reach this server.
    """
    import os
    import threading
    import http.server
    import socketserver
    from pathlib import Path

    root_dir = Path(__file__).resolve().parent.parent

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root_dir), **kwargs)

        def log_message(self, format, *args):
            pass

        def guess_type(self, path):
            if path.endswith(".js") or path.endswith(".mjs"):
                return "application/javascript"
            if path.endswith(".css"):
                return "text/css"
            return super().guess_type(path)

    class _Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = daemon_threads = True

    requested = int(os.environ.get("ODYSSEUS_TEST_STATIC_PORT") or 0)
    if not 0 <= requested <= 65535:
        raise ValueError("ODYSSEUS_TEST_STATIC_PORT must be between 0 and 65535")
    try:
        server = _Server(("127.0.0.1", requested), _Handler)
    except OSError as exc:
        # Port 0 cannot collide, so this only fires for an explicit pin.
        raise RuntimeError(
            f"ODYSSEUS_TEST_STATIC_PORT={requested} is not bindable; unset it to "
            "let the browser tests pick an ephemeral port"
        ) from exc

    origin = f"http://127.0.0.1:{server.server_address[1]}"
    previous_origin = os.environ.get("ODYSSEUS_TEST_STATIC_ORIGIN")
    os.environ["ODYSSEUS_TEST_STATIC_ORIGIN"] = origin

    # One thread per connection: Chromium can hold a speculative connection
    # open without a request, which stalled serial service for ~30s.
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield origin
    finally:
        if previous_origin is None:
            os.environ.pop("ODYSSEUS_TEST_STATIC_ORIGIN", None)
        else:
            os.environ["ODYSSEUS_TEST_STATIC_ORIGIN"] = previous_origin
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="session")
def _effects_store_root(tmp_path_factory):
    return tmp_path_factory.mktemp("effects")


@pytest.fixture(autouse=True)
def _isolated_effects_store(_effects_store_root):
    """Keep durable effect claims out of the developer's real data directory.

    Restored manually: requesting the shared ``monkeypatch`` here would move
    its teardown after ``_no_leaked_module_stubs`` and misreport test stubs.
    """
    from src.agent_runtime import effect_log

    previous = effect_log.EFFECTS_DIR
    effect_log.EFFECTS_DIR = str(_effects_store_root)
    try:
        yield
    finally:
        effect_log.EFFECTS_DIR = previous


@pytest.fixture(autouse=True)
def _no_leaked_module_stubs():
    """Fail the test that leaves a bare ``src.*``/``core.*`` stub behind.

    Several test modules install empty stand-in modules so an import-heavy
    production module can be loaded under the mocks above. When one of those
    writes is not undone, the stub stays in ``sys.modules`` for the rest of the
    session and every later test that imports the real module silently gets an
    empty one instead. The suite still passes as a whole, because the victims
    usually run before the leak; it only breaks under a different collection
    order, which is why this class of bug reaches CI green.

    This fixture is declared in the root conftest, so it is set up before any
    test-module fixture and torn down after all of them — a stub that a test's
    own teardown removes is not reported. The leaked entries are dropped here
    as well as reported, so the failure stays attributed to the test that
    introduced it instead of cascading into the rest of the run.

    Bare stubs present before the test starts are ignored: this guards against
    new leaks, it does not police import state the session began with.
    """
    from tests.helpers.import_state import bare_module_stubs, clear_module

    before = bare_module_stubs()
    yield
    leaked = sorted(bare_module_stubs() - before)
    if not leaked:
        return
    for name in leaked:
        clear_module(name)
    pytest.fail(
        "test left bare module stub(s) in sys.modules: "
        + ", ".join(leaked)
        + ". Register the stub through monkeypatch.setitem(sys.modules, ...) "
        "or tests.helpers.import_state.preserve_import_state so it is undone "
        "at teardown.",
        pytrace=False,
    )


@pytest.fixture(autouse=True)
def _no_context_window_network_probe(request):
    """Keep the turn context-window resolver offline in tests.

    Compact turns resolve their window before the first model request, and
    most tests drive them with placeholder endpoints. Only the resolver's two
    I/O edges are replaced: URL resolution (DNS/Tailscale lookups) and its
    HTTP client, which records each attempted metadata request and fails it
    as a transport error. Everything else (route wiring, caching, credential
    scoping, evidence selection) runs for real, so an unintended extra probe
    stays visible through the ``context_probe_ledger`` fixture.

    Modules that install their own fake client opt out with a module-level
    ``CONTEXT_PROBE_NETWORK = True``.
    """
    ledger = []
    if getattr(request.module, "CONTEXT_PROBE_NETWORK", False):
        yield ledger
        return
    try:
        from src.agent_runtime import context_resolution
    except Exception:
        yield ledger
        return

    class _OfflineMetadataClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            ledger.append({"url": url, "headers": dict(headers or {})})
            raise context_resolution.httpx.ConnectError("network disabled in tests")

    def _offline_provider_urls(endpoint_url):
        base = endpoint_url.split("/v1")[0] if "/v1" in endpoint_url else endpoint_url.rstrip("/")
        return base + "/v1/models", endpoint_url

    # A private patcher keeps the shared ``monkeypatch`` fixture's teardown
    # order unchanged for tests that check their own sys.modules hygiene.
    patcher = pytest.MonkeyPatch()
    patcher.setattr(context_resolution, "_http_client", _OfflineMetadataClient)
    patcher.setattr(context_resolution, "_provider_urls", _offline_provider_urls)
    context_resolution.clear_probe_cache()
    try:
        yield ledger
    finally:
        patcher.undo()
        context_resolution.clear_probe_cache()


@pytest.fixture
def context_probe_ledger(_no_context_window_network_probe):
    """Metadata requests the context resolver attempted during this test."""
    return _no_context_window_network_probe


# Before pytest's tmpdir plugin reads the basetemp this sets.
@pytest.hookimpl(specname="pytest_configure", tryfirst=True)
def pytest_configure_worker_runtime(config):
    configure_runtime(config, _runtime_environment)


@pytest.hookimpl(specname="pytest_collection_modifyitems", tryfirst=True)
def pytest_collection_worker_runtime(items):
    # Mark before pytest applies -m. The final guard also respects --shard.
    for item in items:
        if "smoke" in item.path.parts:
            item.add_marker(pytest.mark.serial)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_finish(session):
    """Refuse shared live resources after marker and shard deselection."""
    config = session.config
    parallel = bool(getattr(config.option, "numprocesses", None)) or hasattr(config, "workerinput")
    if (os.environ.get("APP_PORT") and parallel
            and any(item.get_closest_marker("serial") for item in session.items)):
        message = (
            "live smoke tests share one external application, accounts, and endpoints; "
            "run tests/smoke with -n 0"
        )
        # A worker UsageError here races xdist's collection notification and
        # can lose its message. Emit a normal collection failure before xdist
        # sees any runnable items. Nothing may contact the external instance.
        config.hook.pytest_collectreport(report=pytest.CollectReport(
            nodeid="tests/smoke", outcome="failed", longrepr=message, result=[],
        ))
        session.items.clear()
