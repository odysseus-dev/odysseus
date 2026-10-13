from routes.session_routes import _context_info_agents_md_inventory


def test_context_info_agents_md_inventory_discovers_workspace_and_parent_files(tmp_path):
    root = tmp_path / "project"
    child = root / "package"
    child.mkdir(parents=True)
    root_agents = root / "AGENTS.md"
    child_agents = child / "AGENTS.md"
    root_agents.write_text("root instructions stay on disk", encoding="utf-8")
    child_agents.write_text("package instructions stay on disk", encoding="utf-8")

    files = _context_info_agents_md_inventory(str(child))

    assert files == [
        {"path": str(root_agents), "source": "workspace"},
        {"path": str(child_agents), "source": "workspace"},
    ]
    assert "root instructions" not in str(files)
    assert "package instructions" not in str(files)


def test_context_info_agents_md_inventory_rejects_missing_workspace():
    assert _context_info_agents_md_inventory("/does/not/exist") == []
