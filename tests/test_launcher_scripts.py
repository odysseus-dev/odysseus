from pathlib import Path


def test_start_macos_uses_repo_venv_for_install_and_runtime():
    script = Path("start-macos.sh").read_text(encoding="utf-8")

    assert 'VENV_PY="$VENV_DIR/bin/python"' in script
    assert '"$VENV_PY" -m pip install --quiet --upgrade pip' in script
    assert '"$VENV_PY" -m pip install -r requirements.txt' in script
    assert 'ODYSSEUS_SKIP_RUN_HINT=1 "$VENV_PY" setup.py' in script
    assert '"$VENV_PY" -m uvicorn app:app --host 127.0.0.1 --port "$PORT"' in script

    assert '"$PY" -m pip install --quiet --upgrade pip' not in script
    assert '"$PY" -m pip install -r requirements.txt' not in script
    assert '"$PY" -m uvicorn app:app --host 127.0.0.1 --port "$PORT"' not in script
