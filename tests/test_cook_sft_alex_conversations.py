import importlib.util
from pathlib import Path


PATH = Path(__file__).parents[1] / "scripts" / "cook_sft_alex_conversations.py"
SPEC = importlib.util.spec_from_file_location("cook_sft", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_redact_removes_credentials_but_keeps_request():
    text = MODULE.redact("use hf_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456 and api_key=secret on 10.0.0.2")
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in text
    assert "secret" not in text
    assert "10.0.0.2" not in text
    assert "use" in text


def test_validate_flows_requires_one_known_seed_and_valid_family():
    result = {"flows": [{
        "source_seed_id": "s:1", "family": "notes",
        "turns": [{"user": "Show notes"}, {"user": "Open the first one"}],
    }, {
        "source_seed_id": "other", "family": "notes",
        "turns": [{"user": "x"}, {"user": "y"}],
    }]}
    valid = MODULE.validate_flows(result, {"s:1"})
    assert list(valid) == ["s:1"]
    assert valid["s:1"]["id"].startswith("sft-alex-")


def test_grounding_rejects_replacing_a_source_fixture_title():
    title = "audit document 20260828_190808-document"
    seed = {
        "seed_id": "s:2",
        "context": [
            {"user": f"Create a document titled {title} with one sentence."},
            {"user": f"Open {title} in the editor."},
        ],
        "target_user": f"Open {title} in the editor.",
    }
    flow = {
        "source_seed_id": "s:2", "family": "documents",
        "turns": [{"user": "open sprint-notes"}, {"user": "is this the one?"}],
    }
    assert MODULE.validate_flows({"flows": [flow]}, {"s:2"}, {"s:2": seed}) == {}


def test_grounding_requires_source_creation_before_opening_private_object():
    title = "audit document 20260828_190808-document"
    seed = {
        "seed_id": "s:3",
        "context": [
            {"user": f"Create a document titled {title} with one sentence."},
            {"user": f"Open {title} in the editor."},
        ],
        "target_user": f"Open {title} in the editor.",
    }
    flow = {
        "source_seed_id": "s:3", "family": "documents",
        "turns": [{"user": f"open {title}"}, {"user": "read the first line"}],
    }
    assert any(
        issue.startswith("unestablished_private_entity:")
        for issue in MODULE.grounding_issues(seed, flow)
    )


def test_grounding_accepts_preserved_creation_then_open_sequence():
    title = "audit document 20260828_190808-document"
    seed = {
        "seed_id": "s:4",
        "context": [
            {"user": f"Create a document titled {title} with one sentence."},
            {"user": f"Open {title} in the editor."},
        ],
        "target_user": f"Open {title} in the editor.",
    }
    flow = {
        "source_seed_id": "s:4", "family": "documents",
        "turns": [
            {"user": f"make a document called {title} with one sentence"},
            {"user": f"now open {title} in the editor"},
        ],
    }
    assert MODULE.grounding_issues(seed, flow) == []


def test_grounding_rejects_lookup_of_new_research_topic_before_start():
    seed = {
        "seed_id": "s:5",
        "context": [
            {"user": "List my saved research reports and find the newest SearXNG report."},
            {"user": "Start a concise new research report about SearXNG privacy defaults and return its task id."},
        ],
        "target_user": "Start a concise new research report about SearXNG privacy defaults and return its task id.",
    }
    flow = {
        "source_seed_id": "s:5", "family": "research",
        "turns": [
            {"user": "do I already have anything saved on SearXNG privacy defaults?"},
            {"user": "start a short new report on SearXNG privacy defaults"},
        ],
    }
    assert "lookup_before_creation:searxng privacy defaults" in MODULE.grounding_issues(seed, flow)
