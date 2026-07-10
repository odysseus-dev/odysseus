from pathlib import Path

from src.misumi_household import HouseholdReadOnlyAdapter
from src.misumi_pilots import load_pilot_config, run_pilot


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "household" / "food").mkdir(parents=True)
    (root / "household" / "food" / "shopping-list.md").write_text("- [ ] rice\n", encoding="utf-8")
    (root / "agent-tasks" / "inbox").mkdir(parents=True)
    (root / "agent-tasks" / "inbox" / "runtime.md").write_text(
        "---\ntitle: Runtime health\nstatus: open\npriority: high\n---\nDeploy Odysseus.\n", encoding="utf-8"
    )
    return root


def test_versioned_pilots_are_disabled_by_default():
    config = load_pilot_config()
    assert config["enabled"] is False
    assert all(pilot["enabled"] is False for pilot in config["pilots"].values())


def test_each_pilot_preserves_household_content(tmp_path):
    adapter = HouseholdReadOnlyAdapter(_repo(tmp_path))
    for name in ("morning-status", "skill-audit", "task-triage", "household-qa"):
        result = run_pilot(name, adapter=adapter, question="shopping rice")
        assert result["household_unchanged"] is True
        assert result["writes_allowed"] is False
        assert result["external_sends_allowed"] is False


def test_pilot_output_is_written_only_to_external_data_root(tmp_path):
    repo = _repo(tmp_path)
    output = tmp_path / "runtime-data"
    result = run_pilot("morning-status", adapter=HouseholdReadOnlyAdapter(repo), persist=True, output_root=output)
    assert Path(result["output"]).is_file()
    assert repo not in Path(result["output"]).parents
