import os

from src.builtin_mcp import builtin_python_env


def test_builtin_python_env_preserves_existing_pythonpath(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_DATA_DIR", raising=False)
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(["/app/venv/lib/python3.13/site-packages", "/app", "/extra"]),
    )

    env = builtin_python_env("/app")

    assert env == {
        "PYTHONPATH": os.pathsep.join(["/app", "/app/venv/lib/python3.13/site-packages", "/extra"])
    }


def test_builtin_python_env_uses_app_root_without_existing_pythonpath(monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("ODYSSEUS_DATA_DIR", raising=False)

    assert builtin_python_env("/srv/odysseus") == {"PYTHONPATH": "/srv/odysseus"}


def test_builtin_python_env_passes_the_data_folder_to_children(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", "/data/odysseus")

    assert builtin_python_env("/app")["ODYSSEUS_DATA_DIR"] == "/data/odysseus"
