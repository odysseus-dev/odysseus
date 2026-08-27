"""Regression coverage for Cookbook dependency-install admission and refresh."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "static/js/cookbookDepTasks.js"
COOKBOOK = (ROOT / "static/js/cookbook.js").read_text(encoding="utf-8")
RUNNING = (ROOT / "static/js/cookbookRunning.js").read_text(encoding="utf-8")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _node_eval(source: str):
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=source,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def _section(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_canonical_and_legacy_tasks_match_the_catalog_pip_identity_and_target():
    result = _node_eval(
        textwrap.dedent(
            f"""
            import {{
              dependencyCandidateKeys, findActiveDepTask, sameDependencyTarget,
            }} from '{HELPER.as_uri()}';

            const target = {{ host: 'gpu-box', port: '2222', envPath: '/srv/venv' }};
            const cases = [
              {{
                name: 'new canonical', status: 'running', remoteHost: 'gpu-box', sshPort: '2222',
                payload: {{ _dep: true, _dep_pip_spec: 'sglang[all]', _dep_catalog_name: 'sglang', env_path: '/srv/venv' }},
                pip: 'sglang[all]', catalog: 'sglang',
              }},
              {{
                name: 'pip llama_cpp', status: 'running', remoteHost: 'gpu-box', sshPort: '2222',
                payload: {{ _dep: true, repo_id: 'llama_cpp', env_path: '/srv/venv' }},
                pip: 'llama-cpp-python[server]', catalog: 'llama_cpp',
              }},
              {{
                name: 'sglang setup', status: 'running', remoteHost: 'gpu-box', sshPort: '2222',
                payload: {{ _dep: true, repo_id: 'sglang setup', env_path: '/srv/venv' }},
                pip: 'sglang[all]', catalog: 'sglang',
              }},
              {{
                name: 'pip llama-cpp-python[CUDA]', status: 'running', remoteHost: 'gpu-box', sshPort: '2222',
                payload: {{ _dep: true, repo_id: 'pip llama-cpp-python[CUDA]', env_path: '/srv/venv' }},
                pip: 'llama-cpp-python[server]', catalog: 'llama_cpp',
              }},
              {{
                name: 'reinstall-vllm', status: 'running', remoteHost: 'gpu-box', sshPort: '2222',
                payload: {{ repo_id: 'pip-reinstall', _cmd: '/srv/venv/bin/python3 -m pip install --force-reinstall --no-deps vllm', env_path: '/srv/venv' }},
                pip: 'vllm', catalog: 'vllm',
              }},
            ];
            const matches = cases.map(item => !!findActiveDepTask(
              [item], dependencyCandidateKeys(item.pip, item.catalog, target)
            ));
            const wrongTarget = !!findActiveDepTask(
              [cases[0]], dependencyCandidateKeys('sglang[all]', 'sglang', {{ ...target, host: 'other-box' }})
            );
            console.log(JSON.stringify({{
              matches,
              wrongTarget,
              aliases: sameDependencyTarget(
                {{ host: 'localhost', port: '', envPath: '' }},
                {{ host: '', port: '', venv: '' }}
              ),
            }}));
            """
        )
    )
    assert result == {
        "matches": [True, True, True, True, True],
        "wrongTarget": False,
        "aliases": True,
    }


def test_every_visible_pip_install_surface_uses_shared_admission():
    normal = _section(COOKBOOK, "async function _installDep", "// Wire install buttons")
    gpu = _section(COOKBOOK, "list.querySelectorAll('.cookbook-dep-install-gpu-wheel')", "// Inline command")
    recipe = _section(COOKBOOK, "list.querySelectorAll('[data-dep-recipe-run]')", "async function _rebuildLlamaCpp")
    reinstall = _section(COOKBOOK, "// \"Reinstall\" buttons", "// Serve sort")

    for block in (normal, gpu, recipe, reinstall):
        assert "_launchDependencyTask({" in block
    assert ":not(.cookbook-dep-install-gpu-wheel)" in COOKBOOK

    admission = _section(COOKBOOK, "async function _launchDependencyTask", "async function _fetchDependencies")
    assert admission.index("_pendingDepInstalls.add(activeKey)") < admission.index("await fetch('/api/model/serve'")
    for field in ("_dep_key", "_dep_pip_spec", "_dep_catalog_name"):
        assert field in admission


def test_completion_refresh_is_target_aware_and_owned_by_status_transition():
    fetch_deps = _section(COOKBOOK, "async function _fetchDependencies", "const _depTarget")
    assert "const _dsel = document.getElementById('hwfit-deps-server')" in fetch_deps
    assert "sameDependencyTarget(selectedTarget, requestedTarget)" in fetch_deps
    assert fetch_deps.index("sameDependencyTarget") < fetch_deps.index("list.innerHTML = ''")

    update = _section(RUNNING, "function _updateTask", "function _refreshDepsAfterInstall")
    assert "wasActiveDependency" in update
    assert "!isActiveDepTask(task)" in update

    success = _section(RUNNING, "if (snapshot.includes('DOWNLOAD_OK')", "// Live status parsing")
    assert "_updateTask(task.sessionId, { status: 'done' })" in success
    assert "_refreshDepsAfterInstall(task)" not in success
