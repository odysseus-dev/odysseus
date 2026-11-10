"""Every procfs pid scan in the app tree must be guarded by an existence check.

This is the third instance of the same defect: code walks ``/proc`` on a host
that has no procfs, and the resulting ``FileNotFoundError`` breaks a path that
had otherwise succeeded. Two of the three were found by reading source, so
this pins the class rather than the instances.

Deliberate AST assertion under the narrow exception in
``tests/TESTING_STANDARD.md``: the invariant is "no *other* module grows an
unguarded scan", which cannot be driven at runtime without importing and
exercising every procfs-touching code path on both a Linux and a non-Linux
host. ``test_cookbook_stop_without_procfs.py`` covers the behaviour itself.
"""
import ast

import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Trees that ship as the application. Tests and vendored code are excluded.
APP_TREES = (
    "app.py",
    "core",
    "routes",
    "src",
    "services",
    "scripts",
    "mcp_servers",
    "integrations",
    "companion",
)

# Calls that enumerate a directory's entries. Reading one known file under
# /proc is a different shape — it fails per-file and callers already handle
# that — so only the enumerating calls are in scope here.
_SCAN_FUNCS = {"listdir", "scandir"}
_SCAN_METHODS = {"iterdir", "glob", "rglob"}

# Calls that prove the scan is conditional on procfs being present.
_GUARD_FUNCS = {"has_procfs", "isdir", "is_dir", "exists"}

PROCFS_ROOT = "/proc"


def _is_procfs_root(node: ast.AST) -> bool:
    """True if ``node`` evaluates to the procfs root directory."""
    if isinstance(node, ast.Constant) and node.value == PROCFS_ROOT:
        return True
    # Path("/proc")
    if isinstance(node, ast.Call):
        return any(_is_procfs_root(arg) for arg in node.args)
    # PROC_ROOT / _PROC_ROOT / proc_root / platform_compat.PROC_ROOT
    name = None
    if isinstance(node, ast.Name):
        name = node.id
    elif isinstance(node, ast.Attribute):
        name = node.attr
    return bool(name) and name.lower().lstrip("_") == "proc_root"


def _scan_sites(tree: ast.AST) -> list[ast.Call]:
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _SCAN_FUNCS:
            if node.args and _is_procfs_root(node.args[0]):
                sites.append(node)
        elif isinstance(func, ast.Name) and func.id in _SCAN_FUNCS:
            if node.args and _is_procfs_root(node.args[0]):
                sites.append(node)
        elif isinstance(func, ast.Attribute) and func.attr in _SCAN_METHODS:
            if _is_procfs_root(func.value):
                sites.append(node)
    return sites


def _condition_requires_procfs(node: ast.AST, truth: bool) -> bool:
    """Whether this Boolean outcome proves a direct procfs check succeeded.

    An ignored call, another function's check, or a check on only one possible
    OR branch provides no proof. Keep unknown expressions conservative.
    """
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _condition_requires_procfs(node.operand, not truth)
    if isinstance(node, ast.BoolOp):
        facts = [_condition_requires_procfs(value, truth) for value in node.values]
        all_values_required = isinstance(node.op, ast.And) == truth
        return any(facts) if all_values_required else all(facts)
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    subject_is_procfs = (
        name == "has_procfs"
        or (isinstance(func, ast.Attribute) and _is_procfs_root(func.value))
        or any(_is_procfs_root(arg) for arg in node.args)
    )
    return truth and name in _GUARD_FUNCS and subject_is_procfs


def _guarded_scan_ids(tree: ast.AST) -> set[int]:
    """Follow conditional/early-exit paths instead of comparing line numbers.

    Each function starts with no procfs assumption. Facts from a loop or try
    body cannot escape to code which may run without that body completing.
    This intentionally requires direct checks; arbitrary aliases/call graphs
    are not a proof that procfs exists at the enumeration site.
    """
    scans = {id(site) for site in _scan_sites(tree)}
    guarded = set()

    def expression(node, present):
        if node is None:
            return
        if id(node) in scans and present:
            guarded.add(id(node))
        if isinstance(node, ast.BoolOp):
            for value in node.values:
                expression(value, present)
                # Later AND operands run only after a true prefix; later OR
                # operands run only after a false prefix.
                present = present or _condition_requires_procfs(value, isinstance(node.op, ast.And))
        elif isinstance(node, ast.IfExp):
            expression(node.test, present)
            expression(node.body, present or _condition_requires_procfs(node.test, True))
            expression(node.orelse, present or _condition_requires_procfs(node.test, False))
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for generator in node.generators:
                expression(generator.iter, present)
                for condition in generator.ifs:
                    expression(condition, present)
                    present = present or _condition_requires_procfs(condition, True)
            for attr in ("elt", "key", "value"):
                expression(getattr(node, attr, None), present)
        elif isinstance(node, ast.Lambda):
            expression(node.body, False)
        else:
            for child in ast.iter_child_nodes(node):
                expression(child, present)

    def block(statements, present):
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                block(statement.body, False)
                for value in statement.decorator_list + statement.args.defaults:
                    expression(value, present)
            elif isinstance(statement, ast.ClassDef):
                block(statement.body, present)
            elif isinstance(statement, ast.If):
                expression(statement.test, present)
                yes = block(statement.body, present or _condition_requires_procfs(statement.test, True))
                no = block(statement.orelse, present or _condition_requires_procfs(statement.test, False))
                continuing = [fact for fact in (yes, no) if fact is not None]
                if not continuing:
                    return None
                present = all(continuing)
            elif isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                for child in ast.iter_child_nodes(statement):
                    expression(child, present)
                return None
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                condition = statement.test if isinstance(statement, ast.While) else statement.iter
                expression(condition, present)
                body_present = present or (isinstance(statement, ast.While) and _condition_requires_procfs(condition, True))
                block(statement.body, body_present)
                block(statement.orelse, present)
            elif isinstance(statement, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                block(statement.body, present)
                block(statement.orelse, present)
                block(statement.finalbody, present)
                for handler in statement.handlers:
                    block(handler.body, present)
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                for item in statement.items:
                    expression(item.context_expr, present)
                block(statement.body, present)
            else:
                expression(statement, present)
        return present

    block(tree.body, False)
    return guarded


def _app_python_files() -> list[Path]:
    files = []
    for entry in APP_TREES:
        target = REPO_ROOT / entry
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            files.extend(
                p for p in target.rglob("*.py") if "__pycache__" not in p.parts
            )
    return sorted(files)


def _collect_sites() -> tuple[list[str], list[str]]:
    """Return (guarded, unguarded) ``path:line`` labels for procfs scans."""
    guarded, unguarded = [], []
    for path in _app_python_files():
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        # Cheap pre-filter: a scan has to name the root somehow.
        if PROCFS_ROOT not in source and "proc_root" not in source.lower():
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        sites = _scan_sites(tree)
        if not sites:
            continue
        guards = _guarded_scan_ids(tree)
        for site in sites:
            label = f"{path.relative_to(REPO_ROOT)}:{site.lineno}"
            if id(site) in guards:
                guarded.append(label)
            else:
                unguarded.append(label)
    return guarded, unguarded


def test_every_procfs_scan_is_guarded_by_an_existence_check():
    _guarded, unguarded = _collect_sites()
    assert not unguarded, (
        "procfs pid scans without an effective conditional existence guard — "
        "these raise FileNotFoundError on macOS and Windows: "
        + ", ".join(unguarded)
    )


def test_the_guard_detector_still_sees_the_known_scans():
    """A rename must not silently turn the assertion above into a no-op.

    Lower bound, not an exact count: a new *guarded* scan is fine and should
    not fail this. What must not happen is the detector going blind, which
    shows up as sites disappearing.
    """
    guarded, unguarded = _collect_sites()
    found = set(guarded) | set(unguarded)
    files = {label.rsplit(":", 1)[0] for label in found}
    known = {"src/agent_tools/web_tools.py", "src/tools/cookbook.py"}
    assert known <= files, (
        "the detector no longer sees a known procfs scan — check whether the "
        f"root was renamed. Found: {sorted(found)}"
    )
    assert len(found) >= 3, f"expected at least 3 procfs scans, found {sorted(found)}"


@pytest.mark.parametrize("body", [
    'Path("/proc").exists(); os.listdir("/proc")',
    'if Path("/proc").exists():\n    pass\nos.listdir("/proc")',
    'if not Path("/proc").exists():\n    os.listdir("/proc")',
    'if ready or Path("/proc").exists():\n    os.listdir("/proc")',
    'if ready:\n    Path("/proc").exists()\nos.listdir("/proc")',
    'os.listdir("/proc"); Path("/proc").exists()',
    'if Path("/tmp").exists():\n    os.listdir("/proc")',
    'def other():\n    Path("/proc").exists()\nos.listdir("/proc")',
    'if not has_procfs():\n    pass\nos.listdir("/proc")',
    'for x in []:\n    if not has_procfs():\n        return\nos.listdir("/proc")',
    'if has_procfs():\n    return\nelse:\n    os.listdir("/proc")',
    '[pid for pid in os.listdir("/proc") if has_procfs()]',
], ids=["ignored", "expired-branch", "inverted", "unsafe-or", "sibling-branch",
        "too-late", "wrong-path", "other-function", "no-early-exit", "loop-may-not-run",
        "wrong-else", "late-comprehension-filter"])
def test_guard_detector_rejects_ineffective_checks(body):
    tree = ast.parse("def scan():\n" + "\n".join("    " + line for line in body.splitlines()))
    sites = _scan_sites(tree)
    assert sites, "negative control must contain a recognized enumeration"
    assert any(id(site) not in _guarded_scan_ids(tree) for site in sites)


@pytest.mark.parametrize("body", [
    'if Path("/proc").exists():\n    os.listdir("/proc")',
    'if not Path("/proc").exists():\n    return\nos.listdir("/proc")',
    'if not has_procfs():\n    raise RuntimeError()\nos.listdir(PROC_ROOT)',
    'if has_procfs():\n    pass\nelse:\n    return\nPROC_ROOT.iterdir()',
    'if ready and has_procfs():\n    os.scandir("/proc")',
    'if not has_procfs() or disabled:\n    return\nPROC_ROOT.glob("*")',
    'if sys.platform == "linux" and has_procfs():\n    PROC_ROOT.iterdir()',
    'for item in items:\n    if not has_procfs():\n        continue\n    os.listdir("/proc")',
    'if os.path.isdir("/proc"):\n    os.listdir("/proc")',
    'if has_procfs() and list(PROC_ROOT.iterdir()):\n    pass',
    'if not has_procfs() or list(PROC_ROOT.iterdir()):\n    pass',
    '[pid for item in items if has_procfs() for pid in os.listdir("/proc")]',
], ids=["positive-branch", "early-return", "early-raise", "else-exits", "and",
        "negative-or", "platform-and-procfs", "loop-continue", "isdir",
        "short-circuit-and", "short-circuit-or", "guarded-comprehension"])
def test_guard_detector_preserves_effective_checks(body):
    tree = ast.parse("def scan():\n" + "\n".join("    " + line for line in body.splitlines()))
    sites = _scan_sites(tree)
    assert sites, "positive control must contain a recognized enumeration"
    assert all(id(site) in _guarded_scan_ids(tree) for site in sites)
