from pathlib import Path

from src.chat_processor import ChatProcessor
from src.seed_order_context import build_seed_order_context


LOAD_FILES = (
    "docs/core/misumi-seed-order-v0.1.md",
    "docs/core/agent-personality-registry-v0.1.md",
    "protocols/register.md",
    "templates/change-log-entry.md",
    "agents/core/emperor-aoteru-misumi.md",
    "agents/core/operator-lelouch-lamperouge.md",
    "agents/core/archivist-makise-kurisu.md",
    "docs/repository-boundaries.md",
    "docs/odysseus-contract.md",
)


def _seed_root(tmp_path: Path) -> Path:
    root = tmp_path / "misumi"
    for rel in LOAD_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{rel} body", encoding="utf-8")
    return root


def test_seed_order_loader_degrades_when_absent(tmp_path):
    assert build_seed_order_context(tmp_path / "missing") is None


def test_seed_order_loader_reads_canonical_files_in_order(tmp_path):
    root = _seed_root(tmp_path)

    context = build_seed_order_context(root)

    assert context is not None
    positions = [context.index(f"### {rel}") for rel in LOAD_FILES]
    assert positions == sorted(positions)
    assert "Loaded read-only from the configured canonical Misumi repository." in context
    assert "Observe -> Propose -> Review -> Ratify -> Implement -> Log" in context
    assert "Specialist personas remain dormant" in context
    assert "Level 5 and Level 6 changes must remain proposed" in context


def test_seed_order_loader_accepts_environment_root(tmp_path, monkeypatch):
    root = _seed_root(tmp_path)
    monkeypatch.setenv("MISUMI_SOURCE_ROOT", str(root))
    for key in ("MISUMI_SEED_ORDER_ROOT", "MISUMI_CANONICAL_ROOT", "FLAT_KNOWLEDGEBASE_ROOT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("src.seed_order_context.get_setting", lambda *args, **kwargs: "")

    context = build_seed_order_context()

    assert context is not None
    assert context.startswith("## Misumi Seed Order Runtime Context")


def test_chat_preface_injects_seed_order_before_preset(monkeypatch):
    monkeypatch.setattr(
        "src.chat_processor.build_seed_order_context",
        lambda: "SEED ORDER CONTEXT",
    )
    processor = ChatProcessor(memory_manager=_Memory(), personal_docs_manager=_Docs())

    preface, _, _ = processor.build_context_preface(
        message="hello",
        session=None,
        use_memory=False,
        use_rag=False,
        preset_system_prompt="PERSONA PROMPT",
    )

    system_contents = [m["content"] for m in preface if m["role"] == "system"]
    assert system_contents[0] == "SEED ORDER CONTEXT"
    assert system_contents[1] == "PERSONA PROMPT"


def test_agent_prompt_injects_seed_order_before_tool_prompt(monkeypatch):
    import src.agent_loop as agent_loop

    monkeypatch.setattr(agent_loop, "_build_base_prompt", lambda *args, **kwargs: ("TOOL PROMPT", ""))
    monkeypatch.setattr(agent_loop, "build_seed_order_context", lambda: "SEED ORDER CONTEXT")
    monkeypatch.setattr(agent_loop, "set_active_model", lambda model: None)
    monkeypatch.setattr(agent_loop, "get_builtin_overrides", lambda: {})
    monkeypatch.setattr(agent_loop, "_cached_base_prompt", None)
    monkeypatch.setattr(agent_loop, "_cached_base_prompt_key", None)

    messages, _ = agent_loop._build_system_prompt(
        [
            {"role": "system", "content": "PERSONA PROMPT"},
            {"role": "user", "content": "hello"},
        ],
        model="test-model",
        active_document=None,
        mcp_mgr=None,
    )

    system = next(m for m in messages if m["role"] == "system")
    assert system["content"].startswith("SEED ORDER CONTEXT\n\nPERSONA PROMPT\n\nTOOL PROMPT")


class _Memory:
    def load(self, owner=None):
        return []


class _Docs:
    rag_manager = None
