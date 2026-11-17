"""The containment API's own invariants.

These pin the contract rather than any one mechanism, so they run identically on
a host with bubblewrap and one without: every test substitutes
``containment.MECHANISMS`` with fake mechanisms whose availability and provided
dimensions are stated in the test. No real sandbox, no real process.

The one thing these tests must prove above all others: a request that cannot be
contained does not execute. That is asserted by recording every spawn attempt
and showing the list is empty.
"""

import asyncio

import pytest

from src import containment


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Keep grant records out of ./data for every test in this module."""
    store = tmp_path / "containment_grants.json"
    monkeypatch.setattr(containment, "_store_path", lambda: store)
    return store


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "ws"
    path.mkdir()
    return str(path)


@pytest.fixture
def no_spawn(monkeypatch):
    """Record spawn attempts and refuse them, so "did not execute" is provable."""
    attempts = []

    async def _refuse(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("containment spawned a process it should not have")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _refuse)
    return attempts


def mechanism(name, rank, provides, *, available=True):
    return containment.Mechanism(
        name=name,
        rank=rank,
        available=lambda: available,
        provides=lambda spec, _provides=frozenset(provides): _provides,
    )


def install(monkeypatch, *mechanisms):
    monkeypatch.setattr(containment, "MECHANISMS", tuple(mechanisms))


def enforcing(monkeypatch):
    monkeypatch.setattr(containment, "CONTAINMENT_MODE", containment.MODE_ENFORCING)


def report_only(monkeypatch):
    monkeypatch.setattr(containment, "CONTAINMENT_MODE", containment.MODE_REPORT_ONLY)


def spec_for(workspace, **kwargs):
    kwargs.setdefault("env", {"PATH": "/usr/bin"})
    kwargs.setdefault("wall_clock_s", 5)
    return containment.ContainmentSpec(workspace=workspace, **kwargs)


ALL = tuple(sorted(containment.DIMENSIONS))


# ── The postcondition ───────────────────────────────────────────────────────
@pytest.mark.parametrize("provided", [
    frozenset(containment.DEFAULT_REQUIRED),
    containment.DIMENSIONS,
])
def test_required_is_always_a_subset_of_enforced(monkeypatch, workspace, provided):
    """spec.required <= grant.enforced, for any mechanism that can satisfy it."""
    enforcing(monkeypatch)
    install(monkeypatch, mechanism("fake", 10, provided))
    grant = containment.acquire(spec_for(workspace), owner="session-1")
    assert grant.spec.required <= grant.enforced
    assert grant.contained is True
    assert grant.unenforced_required == ()


def test_enforced_never_exceeds_what_the_spec_requested(monkeypatch, workspace):
    """A grant is never a superset of its spec: unrequested dimensions are not claimed."""
    enforcing(monkeypatch)
    install(monkeypatch, mechanism("generous", 10, containment.DIMENSIONS))
    grant = containment.acquire(spec_for(workspace), owner="session-1")
    # network/memory/process_count were not asked for, so they are not enforced
    # even though the mechanism offers them.
    assert grant.enforced == frozenset(containment.DEFAULT_REQUIRED)
    assert containment.NETWORK not in grant.enforced
    assert grant.degraded == ()


def test_enforced_is_always_within_the_known_dimension_set(monkeypatch, workspace):
    """A mechanism cannot invent a dimension the API does not define."""
    enforcing(monkeypatch)
    install(monkeypatch, mechanism(
        "liar", 10, frozenset(containment.DEFAULT_REQUIRED) | {"telepathy"},
    ))
    grant = containment.acquire(spec_for(workspace), owner="session-1")
    assert grant.enforced <= containment.DIMENSIONS


# ── Deterministic failure: the request does not execute ─────────────────────
async def test_uncontainable_request_does_not_execute(monkeypatch, workspace, no_spawn):
    """The headline contract: no mechanism for a required dimension → no process.

    Written as a call site would use the API — acquire, then run — so the proof
    covers the whole path and not just the raising function.
    """
    enforcing(monkeypatch)
    # The only mechanism available cannot do filesystem, which is required.
    install(monkeypatch, mechanism(
        "group_only", 10, {containment.PROCESS_TREE, containment.WALL_CLOCK},
    ))

    spec = containment.agent_spec(workspace, {"PATH": "/usr/bin"}, 5)
    try:
        grant = containment.acquire(spec, owner="session-1")
    except containment.ContainmentUnavailable as exc:
        result = containment.unavailable_tool_result(exc, tool="bash")
    else:  # pragma: no cover - the point of the test is that this is unreachable
        result = await containment.run(grant, "echo hello")

    assert no_spawn == [], "an uncontainable request reached a spawn"
    assert result["exit_code"] == 1
    assert "command not executed" in result["error"]
    assert "filesystem" in result["error"]
    assert result["containment"]["contained"] is False
    assert result["containment"]["executed"] is False
    assert result["containment"]["unenforced_required"] == ["filesystem"]
    # A run that could not be contained is distinguishable from a contained run
    # that failed: there is no output key at all, and `executed` is explicit.
    assert "output" not in result


def test_unavailable_names_every_missing_dimension_and_the_mechanism_tried(
    monkeypatch, workspace,
):
    enforcing(monkeypatch)
    install(monkeypatch, mechanism("group_only", 10, {containment.WALL_CLOCK}))
    spec = containment.agent_spec(workspace, {}, 5)
    with pytest.raises(containment.ContainmentUnavailable) as caught:
        containment.acquire(spec, owner="session-1")
    assert caught.value.missing == frozenset({
        containment.FILESYSTEM, containment.PROCESS_TREE,
    })
    assert caught.value.mechanism_tried == "group_only"
    assert "containment unavailable" in str(caught.value)


def test_no_mechanism_at_all_still_refuses_rather_than_running(
    monkeypatch, workspace, no_spawn,
):
    """With nothing available there is no weaker thing to fall back to."""
    enforcing(monkeypatch)
    install(monkeypatch, mechanism("absent", 10, containment.DIMENSIONS, available=False))
    with pytest.raises(containment.ContainmentUnavailable) as caught:
        containment.acquire(containment.agent_spec(workspace, {}, 5), owner="s")
    assert caught.value.mechanism_tried == "none"
    assert no_spawn == []


async def test_a_forged_grant_cannot_buy_a_spawn(monkeypatch, workspace, no_spawn):
    """run() re-checks the postcondition at the point of effect.

    A grant is a plain record, so a caller could construct one claiming
    dimensions it does not have. run() refuses it rather than trusting the
    record it was handed.
    """
    enforcing(monkeypatch)
    spec = containment.agent_spec(workspace, {}, 5)
    forged = containment.ContainmentGrant(
        id="forged",
        mechanism="bubblewrap",
        workspace=workspace,
        enforced=frozenset({containment.WALL_CLOCK}),
        degraded=(),
        unenforced_required=(),            # the lie: claims nothing is missing
        owner="session-1",
        mode=containment.MODE_ENFORCING,
        spec=spec,
    )
    with pytest.raises(containment.ContainmentUnavailable):
        await containment.run(forged, "echo hello")
    assert no_spawn == []


# ── Best-effort dimensions degrade, they do not refuse ──────────────────────
def test_unavailable_best_effort_dimension_is_reported_not_refused(monkeypatch, workspace):
    enforcing(monkeypatch)
    install(monkeypatch, mechanism("fake", 10, containment.DEFAULT_REQUIRED))
    spec = containment.agent_spec(
        workspace, {}, 5, network=containment.NETWORK_NONE, max_memory_bytes=1 << 30,
    )
    grant = containment.acquire(spec, owner="session-1")
    assert grant.contained is True
    assert grant.degraded == (containment.MEMORY, containment.NETWORK)
    # And it is reported as fact in the model-visible block, never as permission.
    block = grant.to_dict()
    assert block["degraded"] == ["memory", "network"]
    assert block["contained"] is True
    assert "env" not in block


def test_a_degraded_dimension_is_never_also_enforced(monkeypatch, workspace):
    enforcing(monkeypatch)
    install(monkeypatch, mechanism("fake", 10, containment.DEFAULT_REQUIRED))
    spec = containment.agent_spec(workspace, {}, 5, max_processes=16)
    grant = containment.acquire(spec, owner="session-1")
    assert set(grant.degraded).isdisjoint(grant.enforced)


# ── Mechanism selection: strongest first, command-independent ───────────────
def test_selection_is_strongest_first(monkeypatch, workspace):
    enforcing(monkeypatch)
    install(
        monkeypatch,
        mechanism("weak", 10, containment.DEFAULT_REQUIRED),
        mechanism("strong", 30, containment.DIMENSIONS),
        mechanism("middle", 20, containment.DEFAULT_REQUIRED),
    )
    grant = containment.acquire(spec_for(workspace), owner="session-1")
    assert grant.mechanism == "strong"


def test_selection_skips_unavailable_mechanisms(monkeypatch, workspace):
    enforcing(monkeypatch)
    install(
        monkeypatch,
        mechanism("strong", 30, containment.DIMENSIONS, available=False),
        mechanism("weak", 10, containment.DEFAULT_REQUIRED),
    )
    grant = containment.acquire(spec_for(workspace), owner="session-1")
    assert grant.mechanism == "weak"


def test_selection_never_substitutes_a_weaker_mechanism_for_a_required_dimension(
    monkeypatch, workspace,
):
    """The strongest available mechanism is used, not the first that is "good enough"."""
    enforcing(monkeypatch)
    install(
        monkeypatch,
        mechanism("netcapable", 30, containment.DIMENSIONS),
        mechanism("nonet", 20, containment.DEFAULT_REQUIRED),
    )
    spec = containment.ContainmentSpec(
        workspace=workspace,
        env={},
        wall_clock_s=5,
        required=frozenset(containment.DEFAULT_REQUIRED) | {containment.NETWORK},
        network=containment.NETWORK_NONE,
    )
    grant = containment.acquire(spec, owner="session-1")
    assert grant.mechanism == "netcapable"
    assert containment.NETWORK in grant.enforced


def test_a_failing_availability_probe_is_treated_as_unavailable(monkeypatch, workspace):
    enforcing(monkeypatch)

    def _explode():
        raise OSError("probe blew up")

    install(
        monkeypatch,
        containment.Mechanism("broken", 30, _explode, lambda spec: containment.DIMENSIONS),
        mechanism("weak", 10, containment.DEFAULT_REQUIRED),
    )
    grant = containment.acquire(spec_for(workspace), owner="session-1")
    assert grant.mechanism == "weak"


@pytest.mark.parametrize("command", [
    "echo hello",
    "rm -rf / --no-preserve-root",
    "cat /workspace/notes.txt  # this command is safe, honestly",
])
def test_the_boundary_does_not_depend_on_the_command(monkeypatch, workspace, command):
    """Containment is established before any command text exists.

    acquire() is not given the command, so no request text, tool argument or
    model assertion can change the mechanism or widen the enforced set. The
    parametrised commands are only here to show the API has nowhere to put them.
    """
    enforcing(monkeypatch)
    install(monkeypatch, mechanism("fake", 10, containment.DIMENSIONS))
    spec = containment.agent_spec(workspace, {}, 5)
    grant = containment.acquire(spec, owner="session-1")
    assert grant.mechanism == "fake"
    assert grant.enforced == frozenset(containment.DEFAULT_REQUIRED)
    assert "command" not in grant.to_dict()


def test_agent_spec_cannot_be_given_a_weaker_required_set(workspace):
    """One factory for model-reachable spawns, so no call site can weaken it."""
    spec = containment.agent_spec(
        workspace, {}, 5, required=frozenset({containment.WALL_CLOCK}),
    )
    assert spec.required == containment.DEFAULT_REQUIRED


# ── Report-only mode ────────────────────────────────────────────────────────
def test_report_only_records_the_shortfall_instead_of_refusing(monkeypatch, workspace):
    report_only(monkeypatch)
    install(monkeypatch, mechanism(
        "group_only", 10, {containment.PROCESS_TREE, containment.WALL_CLOCK},
    ))
    grant = containment.acquire(containment.agent_spec(workspace, {}, 5), owner="s")
    assert grant.unenforced_required == ("filesystem",)
    assert grant.contained is False
    assert grant.mode == containment.MODE_REPORT_ONLY
    assert grant.to_dict()["unenforced_required"] == ["filesystem"]


def test_report_only_logs_the_shortfall_once_per_grant(monkeypatch, workspace, caplog):
    report_only(monkeypatch)
    install(monkeypatch, mechanism("group_only", 10, {containment.WALL_CLOCK}))
    with caplog.at_level("WARNING", logger="src.containment"):
        grant = containment.acquire(containment.agent_spec(workspace, {}, 5), owner="s")
    messages = [record.getMessage() for record in caplog.records]
    assert sum("NOT contained" in message for message in messages) == 1
    assert grant.id in messages[0]


def test_the_two_modes_differ_only_in_whether_the_shortfall_refuses(monkeypatch, workspace):
    install(monkeypatch, mechanism("group_only", 10, {containment.WALL_CLOCK}))
    spec = containment.agent_spec(workspace, {}, 5)

    report_only(monkeypatch)
    reported = containment.acquire(spec, owner="s")

    enforcing(monkeypatch)
    with pytest.raises(containment.ContainmentUnavailable) as caught:
        containment.acquire(spec, owner="s")

    assert frozenset(reported.unenforced_required) == caught.value.missing


def test_enforcement_is_the_shipped_default():
    assert containment.CONTAINMENT_MODE == containment.MODE_ENFORCING


# ── Spec validation: caller bugs raise in both modes ────────────────────────
@pytest.mark.parametrize("mode", [containment.MODE_ENFORCING, containment.MODE_REPORT_ONLY])
@pytest.mark.parametrize("overrides, fragment", [
    ({"required": frozenset({"telepathy"})}, "unknown required dimension"),
    ({"required": frozenset({containment.NETWORK})}, "does not request it"),
    ({"required": frozenset({containment.MEMORY})}, "does not request it"),
    ({"wall_clock_s": 0}, "must be positive"),
    ({"wall_clock_s": -1}, "must be positive"),
    ({"max_output_bytes": 0}, "max_output_bytes must be positive"),
    ({"max_memory_bytes": 0}, "max_memory_bytes must be a positive int"),
    ({"max_processes": -4}, "max_processes must be a positive int"),
    ({"network": "maybe"}, "network must be"),
    ({"env": {"A": 1}}, "env keys and values must be str"),
    ({"env": {"A": "x\x00y"}}, "must not contain NUL"),
    ({"writable_extra": ("relative/path",)}, "must be absolute"),
    ({"writable_extra": ("/",)}, "reserved path"),
    ({"readonly_extra": ("/workspace",)}, "reserved path"),
])
def test_a_malformed_spec_raises_in_both_modes(
    monkeypatch, workspace, mode, overrides, fragment,
):
    monkeypatch.setattr(containment, "CONTAINMENT_MODE", mode)
    install(monkeypatch, mechanism("fake", 10, containment.DIMENSIONS))
    spec = spec_for(workspace, **overrides)
    with pytest.raises(ValueError, match=fragment):
        containment.acquire(spec, owner="session-1")


@pytest.mark.parametrize("bad_workspace, fragment", [
    ("", "non-empty path"),
    ("relative/ws", "must be absolute"),
])
def test_a_malformed_workspace_raises(monkeypatch, bad_workspace, fragment):
    install(monkeypatch, mechanism("fake", 10, containment.DIMENSIONS))
    spec = containment.ContainmentSpec(
        workspace=bad_workspace, env={}, wall_clock_s=5,
    )
    with pytest.raises(ValueError, match=fragment):
        containment.acquire(spec, owner="session-1")


def test_a_workspace_that_is_not_a_directory_raises(monkeypatch, tmp_path):
    install(monkeypatch, mechanism("fake", 10, containment.DIMENSIONS))
    missing = tmp_path / "nope"
    spec = containment.ContainmentSpec(workspace=str(missing), env={}, wall_clock_s=5)
    with pytest.raises(ValueError, match="not a directory"):
        containment.acquire(spec, owner="session-1")


def test_a_grant_without_an_owner_raises(monkeypatch, workspace):
    install(monkeypatch, mechanism("fake", 10, containment.DIMENSIONS))
    with pytest.raises(ValueError, match="needs an owner"):
        containment.acquire(spec_for(workspace), owner="   ")


def test_the_child_environment_cannot_be_edited_after_acquire(monkeypatch, workspace):
    """env is part of the boundary, so the caller's dict is copied and frozen."""
    enforcing(monkeypatch)
    install(monkeypatch, mechanism("fake", 10, containment.DIMENSIONS))
    caller_env = {"PATH": "/usr/bin"}
    grant = containment.acquire(
        spec_for(workspace, env=caller_env), owner="session-1",
    )
    caller_env["LD_PRELOAD"] = "/tmp/evil.so"
    assert dict(grant.spec.env) == {"PATH": "/usr/bin"}
    with pytest.raises(TypeError):
        grant.spec.env["LD_PRELOAD"] = "/tmp/evil.so"


# ── Durable records: one owner, one record ─────────────────────────────────
def test_a_grant_is_recorded_with_its_owner_and_declared_limits(
    monkeypatch, workspace, _isolated_store,
):
    enforcing(monkeypatch)
    install(monkeypatch, mechanism("fake", 10, containment.DIMENSIONS))
    spec = containment.agent_spec(workspace, {}, 7, max_processes=8)
    grant = containment.acquire(spec, owner="session-42")

    active = containment.active_grants()
    assert [record["id"] for record in active] == [grant.id]
    record = active[0]
    assert record["owner"] == "session-42"
    assert record["wall_clock_s"] == 7
    assert record["max_processes"] == 8
    assert record["required"] == sorted(containment.DEFAULT_REQUIRED)
    assert record["pid"] is None


def test_releasing_a_grant_with_no_process_clears_it_from_active(monkeypatch, workspace):
    enforcing(monkeypatch)
    install(monkeypatch, mechanism("fake", 10, containment.DIMENSIONS))
    grant = containment.acquire(containment.agent_spec(workspace, {}, 5), owner="s")
    outcome = containment.release(grant)
    assert outcome.dead is True
    assert outcome.escalated is False
    assert outcome.survivors == ()
    assert containment.active_grants() == []


def test_forget_drops_a_record(monkeypatch, workspace):
    enforcing(monkeypatch)
    install(monkeypatch, mechanism("fake", 10, containment.DIMENSIONS))
    grant = containment.acquire(containment.agent_spec(workspace, {}, 5), owner="s")
    containment.forget(grant.id)
    assert containment.active_grants() == []


def test_an_unwritable_store_does_not_take_out_execution(monkeypatch, workspace, caplog):
    """The record is observability, not a containment dimension.

    Refusing an authorized command because a journal file could not be written
    would be a worse failure than running it, so this degrades loudly and the
    grant still describes the boundary accurately.
    """
    enforcing(monkeypatch)
    install(monkeypatch, mechanism("fake", 10, containment.DIMENSIONS))

    def _explode(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(containment, "atomic_write_json", _explode)
    with caplog.at_level("WARNING", logger="src.containment"):
        grant = containment.acquire(containment.agent_spec(workspace, {}, 5), owner="s")
    assert grant.contained is True
    assert any("could not persist" in record.getMessage() for record in caplog.records)


def test_a_corrupt_store_does_not_take_out_execution(monkeypatch, workspace, _isolated_store):
    enforcing(monkeypatch)
    install(monkeypatch, mechanism("fake", 10, containment.DIMENSIONS))
    _isolated_store.write_text("{ this is not json", encoding="utf-8")
    grant = containment.acquire(containment.agent_spec(workspace, {}, 5), owner="s")
    assert [record["id"] for record in containment.active_grants()] == [grant.id]


# ── Execution that leaves the box is declared, not pretended ───────────────
async def test_an_external_bridge_grant_claims_nothing_and_cannot_be_run_locally(
    monkeypatch, workspace, no_spawn,
):
    enforcing(monkeypatch)
    spec = containment.agent_spec(workspace, {}, 5)
    grant = containment.declare_external_bridge(
        spec, owner="session-1", endpoint="http://127.0.0.1:8777/exec",
    )
    assert grant.mechanism == "external_bridge"
    assert grant.enforced == frozenset()
    assert grant.external is True
    assert grant.contained is False
    assert grant.to_dict()["external"] is True
    with pytest.raises(ValueError, match="does not own"):
        await containment.run(grant, "echo hello")
    assert no_spawn == []


@pytest.mark.parametrize("protected_dest", [
    "/etc",
    "/etc/ssl",
    "/usr",
    "/usr/local",
    "/bin",
    "/bin/sh",
    "/sbin",
    "/lib",
    "/lib64",
    "/proc",
    "/proc/sys",
    "/dev",
    "/dev/shm",
    "/sys",
    "/root",
    "/root/.ssh",
    "/home",
    "/workspace",
    "/workspace/sub",
])
def test_writable_extra_rejects_protected_system_roots_and_descendants(monkeypatch, workspace, protected_dest):
    install(monkeypatch, mechanism("fake", 10, containment.DIMENSIONS))
    spec = spec_for(workspace, writable_extra=(protected_dest,))
    with pytest.raises(ValueError, match="reserved path"):
        containment.acquire(spec, owner="session-1")


def test_writable_extra_accepts_legitimate_scratch_destinations(monkeypatch, workspace):
    install(monkeypatch, mechanism("fake", 10, containment.DIMENSIONS))
    spec = spec_for(workspace, writable_extra=("/var/scratch", "/tmp/custom_scratch", "/home/testuser/scratch"))
    grant = containment.acquire(spec, owner="session-1")
    assert "/var/scratch" in grant.spec.writable_extra
    assert "/tmp/custom_scratch" in grant.spec.writable_extra
    assert "/home/testuser/scratch" in grant.spec.writable_extra
