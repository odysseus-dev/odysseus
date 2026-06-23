# tests/test_project_paths.py
import os
from src.constants import DATA_DIR, PROJECTS_DIR
from services.project.paths import slugify_owner, project_data_dir


def test_projects_dir_under_data_dir():
    assert PROJECTS_DIR == os.path.join(DATA_DIR, "projects")


def test_slugify_owner_lowercases_and_strips_unsafe():
    assert slugify_owner("Alice Smith") == "alice_smith"
    assert slugify_owner("Bob!@#") == "bob"
    assert slugify_owner("  Carol-2  ") == "carol-2"
    # Collision-safe fallback for fully-stripped names.
    assert len(slugify_owner("!!!", fallback="anon")) == len("anon")
    # Slug never empty after stripping.
    assert slugify_owner("") == "owner"


def test_project_data_dir_layout():
    # /<DATA_DIR>/projects/<owner_slug>/<project_id>
    base = project_data_dir("alice_smith", "prj_abc123")
    assert base.endswith(os.path.join("projects", "alice_smith", "prj_abc123"))
