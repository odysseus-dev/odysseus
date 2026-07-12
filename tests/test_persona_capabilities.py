from pathlib import Path

from src.persona_capabilities import capability_summary, consult_edges, routing_intents


PERSONAS = """\
personas:
  aoteru:
    role: head-human-interfacer
    skills: [routing, triage]
    owns: [coordination]
    consults: [kurisu]
    escalates_to: [user]
    routing:
      intents: [dispatch, clarify]
  sanji:
    role: chef
    skills: [cooking, shopping]
    owns: [food]
    consults: [misato]
    escalates_to: [aoteru]
    routing:
      intents: [meal-planning, pantry]
"""


def _root(tmp_path: Path, monkeypatch, content: str = PERSONAS) -> Path:
    root = tmp_path / "flat-knowledgebase"
    path = root / "config" / "personas.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr("src.persona_capabilities._resolve_seed_root", lambda: root)
    return root


def test_capability_summary_returns_none_when_file_missing(tmp_path, monkeypatch):
    root = tmp_path / "flat-knowledgebase"
    root.mkdir()
    monkeypatch.setattr("src.persona_capabilities._resolve_seed_root", lambda: root)

    assert capability_summary("sanji") is None


def test_capability_summary_returns_none_for_malformed_yaml(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch, "personas: [unterminated")

    assert capability_summary("sanji") is None


def test_panel_persona_summary_includes_capabilities_and_interface(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)

    summary = capability_summary("sanji")

    assert summary is not None
    assert "Role: chef" in summary
    assert "Skills: cooking, shopping" in summary
    assert "Stewarded domains: food" in summary
    assert "Consults: misato" in summary
    assert "Escalates to: aoteru" in summary
    assert "Routing intents: meal-planning, pantry" in summary
    assert "Interface panel: food/PANTRY" in summary
    assert len(summary.splitlines()) <= 13


def test_aoteru_summary_includes_routing_and_one_passive_memory_line(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)

    summary = capability_summary("aoteru")

    assert summary is not None
    assert "Head-interfacer routing:" in summary
    assert "keeps a local passive memory" in summary
    assert "capture, open-loops, and glance" in summary
    assert "never writes to the household" in summary
    assert sum("Passive memory:" in line for line in summary.splitlines()) == 1
    assert len(summary.splitlines()) <= 13


def test_consult_edges_and_routing_intents_use_cached_persona_data(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)

    assert consult_edges("aoteru") == ["kurisu"]
    assert routing_intents("sanji") == ["meal-planning", "pantry"]


def test_consult_edges_returns_none_when_missing_or_malformed(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch, "personas:\n  aoteru:\n    role: head\n")
    assert consult_edges("aoteru") is None

    (root / "config" / "personas.yaml").write_text(
        "personas:\n  aoteru:\n    role: head\n    consults: {bad: value}\n",
        encoding="utf-8",
    )
    assert consult_edges("aoteru") is None
