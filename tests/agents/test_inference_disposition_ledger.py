"""Characterization: every production inference caller has a ledger row."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

_ROOT = Path(__file__).resolve().parents[2]
_LEDGER = _ROOT / "docs" / "architecture" / "evidence" / "inference-disposition-ledger.md"
_SCAN_ROOTS = ("src", "routes", "services", "mcp_servers")
_PRIMITIVE_FILES = frozenset({"src/llm_core.py"})
_PRIMITIVE_WRAPPERS = frozenset({("src/task_endpoint.py", "task_llm_call_async")})
_INFERENCE_NAMES = frozenset(
    {
        "stream_llm",
        "stream_llm_with_fallback",
        "llm_call",
        "llm_call_async",
        "llm_call_async_with_fallback",
        "llm_call_async_with_route_fallback",
        "task_llm_call_async",
    }
)
_DISPOSITIONS = frozenset(
    {
        "agent-conversation",
        "bounded-job",
        "deterministic",
        "keep-specialist",
        "delete",
        "held",
    }
)
_REQUIRED_COLUMNS = (
    "path",
    "symbol",
    "disposition",
    "replacement-or-rationale",
    "notes",
)
_SPECIALIST_REQUIRED = frozenset(
    {
        ("routes/embedding_routes.py", "setup_embedding_routes"),
        ("routes/stt_routes.py", "transcribe_audio"),
        ("routes/tts_routes.py", "synthesize_speech"),
        ("src/ai_interaction.py", "do_generate_image"),
        ("src/research_handler.py", "ResearchHandler._probe_endpoint"),
    }
)
_HELD_REQUIRED = frozenset(
    {
        ("src/agent_loop.py", "stream_agent_loop"),
        ("src/agent_loop.py", "_run_verifier_subagent"),
        ("src/tool_execution.py", "execute_tool_block"),
    }
)


def _enclosing_symbol(stack: list[ast.AST]) -> str:
    """Return Class.method or function name for the current AST stack.

    Parameters
    ----------
    stack : list[ast.AST]
        Ancestors from the module root to the current node.

    Returns
    -------
    str
        Innermost function, prefixed by class when nested in one.

    Example
    -------
    ``ChatProcessor.build_context_preface`` for a method call site.
    """
    classes: list[str] = []
    funcs: list[str] = []
    for node in stack:
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node.name)
    if not funcs:
        return "<module>"
    prefix = ".".join(classes)
    name = funcs[-1]
    return f"{prefix}.{name}" if prefix else name


def scan_production_inference_callers() -> set[tuple[str, str]]:
    """AST-scan production trees for remaining inference primitives.

    Comments and tests are excluded. Primitive definitions in
    ``src/llm_core.py`` and ``task_llm_call_async`` are not callers.

    Returns
    -------
    set[tuple[str, str]]
        ``(relative_path, enclosing_symbol)`` pairs.

    Example
    -------
    ``("routes/chat_helpers.py", "auto_name_session")`` is one caller.
    """
    found: set[tuple[str, str]] = set()
    for top in _SCAN_ROOTS:
        base = _ROOT / top
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(_ROOT).as_posix()
            if rel in _PRIMITIVE_FILES:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            stack: list[ast.AST] = []

            class _Visitor(ast.NodeVisitor):
                def generic_visit(self, node: ast.AST) -> None:
                    stack.append(node)
                    for child in ast.iter_child_nodes(node):
                        self.visit(child)
                    stack.pop()

                def visit_Call(self, node: ast.Call) -> None:
                    func = node.func
                    name = getattr(func, "id", None) or getattr(func, "attr", None)
                    if name in _INFERENCE_NAMES:
                        symbol = _enclosing_symbol(stack)
                        if (rel, symbol) not in _PRIMITIVE_WRAPPERS:
                            found.add((rel, symbol))
                    self.generic_visit(node)

            _Visitor().visit(tree)
    return found


def parse_disposition_ledger(text: str) -> list[dict[str, str]]:
    """Parse the markdown disposition table into row dicts.

    Parameters
    ----------
    text : str
        Full ledger markdown.

    Returns
    -------
    list[dict[str, str]]
        One dict per data row, keyed by header names.

    Example
    -------
    A row ``| a.py | foo | held | Task 18 | residual |`` becomes a dict.
    """
    tables: list[tuple[list[str], list[dict[str, str]]]] = []
    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            if header is not None:
                tables.append((header, rows))
                header = None
                rows = []
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            rows = []
            continue
        if cells and set(cells[0]) <= {"-", ":"}:
            continue
        if len(cells) != len(header):
            raise AssertionError(f"ledger row column mismatch: {line}")
        rows.append(dict(zip(header, cells)))
    if header is not None:
        tables.append((header, rows))
    for header, rows in tables:
        if all(c in header for c in _REQUIRED_COLUMNS):
            return rows
    raise AssertionError(
        f"ledger has no table with columns {_REQUIRED_COLUMNS}"
    )


def _function_names(path: Path) -> set[str]:
    """Collect function and Class.method names from a Python file.

    Parameters
    ----------
    path : Path
        File to parse.

    Returns
    -------
    set[str]
        Bare names plus Class.method forms.

    Example
    -------
    ``{"stream_agent_loop", "AgentLoop.foo"}``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    stack: list[ast.AST] = []

    class _Visitor(ast.NodeVisitor):
        def generic_visit(self, node: ast.AST) -> None:
            stack.append(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
                names.add(_enclosing_symbol(stack))
            for child in ast.iter_child_nodes(node):
                self.visit(child)
            stack.pop()

    _Visitor().visit(tree)
    return names


def test_ledger_exists_and_covers_every_production_caller():
    assert _LEDGER.is_file(), f"missing disposition ledger: {_LEDGER}"
    rows = parse_disposition_ledger(_LEDGER.read_text(encoding="utf-8"))
    listed = {(row["path"], row["symbol"]) for row in rows}
    missing = sorted(scan_production_inference_callers() - listed)
    assert missing == [], f"production inference callers missing ledger rows: {missing}"


def test_every_ledger_row_has_exactly_one_allowed_disposition():
    rows = parse_disposition_ledger(_LEDGER.read_text(encoding="utf-8"))
    keys = [(row["path"], row["symbol"]) for row in rows]
    assert len(keys) == len(set(keys)), f"duplicate ledger keys: {keys}"
    for row in rows:
        disp = row["disposition"]
        assert disp in _DISPOSITIONS, f"{row['path']} {row['symbol']} disposition {disp!r}"
        assert row["replacement-or-rationale"], f"{row['path']} {row['symbol']} empty rationale"


def test_agent_loop_and_execute_tool_block_are_held():
    rows = parse_disposition_ledger(_LEDGER.read_text(encoding="utf-8"))
    by_key = {(row["path"], row["symbol"]): row["disposition"] for row in rows}
    for key in _HELD_REQUIRED:
        assert by_key.get(key) == "held", f"{key} must be held (Task 18)"
    for row in rows:
        if row["path"] == "src/agent_loop.py":
            assert row["disposition"] == "held", f"{row['symbol']} in agent_loop must be held"


def test_specialists_are_keep_specialist_not_agent():
    rows = parse_disposition_ledger(_LEDGER.read_text(encoding="utf-8"))
    by_key = {(row["path"], row["symbol"]): row["disposition"] for row in rows}
    for key in _SPECIALIST_REQUIRED:
        assert by_key.get(key) == "keep-specialist", (
            f"{key} must be keep-specialist, not fake-agent (AE7)"
        )
    for row in rows:
        if row["disposition"] == "keep-specialist":
            assert row["disposition"] != "agent-conversation"


def test_ledger_symbols_exist_in_named_files():
    rows = parse_disposition_ledger(_LEDGER.read_text(encoding="utf-8"))
    for row in rows:
        path = _ROOT / row["path"]
        assert path.is_file(), f"ledger path missing: {row['path']}"
        names = _function_names(path)
        assert row["symbol"] in names, f"{row['path']} has no symbol {row['symbol']}"
