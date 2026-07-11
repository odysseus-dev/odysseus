from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.misumi_routes import setup_misumi_routes
from services.memory.skills import SkillsManager
from src.misumi_household import HouseholdReadOnlyAdapter
from src.misumi_memory import MisumiMemory, route
from src.misumi_pilots import run_pilot


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    household = tmp_path / "household"
    (household / "household").mkdir(parents=True)
    monkeypatch.setenv("MISUMI_HOUSEHOLD_ROOT", str(household))
    memory_root = tmp_path / "data" / "misumi" / "memory"
    app = FastAPI()
    app.include_router(
        setup_misumi_routes(
            SkillsManager(str(tmp_path / "skills-data")), memory_root=memory_root
        )
    )
    return TestClient(app), memory_root


def test_capture_without_model_preserves_raw_text_verbatim(tmp_path):
    memory = MisumiMemory(tmp_path / "memory")
    raw = "  remember I wired the MPU6050 SDA to D1\nexactly like this  "

    capsule = memory.capture(raw)

    assert capsule["raw_text"] == raw
    assert capsule["summary"] != raw
    assert capsule["confidence"] <= 0.6
    assert capsule["meta"]["human_confirmation_suggested"] is True
    assert set(capsule) == {
        "id", "created", "updated", "raw_text", "summary", "type", "confidence",
        "source", "persona_primary", "persona_secondary", "entities", "next_action",
        "status", "human_confirmed", "meta",
    }


def test_model_refiner_personas_are_stored_normalised(tmp_path):
    memory = MisumiMemory(
        tmp_path / "memory",
        model_refiner=lambda _record: {
            "persona_primary": " sanji ",
            "persona_secondary": " KURISU ",
        },
    )

    capsule = memory.capture("A recipe note")

    assert capsule["persona_primary"] == "sanji"
    assert capsule["persona_secondary"] == "kurisu"


def test_reroute_updates_open_loop_owner_and_glance(tmp_path):
    memory = MisumiMemory(tmp_path / "memory")
    capsule = memory.capture("remember I wired the MPU6050 SDA to D1")
    original_loop = memory.loops()[0][0]

    memory.reroute(capsule["id"], "sanji")

    updated_loop = memory.loops()[0][0]
    assert updated_loop["owner"] == "sanji"
    assert updated_loop["updated"] > original_loop["updated"]
    assert memory.glance()["responsible_persona"] == "sanji"
    assert len((memory.root / "open_loops.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_malformed_stale_hours_falls_back_without_breaking_listing(tmp_path, monkeypatch):
    monkeypatch.setenv("MISUMI_MEMORY_STALE_HOURS", "not-a-number")
    memory = MisumiMemory(tmp_path / "memory")
    memory.capture("the interface box is offline")

    loops, corrupt = memory.loops()

    assert len(loops) == 1
    assert loops[0]["stale"] is False
    assert corrupt == 0


@pytest.mark.parametrize(
    ("text", "capsule_type", "owner"),
    [
        ("remember I wired the MPU6050 SDA to D1", "note", "ichigo"),
        ("all wired up, now for implementation", "open_loop", "lelouch"),
        ("this recipe worked but needed more acid", "experiment_result", "sanji"),
        ("the interface box is offline", "blocker", "ichigo"),
        ("I bought 5 MPU6050 GY-521s", "inventory", "ichigo"),
    ],
)
def test_practical_capture_evals_create_owned_open_loops(tmp_path, text, capsule_type, owner):
    memory = MisumiMemory(tmp_path / "memory")

    capsule = memory.capture(text)
    loops, corrupt = memory.loops()

    assert capsule["type"] == capsule_type
    assert capsule["persona_primary"] == owner
    assert len(loops) == 1
    assert loops[0]["capsule_id"] == capsule["id"]
    assert corrupt == 0


def test_routing_is_bounded_and_unknown_personas_are_rejected(tmp_path, monkeypatch):
    primary, secondary = route("deploy sensor hardware experiment before deadline")
    assert primary in {"lelouch", "ichigo", "ginko", "giorno", "erwin"}
    assert secondary is None or secondary != primary

    client, _ = _client(tmp_path, monkeypatch)
    assert client.post("/misumi/memory/capture", json={"text": "note", "persona": "unknown"}).status_code == 422
    capsule = client.post("/misumi/memory/capture", json={"text": "plain note"}).json()
    assert client.post(
        f"/misumi/memory/{capsule['id']}/route",
        json={"persona_primary": "unknown"},
    ).status_code == 422
    assert client.post(
        "/misumi/handoff",
        json={"from_persona": "unknown", "to_persona": "kurisu", "action": "review locally"},
    ).status_code == 422


def test_handoff_rejects_forbidden_outbound_action(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    response = client.post(
        "/misumi/handoff",
        json={"from_persona": "kurisu", "to_persona": "ichigo", "action": "email the landlord"},
    )
    assert response.status_code == 422


def test_status_and_glance_work_empty_and_with_data_without_model(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    empty_status = client.get("/misumi/status").json()
    empty_glance = client.get("/misumi/glance").json()
    assert empty_status["memory"]["capsules"] == 0
    assert empty_status["memory"]["writes_allowed"] is False
    assert empty_glance["writes_allowed"] is False

    client.post("/misumi/memory/capture", json={"text": "still need to fix the wiring"})
    populated_status = client.get("/misumi/status").json()
    populated_glance = client.get("/misumi/glance").json()
    assert populated_status["memory"]["capsules"] == 1
    assert populated_status["memory"]["open_loops"] == 1
    assert populated_glance["next_recommended_action"]
    assert populated_glance["writes_allowed"] is False


def test_corrupt_jsonl_is_skipped_and_counted(tmp_path, monkeypatch):
    client, memory_root = _client(tmp_path, monkeypatch)
    client.post("/misumi/memory/capture", json={"text": "a valid capture"})
    with (memory_root / "capsules.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")

    recent = client.get("/misumi/memory/recent").json()
    glance = client.get("/misumi/glance").json()
    assert len(recent["capsules"]) == 1
    assert recent["corrupt_lines"] == 1
    assert glance["corrupt_lines"] == 1


def test_memory_digest_only_writes_under_data_root_and_preserves_household(tmp_path):
    household = tmp_path / "household-repo"
    (household / "household").mkdir(parents=True)
    source = household / "household" / "facts.md"
    source.write_text("canonical fact\n", encoding="utf-8")
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    memory_root = tmp_path / "runtime-data" / "misumi" / "memory"
    MisumiMemory(memory_root).capture("still need to verify the sensor")

    result = run_pilot(
        "memory-digest", adapter=HouseholdReadOnlyAdapter(household),
        persist=False, memory_root=memory_root,
    )

    output = Path(result["result"]["output"])
    assert result["household_unchanged"] is True
    assert output.is_file()
    assert memory_root in output.parents
    assert household not in output.parents
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before


def test_state_transitions_append_and_fold_latest_records(tmp_path, monkeypatch):
    client, memory_root = _client(tmp_path, monkeypatch)
    capsule = client.post("/misumi/memory/capture", json={"text": "remember this wiring"}).json()

    confirmed = client.post(f"/misumi/memory/{capsule['id']}/confirm").json()
    routed = client.post(
        f"/misumi/memory/{capsule['id']}/route",
        json={"persona_primary": "ichigo", "persona_secondary": "kurisu"},
    ).json()
    closed = client.post(
        f"/misumi/memory/{capsule['id']}/close", json={"resolution": "verified"}
    ).json()

    assert confirmed["human_confirmed"] is True and confirmed["status"] == "confirmed"
    assert routed["status"] == "routed" and routed["persona_primary"] == "ichigo"
    assert closed["status"] == "closed" and closed["meta"]["resolution"] == "verified"
    recent = client.get("/misumi/memory/recent").json()["capsules"]
    assert len(recent) == 1 and recent[0]["status"] == "closed"
    assert client.get("/misumi/memory/open-loops").json()["open_loops"] == []
    lines = (memory_root / "capsules.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4


def test_handoff_resolves_by_appending_latest_record(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    handoff = client.post(
        "/misumi/handoff",
        json={"from_persona": "kurisu", "to_persona": "ichigo", "action": "review the local wiring note"},
    ).json()
    resolved = client.post(f"/misumi/handoffs/{handoff['id']}/resolve").json()
    assert resolved["status"] == "resolved"
    assert client.get("/misumi/handoffs?status=pending").json()["handoffs"] == []
    assert len(client.get("/misumi/handoffs?status=resolved").json()["handoffs"]) == 1
