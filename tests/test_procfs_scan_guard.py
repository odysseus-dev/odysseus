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


def _guard_lines(tree: ast.AST) -> list[int]:
    """Line numbers of calls that test whether procfs is present."""
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in _GUARD_FUNCS:
            continue
        subject_is_procfs = (
            name in {"has_procfs", "is_wsl"}
            or (isinstance(func, ast.Attribute) and _is_procfs_root(func.value))
            or any(_is_procfs_root(arg) for arg in node.args)
        )
        if subject_is_procfs:
            lines.append(node.lineno)
    return lines


def _enclosing_scope(tree: ast.AST, node: ast.AST) -> ast.AST:
    """Smallest function/module scope containing ``node``."""
    best = tree
    for candidate in ast.walk(tree):
        if not isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(candidate, "end_lineno", None) or candidate.lineno
        if candidate.lineno <= node.lineno <= end:
            if best is tree or candidate.lineno > best.lineno:
                best = candidate
    return best


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
        guards = _guard_lines(tree)
        for site in sites:
            scope = _enclosing_scope(tree, site)
            start = getattr(scope, "lineno", 0)
            label = f"{path.relative_to(REPO_ROOT)}:{site.lineno}"
            if any(start <= g < site.lineno for g in guards):
                guarded.append(label)
            else:
                unguarded.append(label)
    return guarded, unguarded


def test_every_procfs_scan_is_guarded_by_an_existence_check():
    _guarded, unguarded = _collect_sites()
    assert not unguarded, (
        "procfs pid scans with no existence check in the enclosing function — "
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
