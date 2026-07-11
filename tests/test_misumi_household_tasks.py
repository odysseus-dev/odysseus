from pathlib import Path

import pytest

from src.misumi_household import HouseholdReadOnlyAdapter, infer_household_domain
from src.misumi_task_router import MisumiTaskRouter


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "flat-knowledgebase"
    (root / "household" / "food").mkdir(parents=True)
    (root / "household" / "food" / "shopping-list.md").write_text(
        "# Shopping\n- [ ] miso\n- [ ] rice\n", encoding="utf-8"
    )
    (root / "agent-tasks" / "inbox").mkdir(parents=True)
    (root / "agent-tasks" / "inbox" / "deploy-odysseus-host.md").write_text(
        "---\ntitle: Deploy Odysseus host\npriority: high\nstatus: open\n---\nRuntime deployment and health.\n",
        encoding="utf-8",
    )
    (root / "agent-tasks" / "inbox" / "voice-tts.md").write_text(
        "---\ntitle: Voice TTS\npriority: high\nstatus: open\n---\nVoice work.\n", encoding="utf-8"
    )
    return root


def test_household_adapter_reads_and_cites_without_mutation(tmp_path):
    root = _repo(tmp_path)
    adapter = HouseholdReadOnlyAdapter(root)
    before = adapter.content_fingerprint()

    hits = adapter.search("shopping miso", domain="shopping")
    result = adapter.read("household/food/shopping-list.md")

    assert hits[0]["path"] == "household/food/shopping-list.md"
    assert result["line_start"] == 1
    assert "miso" in result["text"]
    assert adapter.content_fingerprint() == before


def test_household_adapter_does_not_rank_substring_matches(tmp_path):
    root = _repo(tmp_path)
    (root / "agent-tasks" / "inbox" / "unrelated.md").write_text(
        "Francesca keeps the runtime spinning online.\n", encoding="utf-8"
    )
    adapter = HouseholdReadOnlyAdapter(root)

    assert adapter.search("What is the capital of France? Answer in three words or fewer.") == []


def test_domain_inference_keeps_food_search_out_of_task_notes(tmp_path):
    root = _repo(tmp_path)
    (root / "household" / "food" / "stock.yaml").write_text("rice: present\n", encoding="utf-8")
    (root / "agent-tasks" / "inbox" / "food-note.md").write_text(
        "food stock recipe data exists\n", encoding="utf-8"
    )
    query = "What food stock and recipe data exists?"
    adapter = HouseholdReadOnlyAdapter(root)

    assert infer_household_domain(query) == "food"
    assert all(hit["path"].startswith("household/") for hit in adapter.search(query, domain="food"))


def test_maintenance_domain_excludes_completed_task_archive(tmp_path):
    root = _repo(tmp_path)
    (root / "agent-tasks" / "done").mkdir(parents=True)
    (root / "agent-tasks" / "done" / "old-loop.md").write_text("urgent open loop\n", encoding="utf-8")
    (root / "agent-tasks" / "inbox" / "live-loop.md").write_text("urgent open loop\n", encoding="utf-8")

    hits = HouseholdReadOnlyAdapter(root).search("urgent open loops", domain="maintenance")

    assert hits
    assert all(not hit["path"].startswith("agent-tasks/done/") for hit in hits)


def test_household_adapter_rejects_escape_and_unsupported_files(tmp_path):
    adapter = HouseholdReadOnlyAdapter(_repo(tmp_path))
    with pytest.raises(ValueError):
        adapter.read("../secret.md")
    with pytest.raises(ValueError):
        adapter.read("household/food/tool.exe")


def test_task_router_prioritises_runtime_over_voice(tmp_path):
    router = MisumiTaskRouter(HouseholdReadOnlyAdapter(_repo(tmp_path)))
    result = router.route("autonomously complete agentic routed tasks")

    assert result["status"] == "planned"
    assert result["selected_task"].endswith("deploy-odysseus-host.md")
    assert result["safe_to_execute_now"] is False
    assert result["files_changed"] == []
    assert result["policy"]["writes_allowed"] is False


def test_task_router_returns_structured_blocker_without_repo(tmp_path):
    router = MisumiTaskRouter(HouseholdReadOnlyAdapter(tmp_path / "missing"))
    result = router.route("do tasks", persona="jin")

    assert result["status"] == "blocked"
    assert result["blockers"]
    assert result["persona"] == "jin"
