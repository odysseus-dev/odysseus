"""Runner-side MLX HF-cache resolution (#5978 follow-up).

The cookbook runner's MLX model-path resolver must find locally cached
`mlx-community/...` snapshots. It previously listed only the Linux cache root
(`~/.cache/huggingface/hub`), so on macOS — where `huggingface_hub` defaults to
`~/Library/Caches/huggingface/hub` — a downloaded model could not be resolved at
serve time. Same defect class as issue #5978 (scan path), fixed here on the
serve path.

The resolver ships as generated Python lines inside
`routes/cookbook_routes.py` (the tmux runner script), so the test extracts the
candidate-list block from the SHIPPED string literals via AST and executes it —
asserting on the executed behavior (the resulting `roots` list), not on source
text. Extraction is required because the block only exists as emitted runner
content; this mirrors `tests/test_cookbook_stale_shim_recovery.py`, which
extracts and runs shipped shell lines for the same reason.
"""
import ast
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ROUTES = REPO / "routes" / "cookbook_routes.py"


def _shipped_runner_lines() -> list:
    """Every `runner_lines.append("<literal>")` string in the route source."""
    tree = ast.parse(ROUTES.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and getattr(node.func.value, "id", "") == "runner_lines"
            and node.args
        ):
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.append(arg.value)
    return out


def _cache_block() -> list:
    """The MLX resolver's candidate-list block, in shipped order."""
    lines = _shipped_runner_lines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "roots = []")
    end = next(
        i for i, ln in enumerate(lines)
        if i > start and ln.strip().startswith("cache_name =")
    )
    block = lines[start:end]
    assert any("HUGGINGFACE_HUB_CACHE" in ln for ln in block)
    assert any("add(" in ln for ln in block)
    return block


def _roots(monkeypatch, platform: str, home, hf_home=None, hub_cache=None) -> list:
    """Execute the shipped block with a controlled platform + HOME."""
    monkeypatch.setenv("HOME", str(home))
    for var in ("HF_HOME", "HUGGINGFACE_HUB_CACHE"):
        monkeypatch.delenv(var, raising=False)
    if hf_home:
        monkeypatch.setenv("HF_HOME", str(hf_home))
    if hub_cache:
        monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(hub_cache))
    monkeypatch.setattr("sys.platform", platform)

    import os
    import sys

    ns = {"os": os, "sys": sys}
    # The shipped block lives inside the runner script's `if` body, so it
    # carries that indentation; dedent before compiling it standalone.
    program = textwrap.dedent("\n".join(_cache_block())) + "\n__ROOTS = list(roots)\n"
    exec(compile(program, "<shipped-mlx-cache-block>", "exec"), ns)
    return ns["__ROOTS"]


def test_macos_includes_library_caches_root(monkeypatch, tmp_path):
    """macOS resolves the platform default cache (#5978 class)."""
    roots = _roots(monkeypatch, "darwin", tmp_path)
    assert any("Library/Caches/huggingface" in r for r in roots), roots


def test_linux_keeps_dotcache_root(monkeypatch, tmp_path):
    """Linux behavior is unchanged (no regression for the common case)."""
    roots = _roots(monkeypatch, "linux", tmp_path)
    assert any(r.endswith(".cache/huggingface/hub") for r in roots), roots
    assert not any("Library/Caches" in r for r in roots), roots


def test_explicit_cache_env_wins_first(monkeypatch, tmp_path):
    """An explicit HUGGINGFACE_HUB_CACHE is honored ahead of the defaults."""
    explicit = tmp_path / "custom-hub"
    roots = _roots(monkeypatch, "linux", tmp_path, hub_cache=explicit)
    assert roots[0] == str(explicit), roots


def test_hf_home_hub_is_included(monkeypatch, tmp_path):
    """HF_HOME-based caches are still searched."""
    hf_home = tmp_path / "hfhome"
    roots = _roots(monkeypatch, "linux", tmp_path, hf_home=hf_home)
    assert str(hf_home / "hub") in roots, roots


def test_darwin_and_linux_sets_are_not_identical(monkeypatch, tmp_path):
    """Guards against a regression back to a single hard-coded root."""
    mac = _roots(monkeypatch, "darwin", tmp_path)
    lin = _roots(monkeypatch, "linux", tmp_path)
    assert mac != lin, (mac, lin)