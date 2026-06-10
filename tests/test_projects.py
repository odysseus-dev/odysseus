"""Tests for the Projects feature.

These tests use source-code inspection and isolated unit logic — they avoid
importing core.database (which calls init_db() at module level and requires
a live SQLite path) to remain runnable without a real database file.

Test coverage:
 1. Project model columns are declared in database.py
 2. ProjectDocument model columns are declared in database.py
 3. ProjectMemory model columns are declared in database.py
 4. sessions.project_id column is in the Session model
 5. All four migration functions exist and are called in init_db()
 6. project_routes.py defines all required endpoints
 7. project_routes.py owner-scopes list/get/patch/delete operations
 8. build_chat_context injects project instructions (source check)
 9. app.py includes the project router
"""

import ast
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(rel_path: str) -> tuple[ast.Module, str]:
    src = Path(rel_path).read_text(encoding="utf-8")
    return ast.parse(src), src


def _fn_source(tree, fn_name: str) -> str:
    """Return unparsed source of a top-level function."""
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == fn_name
    )
    return ast.unparse(fn)


def _class_source(tree, class_name: str) -> str:
    cls = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == class_name
    )
    return ast.unparse(cls)


# ---------------------------------------------------------------------------
# Test 1: Project model columns declared in database.py
# ---------------------------------------------------------------------------

def test_project_model_columns_in_database():
    tree, src = _parse("core/database.py")
    cls_src = _class_source(tree, "Project")

    assert "__tablename__ = 'projects'" in cls_src or '"projects"' in cls_src
    assert "name" in cls_src
    assert "instructions" in cls_src
    assert "owner" in cls_src
    assert "archived" in cls_src
    assert "created_at" in cls_src
    assert "updated_at" in cls_src


# ---------------------------------------------------------------------------
# Test 2: ProjectDocument model columns declared in database.py
# ---------------------------------------------------------------------------

def test_project_document_model_in_database():
    tree, src = _parse("core/database.py")
    cls_src = _class_source(tree, "ProjectDocument")

    assert "project_documents" in cls_src
    assert "project_id" in cls_src
    assert "document_id" in cls_src
    assert "pinned_at" in cls_src


# ---------------------------------------------------------------------------
# Test 3: ProjectMemory model columns declared in database.py
# ---------------------------------------------------------------------------

def test_project_memory_model_in_database():
    tree, src = _parse("core/database.py")
    cls_src = _class_source(tree, "ProjectMemory")

    assert "project_memories" in cls_src
    assert "project_id" in cls_src
    assert "content" in cls_src
    assert "synthesized_at" in cls_src
    assert "session_count" in cls_src


# ---------------------------------------------------------------------------
# Test 4: sessions.project_id column is declared in Session model
# ---------------------------------------------------------------------------

def test_session_model_has_project_id():
    tree, src = _parse("core/database.py")
    cls_src = _class_source(tree, "Session")
    assert "project_id" in cls_src, "Session model must declare project_id column"


# ---------------------------------------------------------------------------
# Test 5: All four migration functions exist and are called from init_db()
# ---------------------------------------------------------------------------

def test_init_db_calls_all_four_project_migrations():
    tree, src = _parse("core/database.py")

    # Functions defined
    for fn_name in (
        "_migrate_add_projects_table",
        "_migrate_add_project_documents_table",
        "_migrate_add_project_memories_table",
        "_migrate_add_session_project_id",
    ):
        assert fn_name in src, f"Migration function {fn_name} must exist in database.py"

    # All called from init_db
    init_src = _fn_source(tree, "init_db")
    for fn_name in (
        "_migrate_add_projects_table()",
        "_migrate_add_project_documents_table()",
        "_migrate_add_project_memories_table()",
        "_migrate_add_session_project_id()",
    ):
        assert fn_name in init_src, f"{fn_name} must be called inside init_db()"


# ---------------------------------------------------------------------------
# Test 6: project_routes.py defines all required API endpoints
# ---------------------------------------------------------------------------

def test_project_routes_defines_all_endpoints():
    _, src = _parse("routes/project_routes.py")

    # Must define a setup function
    assert "def setup_project_routes" in src

    # CRUD endpoints
    assert '"/projects"' in src or "'/projects'" in src
    assert '"/projects/{project_id}"' in src or "'/projects/{project_id}'" in src

    # Sessions subresource
    assert "sessions/{session_id}" in src

    # Documents subresource
    assert "documents/{document_id}" in src

    # Synthesize endpoint
    assert "synthesize" in src

    # Memories endpoint
    assert "memories" in src


# ---------------------------------------------------------------------------
# Test 7: project_routes.py owner-scopes operations
# ---------------------------------------------------------------------------

def test_project_routes_owner_scopes_operations():
    _, src = _parse("routes/project_routes.py")

    # get_current_user is called to resolve the owner
    assert "get_current_user" in src, "Must use get_current_user for auth"

    # owner filter applied in queries
    assert "owner == owner" in src or ".filter(ProjectModel.owner == owner)" in src or \
           "ProjectModel.owner ==" in src, \
        "Must filter queries by owner"


# ---------------------------------------------------------------------------
# Test 8: build_chat_context injects project instructions
# ---------------------------------------------------------------------------

def test_build_chat_context_injects_project_instructions():
    _, src = _parse("routes/chat_helpers.py")

    # Project context injection block present
    assert "Project context injection" in src, \
        "chat_helpers must contain project context injection comment"

    # Checks project_id on session
    assert "project_id" in src, "chat_helpers must reference project_id"

    # Injects instructions as a system message
    assert "_proj.instructions" in src, "must reference project instructions"

    # Injects pinned documents
    assert "project pinned document" in src, "must inject pinned documents as context"


# ---------------------------------------------------------------------------
# Test 9: app.py includes the project router
# ---------------------------------------------------------------------------

def test_app_includes_project_router():
    _, src = _parse("app.py")

    assert "project_routes" in src, "app.py must import from project_routes"
    assert "setup_project_routes" in src, "app.py must call setup_project_routes"
    assert "include_router" in src, "app.py must call include_router"


# ---------------------------------------------------------------------------
# Test 10: GET /api/projects excludes archived projects
# ---------------------------------------------------------------------------

def test_list_projects_source_excludes_archived():
    _, src = _parse("routes/project_routes.py")
    assert "archived == False" in src, \
        "list_projects must filter out archived projects"


# ---------------------------------------------------------------------------
# Test 11: DELETE /api/projects/{id} soft-archives, does not hard-delete
# ---------------------------------------------------------------------------

def test_delete_sets_archived_not_hard_delete():
    tree, src = _parse("routes/project_routes.py")
    assert "archived = True" in src, "DELETE must set archived=True"
    delete_fn_src = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "delete_project":
            delete_fn_src = ast.unparse(node)
    assert "db.delete" not in delete_fn_src, \
        "delete_project must not hard-delete — use archived=True"


# ---------------------------------------------------------------------------
# Test 12: _get_project_or_404 enforces owner scoping
# ---------------------------------------------------------------------------

def test_get_project_or_404_filters_by_owner():
    _, src = _parse("routes/project_routes.py")
    assert "_get_project_or_404" in src, "_get_project_or_404 helper must exist"
    assert "ProjectModel.owner == owner" in src, \
        "_get_project_or_404 must filter by owner"
    assert "HTTPException(404" in src, \
        "_get_project_or_404 must raise 404 when project not found"


# ---------------------------------------------------------------------------
# Test 13: pin_document verifies document ownership before pinning
# ---------------------------------------------------------------------------

def test_pin_document_verifies_doc_owner():
    _, src = _parse("routes/project_routes.py")
    assert "DocModel.owner == owner" in src or "filter(DocModel.owner" in src, \
        "pin_document must filter documents by owner to prevent cross-user pinning"


# ---------------------------------------------------------------------------
# Test 14: CASCADE declared on all three project FKs
# ---------------------------------------------------------------------------

def test_cascade_on_project_fks():
    _, src = _parse("core/database.py")
    assert 'ForeignKey("projects.id", ondelete="CASCADE")' in src or \
           "ForeignKey('projects.id', ondelete='CASCADE')" in src, \
        "ProjectDocument/ProjectMemory project_id FK must have ondelete=CASCADE"
    cascade_count = src.count('ondelete="CASCADE"') + src.count("ondelete='CASCADE'")
    assert cascade_count >= 3, \
        "Need CASCADE on ProjectDocument.project_id, ProjectDocument.document_id, ProjectMemory.project_id"


# ---------------------------------------------------------------------------
# Test 15: projectModal.js is loaded in index.html
# ---------------------------------------------------------------------------

def test_project_modal_script_in_index_html():
    src = Path("static/index.html").read_text(encoding="utf-8")
    assert "projectModal.js" in src, \
        "static/index.html must include a <script> tag for projectModal.js"


# ---------------------------------------------------------------------------
# Test 16: synthesize endpoint handles zero sessions gracefully
# ---------------------------------------------------------------------------

def test_synthesize_handles_zero_sessions():
    _, src = _parse("routes/project_routes.py")
    assert "No sessions" in src or "no sessions" in src or \
           "not session_list" in src or "session_count" in src, \
        "synthesize must handle projects with no sessions"


# ---------------------------------------------------------------------------
# Test 17: sessions.js resets _activeProjectId on loadSessions
# ---------------------------------------------------------------------------

def test_sessions_js_resets_active_project_on_load():
    src = Path("static/js/sessions.js").read_text(encoding="utf-8")
    assert "_activeProjectId = null" in src, \
        "loadSessions must reset _activeProjectId to avoid stale filter across reloads"
