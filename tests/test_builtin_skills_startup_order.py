from pathlib import Path


def test_builtin_installation_precedes_optional_auth_file_read():
    source = (Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8")

    install_position = source.index("install_builtin_skills(skills_manager, ())")
    owner_repair_position = source.index('with open(auth_path, encoding="utf-8") as f:', install_position)

    assert install_position < owner_repair_position
    between = source[install_position:owner_repair_position]
    assert "Built-in skill installation skipped" in between
