import json
import shutil
import subprocess
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


def test_censor_pref_falls_back_when_storage_throws():
    values = _node_eval(
        """
        globalThis.localStorage = {
          getItem() { throw new Error('blocked'); }
        };
        const { _prefEnabled } = await import('./static/js/censor.js');
        console.log(JSON.stringify({ enabled: _prefEnabled() }));
        """
    )

    assert values == {"enabled": False}


def test_censor_pref_reads_enabled_flag():
    values = _node_eval(
        """
        globalThis.localStorage = {
          getItem(key) { return key === 'odysseus-sensitive-blur' ? 'on' : null; }
        };
        const { _prefEnabled } = await import('./static/js/censor.js');
        console.log(JSON.stringify({ enabled: _prefEnabled() }));
        """
    )

    assert values == {"enabled": True}
