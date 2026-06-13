# tests/test_subagent_orchestrator.py
"""Multiagent slice-1 Task 4: orchestrator bounds, dispatch, join. Spec §Testing."""
import asyncio

import pytest

from src import subagent_orchestrator as orch


@pytest.fixture()
def profiles_root(tmp_path):
    from services.business_platform.profile_compiler import (
        GENERAL_OFFICE_CATALOG_PATH, SEED_SKILLS_DIR, compile_profile,
        load_catalog,
    )
    cat = load_catalog(GENERAL_OFFICE_CATALOG_PATH, skills_dir=SEED_SKILLS_DIR)
    compile_profile(cat, tmp_path)
    return tmp_path


def _runner(record=None, fail_names=(), delay=0.0):
    record = record if record is not None else []

    async def run_loop(*, binding, messages, endpoint_url, model, headers,
                       tools, disable_spawn, session_id):
        if delay:
            await asyncio.sleep(delay)
        record.append({"name": binding["name"], "tools": set(tools),
                       "disable_spawn": disable_spawn,
                       "messages": messages, "owner": binding["owner"]})
        if binding["name"] in fail_names:
            raise RuntimeError("boom")
        return f"done:{binding['name']}"

    return run_loop, record


def _spawn(args, root, runner, **kw):
    return asyncio.run(orch.spawn(
        args, human_owner="oleg", endpoint_url="http://e", model="m",
        run_loop=runner, data_dir=root, **kw))


def test_sequential_dispatch_and_join(profiles_root):
    runner, rec = _runner()
    res = _spawn({"mode": "sequential", "agents": [
        {"agent": "general_office-seo", "task": "audit"},
        {"agent": "general_office-content", "task": "write"},
    ]}, profiles_root, runner)
    assert res["status"] == "ok" and res["mode"] == "sequential"
    assert [r["name"] for r in res["results"]] == [
        "general_office-seo", "general_office-content"]
    assert all(r["trusted"] is False for r in res["results"])
    assert res["results"][0]["output"] == "done:general_office-seo"


def test_parallel_fanout_join_and_failure_isolation(profiles_root):
    runner, rec = _runner(fail_names={"general_office-content"})
    res = _spawn({"mode": "parallel", "agents": [
        {"agent": "general_office-seo", "task": "audit"},
        {"agent": "general_office-content", "task": "write"},
    ]}, profiles_root, runner)
    assert res["mode"] == "parallel"
    by_name = {r["name"]: r for r in res["results"]}
    assert by_name["general_office-seo"]["status"] == "ok"
    assert by_name["general_office-content"]["status"] == "error"
    assert res["completed"] == 1 and res["failed"] == 1


def test_depth_cap_refuses_spawn(profiles_root):
    runner, _ = _runner()
    res = _spawn({"agents": [{"agent": "general_office-seo", "task": "x"}]},
                 profiles_root, runner, depth=2)
    assert res["status"] == "refused" and "depth" in res["error"]


def test_nested_loop_gets_spawn_disabled_at_last_level(profiles_root):
    runner, rec = _runner()
    _spawn({"agents": [{"agent": "general_office-seo", "task": "x"}]},
           profiles_root, runner, depth=1)   # children would be depth 2 = cap
    assert rec[0]["disable_spawn"] is True


def test_tool_scoping_intersection(profiles_root):
    runner, rec = _runner()
    _spawn({"agents": [{"agent": "general_office-seo", "task": "x"}]},
           profiles_root, runner, coordinator_tools={"web", "memory"})
    # binding tools [web, documents, memory] ∩ coordinator {web, memory}
    assert rec[0]["tools"] == {"web", "memory"}


def test_owner_derivation_and_soul_injection_untrusted(profiles_root):
    runner, rec = _runner()
    _spawn({"agents": [{"agent": "general_office-seo", "task": "audit"}]},
           profiles_root, runner)
    assert rec[0]["owner"] == "agent:oleg/general_office-seo"
    msgs = rec[0]["messages"]
    assert msgs[0]["metadata"]["trusted"] is False
    assert "SEO specialist" in msgs[0]["content"]
    assert msgs[-1]["content"] == "audit"


def test_parallel_cap_bounds_concurrency(profiles_root, monkeypatch):
    monkeypatch.setattr(orch, "_max_parallel", lambda: 2)
    active, peak = [0], [0]

    async def run_loop(*, binding, **kw):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await asyncio.sleep(0.02)
        active[0] -= 1
        return "ok"

    _spawn({"mode": "parallel", "agents": [
        {"agent": "general_office-seo", "task": "a"},
        {"agent": "general_office-content", "task": "b"},
        {"agent": "general_office-sales", "task": "c"},
        {"agent": "general_office-support", "task": "d"},
    ]}, profiles_root, run_loop)
    assert peak[0] <= 2


def test_malformed_args_error_result_not_crash(profiles_root):
    runner, _ = _runner()
    assert _spawn({}, profiles_root, runner)["status"] == "error"
    assert _spawn({"agents": []}, profiles_root, runner)["status"] == "error"
    assert _spawn({"agents": [{"task": "no binding"}]},
                  profiles_root, runner)["status"] == "error"
    res = _spawn({"mode": "warp", "agents": [
        {"agent": "general_office-seo", "task": "x"}]}, profiles_root, runner)
    assert res["status"] == "error" and "warp" in res["error"]


def test_partial_resolution_still_runs_valid_entries(profiles_root):
    runner, _ = _runner()
    res = _spawn({"mode": "sequential", "agents": [
        {"agent": "general_office-seo", "task": "x"},
        {"agent": "ghost-agent", "task": "y"},
    ]}, profiles_root, runner)
    statuses = {r["name"]: r["status"] for r in res["results"]}
    assert statuses["general_office-seo"] == "ok"
    assert any(s == "error" for n, s in statuses.items() if n != "general_office-seo")
