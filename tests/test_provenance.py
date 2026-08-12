from src.prompt_security import untrusted_context_message
from src.provenance import (
    ContextSensitivity,
    ConversationProvenance,
    ProvenanceOrigin,
    provenance_from_messages,
)
from src.tool_capabilities import ToolRunSecurityContext


def test_message_provenance_keeps_origin_and_sensitivity_separate():
    messages = [
        untrusted_context_message(
            "workspace file",
            "contents",
            origin=ProvenanceOrigin.WORKSPACE,
            sensitivity=ContextSensitivity.WORKSPACE,
        ),
        untrusted_context_message(
            "saved memory",
            "private fact",
            origin=ProvenanceOrigin.ODYSSEUS,
            sensitivity=ContextSensitivity.PRIVATE,
        ),
    ]

    state = provenance_from_messages(messages)

    assert state.workspace_untrusted_context_seen is True
    assert state.odysseus_untrusted_context_seen is True
    assert state.private_data_context_seen is True
    assert state.external_untrusted_context_seen is False


def test_provenance_merge_is_monotonic():
    state = ConversationProvenance(workspace_untrusted_context_seen=True)
    state.merge(ConversationProvenance(private_data_context_seen=True))
    state.merge(ConversationProvenance())

    assert state.labels() == ("workspace_untrusted", "private_data")


def test_workspace_and_private_tool_results_update_independent_bits():
    context = ToolRunSecurityContext()
    context.observe_tool_result(
        "read_file",
        {"output": "workspace text", "exit_code": 0},
        "README.md",
    )
    context.observe_tool_result(
        "manage_memory",
        {"results": ["private"], "exit_code": 0},
        "list",
    )

    assert context.workspace_untrusted_context_seen is True
    assert context.odysseus_untrusted_context_seen is True
    assert context.private_data_context_seen is True
    assert context.external_untrusted_context_seen is False


def test_approval_placeholder_does_not_claim_that_private_data_was_seen():
    context = ToolRunSecurityContext()

    context.observe_tool_result(
        "manage_memory",
        {
            "output": "Waiting for an exact user approval.",
            "approval_required": True,
            "exit_code": None,
        },
        "list",
    )

    assert context.to_provenance().labels() == ()


def test_agent_provenance_migration_adds_fail_closed_columns(
    monkeypatch,
    tmp_path,
):
    import sqlite3
    from sqlalchemy import create_engine
    import core.database as database

    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
    migration_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", migration_engine)
    try:
        database._migrate_add_agent_provenance_columns()
    finally:
        migration_engine.dispose()

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]: row for row in conn.execute(
                "PRAGMA table_info(sessions)"
            ).fetchall()
        }
    for name in (
        "agent_external_untrusted_seen",
        "agent_workspace_untrusted_seen",
        "agent_odysseus_untrusted_seen",
        "agent_private_data_seen",
    ):
        assert name in columns
        assert columns[name][3] == 1
        assert str(columns[name][4]).casefold() in {"0", "'0'", "false"}
