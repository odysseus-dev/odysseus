"""Regression: remove_directory_from_rag must confine its path to PERSONAL_DIR.

DELETE /api/personal/remove_directory took a raw ``directory`` query parameter
and passed it straight to ``personal_docs_manager.remove_directory`` /
``rag.remove_directory`` with no containment check — unlike add_directory_to_rag,
which resolves the path via ``_resolve_allowed_personal_dir`` first. This pins
the parity fix.

``_resolve_allowed_personal_dir`` is a closure inside ``setup_personal_routes``,
so this is a source-level test, matching test_personal_dir_symlink_escape.py.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "routes" / "personal_routes.py"


def _function_source(src_text: str, name: str) -> str:
    tree = ast.parse(src_text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src_text, node)
    raise AssertionError(f"{name} not found in {SRC}")


def test_remove_directory_confines_path():
    body = _function_source(SRC.read_text(), "remove_directory_from_rag")
    assert "_resolve_allowed_personal_dir(" in body, (
        "remove_directory_from_rag must call _resolve_allowed_personal_dir to "
        "confine the user-supplied directory to PERSONAL_DIR (parity with "
        "add_directory_to_rag)"
    )


def test_confinement_runs_before_removal_sinks():
    """The confinement must happen before the path reaches either removal sink."""
    body = _function_source(SRC.read_text(), "remove_directory_from_rag")
    resolve_idx = body.index("_resolve_allowed_personal_dir(")
    for sink in ("personal_docs_manager.remove_directory(", "rag.remove_directory("):
        assert sink in body, f"expected sink {sink} in remove_directory_from_rag"
        assert body.index(sink) > resolve_idx, (
            f"{sink} runs before _resolve_allowed_personal_dir — path not confined"
        )
