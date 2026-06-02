from pathlib import Path


def test_start_macos_uses_repo_venv_for_install_and_runtime():
    script = Path("start-macos.sh").read_text(encoding="utf-8")

    assert script.startswith("#!/bin/bash\n")
    assert 'HOST="${ODYSSEUS_HOST:-127.0.0.1}"' in script
    assert 'PROBE_HOST="$HOST"' in script
    assert 'if (exec 3<>"/dev/tcp/$PROBE_HOST/$PORT") 2>/dev/null; then' in script
    assert 'brew_ensure() {' in script
    assert 'brew_ensure tmux tmux' in script
    assert 'brew_ensure llama-server llama.cpp' in script

    assert 'if [ ! -x "$VENV_DIR/bin/python" ]; then' in script
    assert 'VENV_PY="$VENV_DIR/bin/python"' in script
    assert '"$VENV_PY" -m pip install --quiet --upgrade pip' in script
    assert '"$VENV_PY" -m pip install -r requirements.txt' in script
    assert 'ODYSSEUS_SKIP_RUN_HINT=1 "$VENV_PY" setup.py' in script
    assert 'TAILSCALE_URL=""' in script
    assert '"$VENV_PY" -m uvicorn app:app --host "$HOST" --port "$PORT"' in script

    assert '"$PY" -m pip install --quiet --upgrade pip' not in script
    assert '"$PY" -m pip install -r requirements.txt' not in script
    assert '"$PY" -m uvicorn app:app --host "$HOST" --port "$PORT"' not in script
