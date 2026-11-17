"""Session wiring for the release smoke suite.

The suite drives a real instance over HTTP. It never starts one: that is
`scripts/odysseus-smoke`'s job, which boots the worktree through
`scripts/odysseus-dev` and hands the details over in the environment.
Run under a plain `pytest` with no instance up, every scenario skips
with the reason rather than failing, so the full suite stays green.

Three environment values form the contract, and they are exactly what
`odysseus dev env` prints plus the dev admin account:

  APP_PORT                    - which instance, read through
                                `internal_api_base()`
  ODYSSEUS_ADMIN_USER         - the account to authenticate as
  ODYSSEUS_ADMIN_PASSWORD
  ODYSSEUS_DATA_DIR           - where the email fixture file goes, read
                                through `src.constants.DATA_DIR`
"""
from __future__ import annotations

import os

import httpx
import pytest

from src.constants import internal_api_base
from tests.helpers.cli_loader import load_script
from tests.smoke import areas
from tests.smoke.stub_provider import MODEL_PRIMARY, StubProvider

# How long a smoke request may take. Generous: the first turn through a
# cold agent path does real work, and a timeout here reads as a product
# failure, which is the one thing this suite must not get wrong.
REQUEST_TIMEOUT_SECONDS = 120.0

# Auth and endpoint routes the suite drives directly. Kept here so a
# route rename shows up in one place rather than twelve.
LOGIN_PATH = "/api/auth/login"
HEALTH_PATH = "/api/health"
ENDPOINTS_PATH = "/api/model-endpoints"
SESSION_PATH = "/api/session"

_NO_PORT = (
    "APP_PORT is not set, so there is no instance to drive. Run the suite "
    "with `scripts/odysseus-smoke`, which boots this worktree and exports it."
)


def _reserved_ports() -> dict:
    """`odysseus dev`'s own refuse-list, read from the launcher.

    The smoke suite writes and deletes real records, so pointing it at a
    port that means something - a normal launch of this checkout, the
    machine's production instance - has to be impossible rather than
    merely discouraged. Reusing the launcher's table keeps one source of
    truth instead of a second copy that can drift.
    """
    try:
        return dict(load_script("odysseus-dev").RESERVED_PORTS)
    except Exception:  # pragma: no cover - launcher absent or unloadable
        return {}


@pytest.fixture(scope="session")
def base_url() -> str:
    """The instance this run drives, or a skip explaining why there is none."""
    port = (os.environ.get("APP_PORT") or "").strip()
    if not port:
        pytest.skip(_NO_PORT)
    reason = _reserved_ports().get(int(port)) if port.isdigit() else None
    if reason:
        pytest.skip(
            f"APP_PORT={port} is {reason}. The smoke suite creates and deletes "
            f"real records, so it refuses to run against that instance."
        )
    return internal_api_base()


@pytest.fixture(scope="session")
def account() -> dict:
    user = (os.environ.get("ODYSSEUS_ADMIN_USER") or "").strip()
    password = os.environ.get("ODYSSEUS_ADMIN_PASSWORD") or ""
    if not user or not password:
        pytest.skip(
            "ODYSSEUS_ADMIN_USER / ODYSSEUS_ADMIN_PASSWORD are not set, so the "
            "suite cannot authenticate. Run it with `scripts/odysseus-smoke`."
        )
    return {"username": user, "password": password}


def _new_client(base_url: str, account: dict) -> httpx.Client:
    """An authenticated client, or a skip naming what the instance said."""
    client = httpx.Client(base_url=base_url, timeout=REQUEST_TIMEOUT_SECONDS,
                          follow_redirects=True)
    try:
        client.get(HEALTH_PATH)
    except httpx.HTTPError as exc:
        client.close()
        pytest.skip(f"no instance answering at {base_url} ({exc}). Boot one with "
                    f"`odysseus dev up`, or run `scripts/odysseus-smoke`.")
    response = client.post(LOGIN_PATH, json=account)
    if response.status_code != 200:
        client.close()
        pytest.skip(
            f"could not log in as {account['username']} at {base_url}: "
            f"HTTP {response.status_code}. The recorded credentials may not "
            f"match this instance's data dir."
        )
    return client


@pytest.fixture(scope="session")
def client(base_url, account):
    """One authenticated session shared by every scenario."""
    handle = _new_client(base_url, account)
    yield handle
    handle.close()


@pytest.fixture
def fresh_client(base_url, account):
    """A second authenticated session, for asserting something persisted.

    Reading a value back on the same cookie proves the request handler
    returned it. Reading it back on a new login is the closest a test can
    get to the user reloading the page.
    """
    handle = _new_client(base_url, account)
    yield handle
    handle.close()


@pytest.fixture(scope="session")
def stub_provider():
    """The deterministic provider every model-backed scenario talks to."""
    with StubProvider() as provider:
        yield provider


@pytest.fixture(scope="session")
def stub_endpoint(client, stub_provider) -> str:
    """Register the stub as a model endpoint and return its id.

    Registered as `endpoint_kind=local` so the app treats it the way it
    treats a Cookbook-served model rather than probing it as a hosted
    API, and removed afterwards so a `--keep-up` instance is not left
    pointing at a port that has gone away.
    """
    response = client.post(ENDPOINTS_PATH, data={
        "name": "odysseus-smoke-stub",
        "base_url": stub_provider.base_url,
        "endpoint_kind": "local",
    })
    if response.status_code != 200:
        pytest.skip(
            f"the instance would not register the stub provider at "
            f"{stub_provider.base_url}: HTTP {response.status_code} "
            f"{response.text[:200]}"
        )
    body = response.json()
    endpoint_id = str(body.get("id") or "")
    if not endpoint_id:
        pytest.skip(f"the endpoint the instance registered has no id: {body}")
    if MODEL_PRIMARY not in (body.get("models") or []):
        pytest.skip(
            f"the instance did not discover {MODEL_PRIMARY} on the stub "
            f"provider; it saw {body.get('models')}"
        )
    yield endpoint_id
    client.delete(f"{ENDPOINTS_PATH}/{endpoint_id}")


@pytest.fixture
def chat_session(client, stub_endpoint):
    """A chat session bound to the stub provider, deleted afterwards."""
    response = client.post(SESSION_PATH, data={
        "name": "odysseus-smoke",
        "endpoint_id": stub_endpoint,
        "model": MODEL_PRIMARY,
    })
    assert response.status_code == 200, response.text
    session_id = response.json()["id"]
    yield session_id
    client.delete(f"{SESSION_PATH}/{session_id}")


# --------------------------------------------------------------------------
# The per-area table
# --------------------------------------------------------------------------
# One row per area in `areas.COVERED`, built from the outcomes pytest
# reports rather than from anything a test asserts about itself, so a
# module that never ran cannot report a pass.

_outcomes: dict[str, list[str]] = {}
_details: dict[str, str] = {}
_checks: dict[str, int] = {}


def _skip_reason(report) -> str:
    """The reason text out of a skip report, best effort."""
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        reason = str(longrepr[2] or "")
        return reason.removeprefix("Skipped: ").strip()
    return str(longrepr or "").strip()


def pytest_runtest_logreport(report):
    key = areas.area_for_module(os.path.basename(str(report.fspath)))
    if key is None:
        return
    if report.skipped:
        _outcomes.setdefault(key, []).append(areas.SKIP)
        _details.setdefault(key, _skip_reason(report))
        return
    if report.failed:
        _outcomes.setdefault(key, []).append(areas.FAIL)
        _details[key] = f"{report.when} failed: {report.nodeid.split('::')[-1]}"
        return
    if report.when == "call" and report.passed:
        _outcomes.setdefault(key, []).append(areas.PASS)
        _checks[key] = _checks.get(key, 0) + 1


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not _outcomes:
        return
    results = {}
    for key, outcomes in _outcomes.items():
        results[key] = {
            "result": areas.resolve(outcomes),
            "checks": _checks.get(key, 0),
            "detail": _details.get(key, ""),
        }

    if all(entry["result"] == areas.SKIP for entry in results.values()):
        reasons = {entry["detail"] for entry in results.values() if entry["detail"]}
        terminalreporter.write_line("")
        terminalreporter.write_line(
            "release smoke suite skipped: " + (
                reasons.pop() if len(reasons) == 1 else "; ".join(sorted(reasons))
            )
        )
        return

    header = f"Odysseus release smoke - {internal_api_base()}"
    terminalreporter.write_line("")
    terminalreporter.write_line(areas.render_table(results, header=header))
