"""MLX serve-engine launch commands (static/js/cookbookMlxEngines.js).

Two things the UI has to get right for a REMOTE Mac serve target:

* bind address — a server that binds loopback on the remote host is unreachable
  from the Odysseus host, so every engine must mirror the mlx-lm host rule;
* --model-dir — oMLX needs a real directory, and the download base dir is not
  one: for HF-cache layouts it holds `models--org--name/snapshots` indirections,
  and for a default-cache model there is no path at all. Guessing a directory
  there produces a server that starts and serves nothing.

The registry is a browser-free module precisely so these are executable under
node (cookbook.js itself pulls in DOM-bound modules and can't load).
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from routes.cookbook_helpers import _normalize_mlx_model_path, _validate_serve_cmd

_REPO = Path(__file__).resolve().parent.parent
_SERVE_JS = (_REPO / "static/js/cookbookServe.js").read_text(encoding="utf-8")
_HAS_NODE = shutil.which("node") is not None

requires_node = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def _run_node(body: str):
    script = textwrap.dedent(f"""
        const {{ MLX_ENGINES, buildMlxServeCmd, mlxModelDirFor }} =
          await import('./static/js/cookbookMlxEngines.js');
        {body}
    """)
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO, capture_output=True, timeout=30, text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"node failed:\n{res.stderr}")
    out = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not out:
        raise AssertionError("node produced no stdout")
    return json.loads(out[-1])


def _omlx(fields):
    return _run_node(
        f"console.log(JSON.stringify(buildMlxServeCmd({json.dumps(fields)}, 'org/model', 'python3')));"
    )


def _mlx_lm(fields):
    fields = {**fields, "mlx_engine": "mlx_lm"}
    return _run_node(
        f"console.log(JSON.stringify(buildMlxServeCmd({json.dumps(fields)}, '/m/Qwen3-4bit', 'python3')));"
    )


# ── bind address ──

@requires_node
def test_omlx_binds_all_interfaces_for_a_remote_target():
    cmd = _omlx({"mlx_engine": "omlx", "host": "mac.local", "_mlx_model_dir": "/models"})
    assert "--host 0.0.0.0" in cmd


@requires_node
def test_omlx_stays_on_loopback_for_a_local_target():
    cmd = _omlx({"mlx_engine": "omlx", "_mlx_model_dir": "/models"})
    assert "--host 127.0.0.1" in cmd


@requires_node
def test_both_engines_use_the_same_host_rule():
    remote = {"host": "mac.local", "_mlx_model_dir": "/models"}
    local = {"_mlx_model_dir": "/models"}
    assert "--host 0.0.0.0" in _mlx_lm(remote)
    assert "--host 127.0.0.1" in _mlx_lm(local)
    assert "--host 0.0.0.0" in _omlx({**remote, "mlx_engine": "omlx"})
    assert "--host 127.0.0.1" in _omlx({**local, "mlx_engine": "omlx"})


# ── --model-dir resolution ──

@requires_node
def test_model_dir_is_the_parent_of_the_resolved_model_path():
    # Same path mlx-lm's --model receives, one level up: oMLX scans a directory
    # of models and the request's `model` field picks one out of it.
    assert _run_node(
        "console.log(JSON.stringify(mlxModelDirFor('/srv/models/Qwen3-4B-4bit')));"
    ) == "/srv/models"


@pytest.mark.parametrize("serve_model", [
    "mlx-community/Qwen3-4B-4bit",   # default HF cache — a repo id, not a path
    "Qwen3-4B-4bit",
    "",
])
@requires_node
def test_unresolvable_model_yields_no_directory(serve_model):
    assert _run_node(f"console.log(JSON.stringify(mlxModelDirFor({json.dumps(serve_model)})));") == ""


@requires_node
def test_no_directory_means_no_flag_not_a_guessed_one():
    cmd = _omlx({"mlx_engine": "omlx"})
    assert "--model-dir" not in cmd
    assert "$HOME/models" not in cmd
    assert cmd.startswith("omlx serve ")


@requires_node
def test_home_relative_dir_stays_expandable():
    # Single-quoting the whole path would make the shell take `~` literally.
    cmd = _omlx({"mlx_engine": "omlx", "_mlx_model_dir": "~/models"})
    assert '--model-dir "$HOME"\'/models\'' in cmd


@requires_node
def test_generated_omlx_commands_pass_the_server_side_validator():
    for fields in (
        {"mlx_engine": "omlx", "_mlx_model_dir": "/srv/models", "host": "mac.local"},
        {"mlx_engine": "omlx", "_mlx_model_dir": "~/models"},
        {"mlx_engine": "omlx"},
        {"mlx_engine": "omlx", "_mlx_model_dir": "/srv/my models", "max_seqs": "8"},
    ):
        cmd = _omlx(fields)
        assert _validate_serve_cmd(cmd) == cmd
        # …and survive the server-side bundle-path rewrite untouched: a
        # directory is already the shape both engines want.
        assert _normalize_mlx_model_path(cmd) == cmd


# ── the serve panel feeds it the resolved path, not the download base dir ──

def test_serve_panel_resolves_the_dir_from_the_model_path():
    assert "f._mlx_model_dir = String(f.mlx_model_dir || '').trim() || mlxModelDirFor(serveModel);" in _SERVE_JS
    # The old bug: handing oMLX the scan/download base dir verbatim.
    assert "f._mlx_model_dir = m.path" not in _SERVE_JS


def test_serve_panel_exposes_a_model_dir_input():
    # When nothing resolves, the user needs somewhere to put one.
    assert 'data-field="mlx_model_dir"' in _SERVE_JS
