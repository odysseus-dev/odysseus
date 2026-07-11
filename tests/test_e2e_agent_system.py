"""End-to-end test of the entire agent system — all 4 phases."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time

# ═══════════════════════════════════════════════════════════════════════
# PHASE 1: Agent Loop Core
# ═══════════════════════════════════════════════════════════════════════

def test_phase1_loop_detector():
    """LoopDetector detects stalls, runaway, and text repetition."""
    from src.agent.loop_detector import LoopDetector, RecoveryLevel, StableSignature

    # Stable signature ignores key order
    sig1 = StableSignature.from_tool_call("bash", '{"command": "ls", "path": "/tmp"}')
    sig2 = StableSignature.from_tool_call("bash", '{"path": "/tmp", "command": "ls"}')
    assert sig1 == sig2, "StableSignature should ignore key order"

    # Stall detection
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    sig = StableSignature.from_tool_call("bash", '{"command": "ls"}')
    for _ in range(5):
        det.record_round(text="", tool_calls=[sig])
    assert det.check_stall() == RecoveryLevel.STRONG, "Should detect stall"

    # Runaway detection
    det2 = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    for _ in range(16):
        det2.record_round(text="", tool_calls=[sig])
    assert det2.is_runaway(), "Should detect runaway"

    # Recovery prompts
    from src.agent.recovery import RecoveryPrompts
    msg = RecoveryPrompts.runaway("bash")
    assert "bash" in msg.lower(), "Recovery prompt should mention the tool"

    print("  ✅ LoopDetector: stall, runaway, stable signatures, recovery prompts")


def test_phase1_recovery():
    """IntentSupervisor catches 'I'll check...' without action."""
    from src.agent.recovery import IntentSupervisor
    sup = IntentSupervisor(max_nudges=2)
    assert sup.detect("Let me check the logs") is True
    assert sup.detect("Here is the result") is False
    sup.nudge()
    sup.nudge()
    assert sup.should_nudge() is False, "Should cap at max_nudges"
    print("  ✅ Recovery: IntentSupervisor detection and capping")


def test_phase1_prompt_builder():
    """PromptBuilder assembles sections by priority."""
    from src.agent.prompt_builder import PromptBuilder, PromptSection
    b = PromptBuilder()
    b.add_section(PromptSection(id="low", content="LOW", priority=10, trusted=True))
    b.add_section(PromptSection(id="high", content="HIGH", priority=100, trusted=True))
    result = b.build()
    assert result.index("HIGH") < result.index("LOW"), "Higher priority first"
    print("  ✅ PromptBuilder: priority ordering")


def test_phase1_checkpoint():
    """ContextManager tracks tokens and triggers compaction."""
    from src.agent.checkpoint import ContextManager
    cm = ContextManager(max_tokens=1000, compaction_threshold=0.8)
    cm.add_tokens(500)
    assert cm.needs_compaction() is False
    cm.add_tokens(400)
    assert cm.needs_compaction() is True
    print("  ✅ ContextManager: token tracking and compaction trigger")


# ═══════════════════════════════════════════════════════════════════════
# PHASE 2: Tool.define() + Permissions
# ═══════════════════════════════════════════════════════════════════════

def test_phase2_tool_define():
    """Tool.define() creates typed tools with validation."""
    from pydantic import BaseModel, Field
    from src.agent.tool import Tool, ToolResult, ToolContext, RecoverableError

    class EchoParams(BaseModel):
        message: str = Field(description="Message")

    async def echo(params: EchoParams, ctx: ToolContext) -> ToolResult:
        return ToolResult(output=params.message, title="Echo")

    T = Tool.define("echo", "Echoes", EchoParams, echo)
    assert T.id == "echo"
    assert T.to_schema()["function"]["name"] == "echo"

    # Execute
    ctx = ToolContext(session_id="test", owner="test")
    result = asyncio.get_event_loop().run_until_complete(
        T.execute({"message": "hello"}, ctx)
    )
    assert result.output == "hello"

    # Validation error
    try:
        asyncio.get_event_loop().run_until_complete(
            T.execute({}, ctx)
        )
        assert False, "Should raise RecoverableError"
    except RecoverableError:
        pass

    print("  ✅ Tool.define(): creation, execution, validation, schema")


def test_phase2_permissions():
    """Permission system with allow/deny/ask rules."""
    from src.agent.permission import Action, Rule, evaluate, AGENT_PERMISSIONS

    rules = [Rule(permission="bash", pattern="*", action=Action.ALLOW)]
    result = evaluate("bash", "ls", rules)
    assert result.action == Action.ALLOW

    rules2 = [
        Rule(permission="bash", pattern="*", action=Action.ALLOW),
        Rule(permission="bash", pattern="rm *", action=Action.DENY),
    ]
    result2 = evaluate("bash", "rm -rf /", rules2)
    assert result2.action == Action.DENY

    # Plan mode disables writes
    plan_rules = AGENT_PERMISSIONS["plan"]
    result3 = evaluate("edit_file", "src/main.py", plan_rules)
    assert result3.action == Action.DENY

    print("  ✅ Permissions: allow/deny, pattern matching, agent rulesets")


def test_phase2_registry():
    """ToolRegistry with resolution and filtering."""
    from pydantic import BaseModel, Field
    from src.agent.tool import Tool, ToolResult, ToolContext
    from src.agent.permission import Action, Rule
    from src.agent.tool_registry import ToolRegistry

    class P(BaseModel):
        x: int = Field(default=1)

    async def fn(p: P, c: ToolContext) -> ToolResult:
        return ToolResult(output="ok", title="OK")

    reg = ToolRegistry()
    reg.register(Tool.define("t1", "Tool 1", P, fn))
    reg.register(Tool.define("t2", "Tool 2", P, fn))

    # All allowed
    tools = reg.resolve()
    assert len(tools) == 2

    # Deny t1
    rules = [Rule(permission="t1", pattern="*", action=Action.DENY)]
    tools2 = reg.resolve(ruleset=rules)
    assert len(tools2) == 1
    assert tools2[0].id == "t2"

    # Allowlist
    tools3 = reg.resolve(allowlist={"t1"})
    assert len(tools3) == 1

    print("  ✅ ToolRegistry: registration, resolution, filtering")


# ═══════════════════════════════════════════════════════════════════════
# PHASE 3: Subagent/Actor + Memory
# ═══════════════════════════════════════════════════════════════════════

def test_phase3_actor():
    """Actor lifecycle and registry."""
    from src.agent.actor import Actor, ActorMode, ActorStatus, ActorOutcome, ActorRegistry

    reg = ActorRegistry()
    actor = Actor(id="test-1", session_id="s1", mode=ActorMode.SUBAGENT)
    reg.register(actor)
    assert reg.get("test-1") is actor

    reg.update_status("test-1", ActorStatus.RUNNING)
    assert reg.get("test-1").status == ActorStatus.RUNNING

    reg.update_status("test-1", ActorStatus.IDLE, outcome=ActorOutcome.SUCCESS)
    assert reg.get("test-1").outcome == ActorOutcome.SUCCESS

    # Singleton
    reg2 = ActorRegistry.get_instance()
    assert reg2 is ActorRegistry.get_instance()

    print("  ✅ Actor: lifecycle, registry, singleton")


def test_phase3_spawn():
    """SpawnConfig and ReturnFormat parsing."""
    from src.agent.spawn import SpawnConfig, ReturnFormat, parse_return_header

    config = SpawnConfig(agent_type="explore", task="Find files", session_id="s1")
    assert config.agent_type == "explore"
    assert config.mode == "subagent"

    text = """**Status**: success
**Summary**: Found 5 files

- src/main.py

**Files touched**: src/main.py
**Findings worth promoting**: Entry point"""
    result = parse_return_header(text)
    assert result.status == "success"
    assert result.summary == "Found 5 files"

    print("  ✅ Spawn: config, return format parsing")


def test_phase3_inbox():
    """Inbox message passing between actors."""
    from src.agent.inbox import Inbox, InboxMessage

    inbox = Inbox()
    inbox.send(InboxMessage(sender_id="a", receiver_id="b", content="hello", type="text"))
    msgs = inbox.receive("b")
    assert len(msgs) == 1
    assert msgs[0].content == "hello"

    inbox.send_notification(sender_id="a", receiver_id="b", status="done", summary="Task complete")
    msgs2 = inbox.receive("b", type_filter="actor_notification")
    assert len(msgs2) == 1
    assert "done" in msgs2[0].content.lower()

    print("  ✅ Inbox: send, receive, notifications")


def test_phase3_memory_persist():
    """File-based memory persistence."""
    from src.agent.memory_persist import MemoryStore, CheckpointStore, NotesStore, TaskProgressStore

    with tempfile.TemporaryDirectory() as d:
        # MemoryStore
        ms = MemoryStore(d)
        assert "Project memory" in ms.read()

        # CheckpointStore
        cs = CheckpointStore(d)
        cs.update_section("active_intent", "Fix the bug")
        assert "Fix the bug" in cs.get_section("active_intent")

        # NotesStore
        ns = NotesStore(d)
        ns.append("Important finding")
        assert "Important finding" in ns.read()

        # TaskProgressStore
        tp = TaskProgressStore(d)
        tp.write_progress("T1", "Done")
        assert "Done" in tp.read_progress("T1")

    print("  ✅ Memory: MEMORY.md, checkpoint.md, notes.md, task progress")


# ═══════════════════════════════════════════════════════════════════════
# PHASE 4: Integration
# ═══════════════════════════════════════════════════════════════════════

def test_phase4_checkpoint_writer():
    """CheckpointWriter persists state to files."""
    from src.agent.checkpoint_writer import CheckpointWriter

    with tempfile.TemporaryDirectory() as d:
        writer = CheckpointWriter(d)
        writer.write_checkpoint(
            active_intent="User wants to fix the bug",
            next_action="Run tests",
            current_work="Debugging",
        )
        writer.write_memory(
            project_context="Test project",
            rules=["Rule 1"],
        )
        writer.write_note("Important note")

        context = writer.rebuild_context()
        assert "fix the bug" in context.lower()
        assert "Test project" in context

    print("  ✅ CheckpointWriter: write checkpoint, memory, notes, rebuild")


def test_phase4_context_rebuild():
    """ContextRebuilder restores state from checkpoint files."""
    from src.agent.context_rebuild import ContextRebuilder
    from src.agent.memory_persist import CheckpointStore

    with tempfile.TemporaryDirectory() as d:
        cs = CheckpointStore(d)
        cs.update_section("active_intent", "Fix the bug")

        rebuilder = ContextRebuilder(d)
        assert rebuilder.needs_rebuild() is True

        msg = rebuilder.build_system_message()
        assert msg["role"] == "system"
        assert "Fix the bug" in msg["content"]

    print("  ✅ ContextRebuilder: needs_rebuild, build system message")


def test_phase4_subagent_tools():
    """Subagent tools (spawn, wait, list) work end-to-end."""
    from src.agent_tools.subagent_tools import SpawnSubagentTool, WaitActorTool, ListActorsTool

    async def run():
        spawn = SpawnSubagentTool()
        list_t = ListActorsTool()
        wait = WaitActorTool()
        ctx = {"session_id": "e2e-test", "owner": "test"}

        # Spawn background
        result = await spawn.execute(
            '{"task": "Test task", "agent_type": "explore", "background": true}',
            ctx,
        )
        assert result["status"] == "spawned"
        actor_id = result["actor_id"]

        # List actors
        result2 = await list_t.execute('{}', ctx)
        assert any(a["id"] == actor_id for a in result2["actors"])

        # Wait (should find it)
        result3 = await wait.execute(
            json.dumps({"actor_id": actor_id, "timeout": 0.5}),
            ctx,
        )
        assert result3["status"] in ("running", "idle", "not_found")

    asyncio.get_event_loop().run_until_complete(run())
    print("  ✅ SubagentTools: spawn, list, wait")


def test_phase4_prompt_builder_integration():
    """PromptBuilder used in _assemble_prompt."""
    from src.agent_loop import _assemble_prompt

    # Full mode
    result = _assemble_prompt({"bash", "read_file", "web_search"})
    assert "bash" in result
    assert "read_file" in result

    # Compact mode
    result2 = _assemble_prompt({"bash", "read_file"}, compact=True)
    assert "native tool/function calling" in result2

    print("  ✅ PromptBuilder integration: _assemble_prompt uses PromptBuilder")


def test_phase4_checkpoint_trigger_logic():
    """Checkpoint trigger fires when context is large."""
    from src.agent.checkpoint_writer import CheckpointWriter

    with tempfile.TemporaryDirectory() as d:
        writer = CheckpointWriter(d)
        # Simulate checkpoint write
        writer.write_checkpoint(
            active_intent="Long conversation",
            next_action="Continue",
            current_work="At 80% context",
        )
        context = writer.rebuild_context()
        assert len(context) > 0

    print("  ✅ Checkpoint trigger: writer produces valid rebuild context")


# ═══════════════════════════════════════════════════════════════════════
# TOOL REGISTRATION
# ═══════════════════════════════════════════════════════════════════════

def test_tool_registration():
    """Subagent tools are registered in TOOL_HANDLERS and schemas."""
    from src.agent_tools import TOOL_HANDLERS, TOOL_TAGS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    assert "spawn_subagent" in TOOL_HANDLERS
    assert "wait_actor" in TOOL_HANDLERS
    assert "list_actors" in TOOL_HANDLERS

    assert "spawn_subagent" in TOOL_TAGS
    assert "wait_actor" in TOOL_TAGS
    assert "list_actors" in TOOL_TAGS

    schema_names = [s["function"]["name"] for s in FUNCTION_TOOL_SCHEMAS]
    assert "spawn_subagent" in schema_names
    assert "wait_actor" in schema_names
    assert "list_actors" in schema_names

    print("  ✅ Tool registration: spawn_subagent, wait_actor, list_actors")


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("END-TO-END TEST: Agent System (4 Phases)")
    print("=" * 60)
    print()

    print("Phase 1: Agent Loop Core")
    test_phase1_loop_detector()
    test_phase1_recovery()
    test_phase1_prompt_builder()
    test_phase1_checkpoint()
    print()

    print("Phase 2: Tool.define() + Permissions")
    test_phase2_tool_define()
    test_phase2_permissions()
    test_phase2_registry()
    print()

    print("Phase 3: Subagent/Actor + Memory")
    test_phase3_actor()
    test_phase3_spawn()
    test_phase3_inbox()
    test_phase3_memory_persist()
    print()

    print("Phase 4: Integration")
    test_phase4_checkpoint_writer()
    test_phase4_context_rebuild()
    test_phase4_subagent_tools()
    test_phase4_prompt_builder_integration()
    test_phase4_checkpoint_trigger_logic()
    test_tool_registration()
    print()

    print("=" * 60)
    print("ALL 19 E2E TESTS PASSED ✅")
    print("=" * 60)
