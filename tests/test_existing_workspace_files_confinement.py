from src.agent_loop import _existing_workspace_files


def test_existing_workspace_files_accepts_relative_file_inside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "inside.py"
    target.write_text("pass\n")

    assert _existing_workspace_files(
        ["inside.py"],
        str(workspace),
    ) == ["inside.py"]


def test_existing_workspace_files_accepts_absolute_file_inside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "inside.py"
    target.write_text("pass\n")

    assert _existing_workspace_files(
        [str(target)],
        str(workspace),
    ) == [str(target)]


def test_existing_workspace_files_rejects_absolute_file_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n")

    assert _existing_workspace_files(
        [str(outside)],
        str(workspace),
    ) == []


def test_existing_workspace_files_rejects_parent_traversal(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n")

    assert _existing_workspace_files(
        ["../outside.py"],
        str(workspace),
    ) == []


def test_existing_workspace_files_rejects_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n")

    escape = workspace / "escape.py"
    escape.symlink_to(outside)

    assert _existing_workspace_files(
        ["escape.py"],
        str(workspace),
    ) == []


def test_existing_workspace_files_supports_workspace_alias(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "inside.py"
    target.write_text("pass\n")

    assert _existing_workspace_files(
        ["/workspace/inside.py"],
        str(workspace),
    ) == ["/workspace/inside.py"]
