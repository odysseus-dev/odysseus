"""Workspace URL deep-link helpers.

The full workspace module imports the draggable modal/UI stack, so the pure URL
parser lives in a small module that can be exercised directly through Node.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _node_eval(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_workspace_url_param_decodes_path_and_preserves_other_state():
    values = _node_eval(
        textwrap.dedent(
            """
            const { parseWorkspaceUrl } = await import('./static/js/workspaceUrl.js');
            const parsed = parseWorkspaceUrl({
              pathname: '/',
              search: '?workspace=%2FUsers%2Fandrewmagu%2Fsrc%2F_main%2Fbaker-street&mode=agent',
              hash: '#session-1',
            });
            console.log(JSON.stringify(parsed));
            """
        )
    )

    assert values == {
        "found": True,
        "path": "/Users/andrewmagu/src/_main/baker-street",
        "cleanUrl": "/?mode=agent#session-1",
    }


def test_workspace_url_param_is_wired_through_server_vetting():
    source = (ROOT / "static" / "js" / "workspace.js").read_text(encoding="utf-8")

    assert "import { parseWorkspaceUrl } from './workspaceUrl.js';" in source
    assert "const result = await vetAndSetWorkspace(parsed.path);" in source
    assert "Storage.set(KEYS.WORKSPACE, parsed.path)" not in source
