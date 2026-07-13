from pathlib import Path


def _research_anchor_branch() -> str:
    source = Path("src/agent_loop.py").read_text(encoding="utf-8")
    start = source.index('result.get("research_session_id")')
    end = source.index("# Same pattern for notes:", start)
    return source[start:end]


def test_research_anchor_is_added_to_persisted_assistant_response():
    """The link shown in SSE must survive session reload via full_response."""
    branch = _research_anchor_branch()

    assert 'json.dumps({"delta": _anchor})' in branch
    assert "full_response" in branch
    assert "_anchor" in branch

    persist_position = branch.index("full_response")
    stream_position = branch.index('json.dumps({"delta": _anchor})')
    assert persist_position < stream_position


def test_research_anchor_is_not_persisted_twice():
    branch = _research_anchor_branch()

    persistence_statements = [
        line
        for line in branch.splitlines()
        if "full_response" in line and "_anchor" in line
    ]
    assert len(persistence_statements) == 1
