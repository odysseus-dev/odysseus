# Tool.define() + Permission System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port MiMo-Code's Tool.define() framework and permission system to Odysseus's Python codebase, providing typed tool definitions with input validation, structured output, and multi-layer permission rules.

**Architecture:** Three new modules in `src/agent/` (tool.py, permission.py, tool_registry.py) plus integration with existing tool handlers via adapter pattern. Old TOOL_HANDLERS continue to work; new tools use Tool.define() natively.

**Tech Stack:** Python 3.9+ (with `from __future__ import annotations`), Pydantic for input validation, existing FastAPI/asyncio infrastructure.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/agent/tool.py` | Tool.define() framework, ToolResult, RecoverableError, Pydantic validation |
| `src/agent/permission.py` | Rule evaluation, pattern matching, agent-specific rulesets, forced-ask |
| `src/agent/tool_registry.py` | Tool registration, resolution, filtering, legacy adapter |
| `tests/test_tool.py` | Unit tests for Tool.define() |
| `tests/test_permission.py` | Unit tests for permission system |
| `tests/test_tool_registry.py` | Unit tests for registry |

---

### Task 1: Tool.define() framework

**Covers:** [S1] Tool definition, validation, structured output

**Files:**
- Create: `src/agent/tool.py`
- Create: `tests/test_tool.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tool.py
"""Tests for src/agent/tool.py"""
from __future__ import annotations
import pytest
from pydantic import BaseModel, Field
from src.agent.tool import Tool, ToolResult, RecoverableError, ToolContext


class EchoParams(BaseModel):
    message: str = Field(description="Message to echo back")
    uppercase: bool = Field(default=False, description="Convert to uppercase")


class FailParams(BaseModel):
    reason: str = Field(description="Reason to fail")


async def _echo_execute(params: EchoParams, ctx: ToolContext) -> ToolResult:
    text = params.message.upper() if params.uppercase else params.message
    return ToolResult(output=text, title=f"Echoed: {text[:30]}")


async def _fail_execute(params: FailParams, ctx: ToolContext) -> ToolResult:
    raise RecoverableError(f"Intentional failure: {params.reason}")


EchoTool = Tool.define(
    "echo",
    description="Echoes a message back",
    parameters=EchoParams,
    execute=_echo_execute,
)

FailTool = Tool.define(
    "fail",
    description="Always fails with a message",
    parameters=FailParams,
    execute=_fail_execute,
)


def test_tool_has_id():
    assert EchoTool.id == "echo"


def test_tool_has_description():
    assert EchoTool.description == "Echoes a message back"


def test_tool_has_parameters():
    assert EchoTool.parameters == EchoParams


def test_tool_result_dataclass():
    result = ToolResult(output="hello", title="Greeting")
    assert result.output == "hello"
    assert result.title == "Greeting"
    assert result.metadata == {}
    assert result.attachments is None


def test_tool_result_with_metadata():
    result = ToolResult(output="ok", title="Done", metadata={"exit_code": 0})
    assert result.metadata == {"exit_code": 0}


@pytest.mark.asyncio
async def test_tool_execute_success():
    ctx = ToolContext(session_id="test", owner="test")
    result = await EchoTool.execute({"message": "hello"}, ctx)
    assert result.output == "hello"
    assert result.title == "Echoed: hello"


@pytest.mark.asyncio
async def test_tool_execute_with_optional():
    ctx = ToolContext(session_id="test", owner="test")
    result = await EchoTool.execute({"message": "hello", "uppercase": True}, ctx)
    assert result.output == "HELLO"


@pytest.mark.asyncio
async def test_tool_execute_validation_error():
    ctx = ToolContext(session_id="test", owner="test")
    with pytest.raises(RecoverableError):
        await EchoTool.execute({}, ctx)  # missing required 'message'


@pytest.mark.asyncio
async def test_tool_execute_recoverable_error():
    ctx = ToolContext(session_id="test", owner="test")
    with pytest.raises(RecoverableError) as exc_info:
        await FailTool.execute({"reason": "test"}, ctx)
    assert "test" in str(exc_info.value)


def test_tool_to_schema():
    schema = EchoTool.to_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"
    assert "message" in schema["function"]["parameters"]["properties"]


def test_legacy_adapter():
    """Old-style handler can be wrapped in Tool.define()."""
    async def old_handler(content: str, ctx: dict) -> dict:
        return {"output": f"old: {content}", "exit_code": 0}

    LegacyTool = Tool.from_legacy("legacy_echo", "Old echo tool", old_handler)
    assert LegacyTool.id == "legacy_echo"
    schema = LegacyTool.to_schema()
    assert schema["function"]["name"] == "legacy_echo"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tool.py -v --noconftest`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement tool.py**

```python
# src/agent/tool.py
"""Tool.define() framework — typed tool definitions with Pydantic validation.

Ported from MiMo-Code patterns:
- Tool.define() for declarative tool creation
- Pydantic models for input validation (replaces Zod)
- ToolResult with title/output/metadata/attachments
- RecoverableError for model-fixable errors
- ToolContext with session/owner/abort info
- Legacy adapter for old-style handlers
"""
from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

P = TypeVar("P", bound=BaseModel)


@dataclass
class ToolResult:
    """Structured output from a tool execution."""
    output: str
    title: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    attachments: Optional[list] = None


class RecoverableError(Exception):
    """Error that the model can fix by retrying with different args.
    
    Unlike fatal errors, RecoverableError is rendered muted in the UI
    and the model sees the error message to correct its approach.
    """
    pass


@dataclass
class ToolContext:
    """Context passed to every tool's execute function."""
    session_id: str = ""
    owner: str = ""
    workspace: str = ""
    progress_cb: Optional[Callable] = None
    abort: Optional[Any] = None  # asyncio.Event or similar
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolInfo:
    """Immutable tool definition — created by Tool.define()."""
    id: str
    description: str
    parameters: Type[BaseModel]
    _execute: Callable
    _schema: Optional[Dict] = field(default=None, repr=False)

    async def execute(self, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Validate args and execute the tool."""
        try:
            params = self.parameters.model_validate(args)
        except ValidationError as e:
            raise RecoverableError(
                f"Invalid arguments for {self.id} tool:\n{e}"
            ) from e
        return await self._execute(params, ctx)

    def to_schema(self) -> Dict:
        """Generate OpenAI-compatible function schema."""
        if self._schema:
            return self._schema
        schema = _pydantic_to_json_schema(self.parameters)
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": schema,
            }
        }


class Tool:
    """Factory for creating tool definitions."""

    @staticmethod
    def define(
        id: str,
        description: str,
        parameters: Type[P],
        execute: Callable[[P, ToolContext], Awaitable[ToolResult]],
    ) -> ToolInfo:
        """Create a new tool definition with typed parameters.
        
        Args:
            id: Unique tool identifier (e.g. "bash", "read_file")
            description: Natural language description for the LLM
            parameters: Pydantic model class for input validation
            execute: Async function(params, ctx) -> ToolResult
        """
        return ToolInfo(
            id=id,
            description=description,
            parameters=parameters,
            _execute=execute,
        )

    @staticmethod
    def from_legacy(
        id: str,
        description: str,
        handler: Callable[[str, Dict], Awaitable[Dict]],
    ) -> ToolInfo:
        """Wrap an old-style handler in Tool.define() interface.
        
        Old handlers have signature: async handler(content: str, ctx: dict) -> dict
        New handlers have signature: async handler(params: BaseModel, ctx: ToolContext) -> ToolResult
        
        This adapter bridges the two by:
        1. Creating a passthrough Pydantic model that accepts raw content
        2. Converting the dict result to ToolResult
        """
        class _PassthroughParams(BaseModel):
            content: str = Field(description="Raw content string")

        async def _adapter(params: _PassthroughParams, ctx: ToolContext) -> ToolResult:
            result = await handler(params.content, {
                "session_id": ctx.session_id,
                "owner": ctx.owner,
                "workspace": ctx.workspace,
                "progress_cb": ctx.progress_cb,
                **ctx.extra,
            })
            if isinstance(result, tuple):
                desc, result = result
            return ToolResult(
                output=result.get("output") or result.get("error") or str(result),
                title=desc if isinstance(result, tuple) else f"{id} result",
                metadata={k: v for k, v in result.items() if k not in ("output", "error")},
            )

        return ToolInfo(
            id=id,
            description=description,
            parameters=_PassthroughParams,
            _execute=_adapter,
        )


def _pydantic_to_json_schema(model: Type[BaseModel]) -> Dict:
    """Convert a Pydantic model to JSON Schema for OpenAI function calling."""
    schema = model.model_json_schema()
    # Clean up Pydantic's extra fields for OpenAI compatibility
    schema.pop("title", None)
    schema.pop("$schema", None)
    # Remove extra fields from properties
    if "properties" in schema:
        for prop in schema["properties"].values():
            prop.pop("title", None)
            prop.pop("default", None)
    return schema
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tool.py -v --noconftest`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/tool.py tests/test_tool.py
git commit -m "feat(agent): add Tool.define() framework with Pydantic validation and structured output"
```

---

### Task 2: Permission system

**Covers:** [S2] Permission rules, pattern matching, agent rulesets

**Files:**
- Create: `src/agent/permission.py`
- Create: `tests/test_permission.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_permission.py
"""Tests for src/agent/permission.py"""
from __future__ import annotations
from src.agent.permission import (
    Action,
    Rule,
    Ruleset,
    evaluate,
    merge_rulesets,
    disabled_tools,
    FORCED_ASK,
    AGENT_PERMISSIONS,
)


def test_rule_creation():
    rule = Rule(permission="bash", pattern="*", action=Action.ALLOW)
    assert rule.permission == "bash"
    assert rule.action == Action.ALLOW


def test_evaluate_allow():
    rules = [Rule(permission="bash", pattern="*", action=Action.ALLOW)]
    result = evaluate("bash", "ls -la", rules)
    assert result.action == Action.ALLOW


def test_evaluate_deny():
    rules = [Rule(permission="bash", pattern="*", action=Action.DENY)]
    result = evaluate("bash", "ls -la", rules)
    assert result.action == Action.DENY


def test_evaluate_last_wins():
    rules = [
        Rule(permission="bash", pattern="*", action=Action.ALLOW),
        Rule(permission="bash", pattern="rm *", action=Action.DENY),
    ]
    result = evaluate("bash", "rm -rf /", rules)
    assert result.action == Action.DENY


def test_evaluate_default_ask():
    rules = []
    result = evaluate("unknown_tool", "something", rules)
    assert result.action == Action.ASK


def test_evaluate_wildcard_permission():
    rules = [Rule(permission="*", pattern="*", action=Action.ALLOW)]
    result = evaluate("any_tool", "any_args", rules)
    assert result.action == Action.ALLOW


def test_evaluate_pattern_match():
    rules = [Rule(permission="bash", pattern="rm *", action=Action.DENY)]
    result = evaluate("bash", "ls -la", rules)
    assert result.action == Action.ALLOW  # default since pattern didn't match
    result2 = evaluate("bash", "rm -rf /", rules)
    assert result2.action == Action.DENY


def test_merge_rulesets():
    base = [Rule(permission="bash", pattern="*", action=Action.ALLOW)]
    override = [Rule(permission="bash", pattern="rm *", action=Action.DENY)]
    merged = merge_rulesets(base, override)
    assert len(merged) == 2
    result = evaluate("bash", "rm -rf /", merged)
    assert result.action == Action.DENY


def test_disabled_tools():
    rules = [
        Rule(permission="bash", pattern="*", action=Action.DENY),
        Rule(permission="web_search", pattern="*", action=Action.ALLOW),
    ]
    disabled = disabled_tools(["bash", "web_search", "read_file"], rules)
    assert "bash" in disabled
    assert "web_search" not in disabled
    assert "read_file" not in disabled


def test_forced_ask_contains_bash_delete():
    assert "bash_delete" in FORCED_ASK


def test_agent_permissions_exist():
    assert "build" in AGENT_PERMISSIONS
    assert "plan" in AGENT_PERMISSIONS
    assert "explore" in AGENT_PERMISSIONS


def test_plan_mode_disables_writes():
    plan_rules = AGENT_PERMISSIONS["plan"]
    result = evaluate("edit_file", "src/main.py", plan_rules)
    assert result.action == Action.DENY


def test_explore_mode_limited():
    explore_rules = AGENT_PERMISSIONS["explore"]
    result = evaluate("write_file", "test.py", explore_rules)
    assert result.action == Action.DENY
    result2 = evaluate("read_file", "test.py", explore_rules)
    assert result2.action == Action.ALLOW
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_permission.py -v --noconftest`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement permission.py**

```python
# src/agent/permission.py
"""Permission system — rule-based allow/deny/ask with pattern matching.

Ported from MiMo-Code patterns:
- Rule(permission, pattern, action) with findLast semantics
- Pattern matching with wildcard support
- FORCED_ASK for tools requiring human confirmation
- Agent-specific rulesets (build, plan, explore)
- Merge function for layering rulesets
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Set


class Action(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class Rule:
    """A single permission rule."""
    permission: str   # Tool name or glob (e.g. "bash", "*")
    pattern: str      # Resource pattern (e.g. "rm *", "/tmp/*")
    action: Action


Ruleset = List[Rule]


# Tools that ALWAYS require human confirmation
FORCED_ASK: Set[str] = {
    "bash_delete",
}


def evaluate(
    permission: str,
    pattern: str,
    ruleset: Ruleset,
) -> Rule:
    """Evaluate a permission request against a ruleset.
    
    Uses findLast semantics — the LAST matching rule wins.
    This allows config layering where user rules override defaults.
    """
    matching = [
        rule for rule in ruleset
        if fnmatch.fnmatch(permission, rule.permission)
        and fnmatch.fnmatch(pattern, rule.pattern)
    ]
    if matching:
        return matching[-1]
    # Default: ask
    return Rule(permission=permission, pattern="*", action=Action.ASK)


def merge_rulesets(*rulesets: Ruleset) -> Ruleset:
    """Merge multiple rulesets, preserving order (later rulesets win)."""
    result = []
    for rs in rulesets:
        result.extend(rs)
    return result


def disabled_tools(tools: List[str], ruleset: Ruleset) -> Set[str]:
    """Find tools that are disabled by the ruleset."""
    result = set()
    for tool in tools:
        rule = evaluate(tool, "*", ruleset)
        if rule.action == Action.DENY:
            result.add(tool)
    return result


# Agent-specific permission rulesets
AGENT_PERMISSIONS: dict[str, Ruleset] = {
    "build": [
        Rule(permission="*", pattern="*", action=Action.ALLOW),
    ],
    "plan": [
        Rule(permission="*", pattern="*", action=Action.ALLOW),
        Rule(permission="write_file", pattern="*", action=Action.DENY),
        Rule(permission="edit_file", pattern="*", action=Action.DENY),
        Rule(permission="bash", pattern="*", action=Action.DENY),
    ],
    "explore": [
        Rule(permission="*", pattern="*", action=Action.DENY),
        Rule(permission="read_file", pattern="*", action=Action.ALLOW),
        Rule(permission="ls", pattern="*", action=Action.ALLOW),
        Rule(permission="glob", pattern="*", action=Action.ALLOW),
        Rule(permission="grep", pattern="*", action=Action.ALLOW),
        Rule(permission="web_search", pattern="*", action=Action.ALLOW),
        Rule(permission="web_fetch", pattern="*", action=Action.ALLOW),
    ],
    "compose": [
        Rule(permission="*", pattern="*", action=Action.ALLOW),
    ],
    "general": [
        Rule(permission="*", pattern="*", action=Action.ALLOW),
        Rule(permission="manage_session", pattern="*", action=Action.DENY),
    ],
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_permission.py -v --noconftest`
Expected: All 14 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/permission.py tests/test_permission.py
git commit -m "feat(agent): add permission system with rule-based allow/deny/ask and agent rulesets"
```

---

### Task 3: Tool registry

**Covers:** [S3] Tool registration, resolution, legacy adapter

**Files:**
- Create: `src/agent/tool_registry.py`
- Create: `tests/test_tool_registry.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tool_registry.py
"""Tests for src/agent/tool_registry.py"""
from __future__ import annotations
import pytest
from pydantic import BaseModel, Field
from src.agent.tool import Tool, ToolResult, ToolContext
from src.agent.permission import Action, Rule, Ruleset
from src.agent.tool_registry import ToolRegistry


class EchoParams(BaseModel):
    message: str = Field(description="Message to echo")


async def _echo(params: EchoParams, ctx: ToolContext) -> ToolResult:
    return ToolResult(output=params.message, title="Echo")


EchoTool = Tool.define("echo", "Echoes message", EchoParams, _echo)


def test_registry_register():
    reg = ToolRegistry()
    reg.register(EchoTool)
    assert "echo" in reg.list_tools()


def test_registry_register_duplicate():
    reg = ToolRegistry()
    reg.register(EchoTool)
    reg.register(EchoTool)  # should not raise
    assert len(reg.list_tools()) == 1


def test_registry_get():
    reg = ToolRegistry()
    reg.register(EchoTool)
    tool = reg.get("echo")
    assert tool is EchoTool


def test_registry_get_unknown():
    reg = ToolRegistry()
    assert reg.get("nonexistent") is None


def test_registry_resolve_all_allowed():
    reg = ToolRegistry()
    reg.register(EchoTool)
    tools = reg.resolve()
    assert len(tools) == 1


def test_registry_resolve_with_deny():
    reg = ToolRegistry()
    reg.register(EchoTool)
    rules = [Rule(permission="echo", pattern="*", action=Action.DENY)]
    tools = reg.resolve(ruleset=rules)
    assert len(tools) == 0


def test_registry_resolve_with_allowlist():
    reg = ToolRegistry()
    reg.register(EchoTool)
    # Register another tool
    class FooParams(BaseModel):
        x: int = Field(default=1)
    async def _foo(p: FooParams, c: ToolContext) -> ToolResult:
        return ToolResult(output="foo", title="Foo")
    FooTool = Tool.define("foo", "Foo tool", FooParams, _foo)
    reg.register(FooTool)
    
    tools = reg.resolve(allowlist={"echo"})
    assert len(tools) == 1
    assert tools[0].id == "echo"


def test_registry_disabled():
    reg = ToolRegistry()
    reg.register(EchoTool)
    rules = [Rule(permission="echo", pattern="*", action=Action.DENY)]
    disabled = reg.disabled(rules)
    assert "echo" in disabled


def test_registry_to_schemas():
    reg = ToolRegistry()
    reg.register(EchoTool)
    schemas = reg.to_schemas()
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "echo"


def test_registry_from_legacy():
    reg = ToolRegistry()
    async def old_handler(content: str, ctx: dict) -> dict:
        return {"output": f"old: {content}", "exit_code": 0}
    reg.register_legacy("old_tool", "Old tool", old_handler)
    assert "old_tool" in reg.list_tools()
    tool = reg.get("old_tool")
    assert tool.id == "old_tool"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tool_registry.py -v --noconftest`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement tool_registry.py**

```python
# src/agent/tool_registry.py
"""Tool registry — registration, resolution, filtering, legacy adapter.

Central registry for all tools (both Tool.define() and legacy handlers).
Provides resolution by agent permissions, allowlists, and model capabilities.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Dict, List, Optional, Set

from src.agent.tool import Tool, ToolInfo, ToolContext, ToolResult
from src.agent.permission import (
    Action,
    Rule,
    Ruleset,
    evaluate,
    disabled_tools,
)

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Central registry for tool definitions.
    
    Supports both Tool.define() tools and legacy handlers via adapter.
    """
    _tools: Dict[str, ToolInfo]

    def __init__(self) -> None:
        self._tools = {}

    def register(self, tool: ToolInfo) -> None:
        """Register a Tool.define() tool."""
        if tool.id in self._tools:
            logger.debug(f"Tool {tool.id} already registered, overwriting")
        self._tools[tool.id] = tool

    def register_legacy(
        self,
        id: str,
        description: str,
        handler: Callable[[str, Dict], Awaitable[Dict]],
    ) -> None:
        """Register an old-style handler via adapter."""
        tool = Tool.from_legacy(id, description, handler)
        self.register(tool)

    def get(self, id: str) -> Optional[ToolInfo]:
        """Get a tool by ID."""
        return self._tools.get(id)

    def list_tools(self) -> List[str]:
        """List all registered tool IDs."""
        return list(self._tools.keys())

    def resolve(
        self,
        ruleset: Optional[Ruleset] = None,
        allowlist: Optional[Set[str]] = None,
    ) -> List[ToolInfo]:
        """Resolve available tools based on permissions and allowlist.
        
        Args:
            ruleset: Permission rules to filter by
            allowlist: Hard whitelist of tool IDs (if set, only these are allowed)
        """
        tools = list(self._tools.values())

        # Apply allowlist filter
        if allowlist is not None:
            tools = [t for t in tools if t.id in allowlist]

        # Apply permission filter
        if ruleset is not None:
            disabled = disabled_tools([t.id for t in tools], ruleset)
            tools = [t for t in tools if t.id not in disabled]

        return tools

    def disabled(self, ruleset: Ruleset) -> Set[str]:
        """Find which tools are disabled by the given ruleset."""
        return disabled_tools(list(self._tools.keys()), ruleset)

    def to_schemas(self, tool_ids: Optional[Set[str]] = None) -> List[Dict]:
        """Generate OpenAI-compatible function schemas for tools.
        
        Args:
            tool_ids: If provided, only generate schemas for these tools
        """
        tools = self._tools.values()
        if tool_ids is not None:
            tools = [t for t in tools if t.id in tool_ids]
        return [t.to_schema() for t in tools]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tool_registry.py -v --noconftest`
Expected: All 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/tool_registry.py tests/test_tool_registry.py
git commit -m "feat(agent): add tool registry with resolution, filtering, and legacy adapter"
```

---

### Task 4: Integration with existing tools

**Covers:** [S4] Wire new framework into existing agent_loop.py

**Files:**
- Modify: `src/agent/__init__.py`
- Modify: `src/agent_loop.py` (targeted edits)

- [ ] **Step 1: Update agent package exports**

```python
# src/agent/__init__.py
"""Odysseus agent loop package — modular rewrite based on MiMo-Code patterns."""
from __future__ import annotations

from src.agent.loop_detector import LoopDetector, RecoveryLevel, StableSignature
from src.agent.recovery import RecoveryPrompts, IntentSupervisor
from src.agent.prompt_builder import PromptBuilder, PromptSection
from src.agent.checkpoint import ContextManager, CompactionResult
from src.agent.tool import Tool, ToolResult, ToolContext, RecoverableError, ToolInfo
from src.agent.permission import Action, Rule, Ruleset, evaluate, AGENT_PERMISSIONS
from src.agent.tool_registry import ToolRegistry

__all__ = [
    # Loop detection
    "LoopDetector", "RecoveryLevel", "StableSignature",
    # Recovery
    "RecoveryPrompts", "IntentSupervisor",
    # Prompt building
    "PromptBuilder", "PromptSection",
    # Context management
    "ContextManager", "CompactionResult",
    # Tool framework
    "Tool", "ToolResult", "ToolContext", "RecoverableError", "ToolInfo",
    # Permissions
    "Action", "Rule", "Ruleset", "evaluate", "AGENT_PERMISSIONS",
    # Registry
    "ToolRegistry",
]
```

- [ ] **Step 2: Create bridge module for existing tools**

```python
# src/agent/legacy_bridge.py
"""Bridge between new Tool.define() framework and existing TOOL_HANDLERS.

This module wraps the existing tool handlers in ToolInfo objects,
allowing gradual migration without breaking the current system.
"""
from __future__ import annotations

import logging
from typing import Dict

from src.agent.tool import Tool, ToolInfo, ToolResult, ToolContext
from src.agent.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def create_registry_from_legacy() -> ToolRegistry:
    """Create a ToolRegistry populated with existing TOOL_HANDLERS.
    
    Each legacy handler is wrapped via Tool.from_legacy() adapter.
    """
    from src.agent_tools import TOOL_HANDLERS

    registry = ToolRegistry()
    for name, handler in TOOL_HANDLERS.items():
        try:
            registry.register_legacy(name, f"Legacy tool: name}", handler)
        except Exception as e:
            logger.warning(f"Failed to register legacy tool {name}: {e}")
    return registry


def get_tool_schemas_for_agent(
    agent: str = "build",
    disabled: set = None,
    relevant_tools: set = None,
) -> list:
    """Get tool schemas filtered by agent permissions and relevance.
    
    This bridges the new registry with the existing schema generation.
    """
    from src.agent.permission import AGENT_PERMISSIONS

    registry = create_registry_from_legacy()
    ruleset = AGENT_PERMISSIONS.get(agent, [])
    
    # Apply disabled tools
    if disabled:
        for tool_id in disabled:
            ruleset = ruleset + [__import__("src.agent.permission", fromlist=["Rule"]).Rule(
                permission=tool_id, pattern="*", action=__import__("src.agent.permission", fromlist=["Action"]).Action.DENY
            )]
    
    tools = registry.resolve(ruleset=ruleset, allowlist=relevant_tools)
    return registry.to_schemas(tool_ids={t.id for t in tools})
```

- [ ] **Step 3: Run all tests**

Run: `python -m pytest tests/test_tool.py tests/test_permission.py tests/test_tool_registry.py -v --noconftest`
Expected: All 37 tests PASS

- [ ] **Step 4: Verify imports**

Run: `python -c "from src.agent import Tool, ToolResult, ToolRegistry, AGENT_PERMISSIONS; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 5: Deploy to Docker**

```bash
echo "rt67we45" | sudo -S docker cp src/agent/. odysseus-odysseus-1:/app/src/agent/
echo "rt67we45" | sudo -S docker exec odysseus-odysseus-1 python -c "from src.agent import Tool, ToolResult, ToolRegistry, AGENT_PERMISSIONS; print('Docker imports OK')"
```

- [ ] **Step 6: Commit**

```bash
git add src/agent/__init__.py src/agent/legacy_bridge.py
git commit -m "feat(agent): add tool framework integration with legacy bridge"
```
