# tests/test_db_project_model.py
from core.database import Base, DbProject


def test_db_project_columns_present():
    """The schema must match spec §5 exactly so the route layer can rely on it."""
    cols = {c.name for c in DbProject.__table__.columns}
    expected = {
        "id", "owner", "name", "icon", "description",
        "memory_mode", "snapshot_meta",
        "custom_prompt", "custom_instructions",
        "prompt_override_mode", "instructions_override_mode",
        "created_at", "updated_at", "deleted_at",
    }
    assert expected.issubset(cols), f"missing: {expected - cols}"


def test_db_project_tablename():
    assert DbProject.__tablename__ == "projects"
