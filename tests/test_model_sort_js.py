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


def test_model_sort_helpers_ignore_non_arrays():
    values = _node_eval(
        """
        import { sortModelIds, sortModelObjects } from './static/js/modelSort.js';
        console.log(JSON.stringify({
          idsObject: sortModelIds({bad: true}),
          idsString: sortModelIds('llama'),
          objectsNull: sortModelObjects(null),
          objectsObject: sortModelObjects({bad: true})
        }));
        """
    )

    assert values == {
        "idsObject": [],
        "idsString": [],
        "objectsNull": [],
        "objectsObject": [],
    }


def test_model_sort_helpers_keep_valid_arrays():
    values = _node_eval(
        """
        import { sortModelIds, sortModelObjects } from './static/js/modelSort.js';
        console.log(JSON.stringify({
          ids: sortModelIds(['zeta/10', 'alpha/2', 'alpha/11']),
          objects: sortModelObjects([{id: 'zeta/10'}, {id: 'alpha/2'}]).map(m => m.id)
        }));
        """
    )

    assert values == {
        "ids": ["alpha/2", "zeta/10", "alpha/11"],
        "objects": ["alpha/2", "zeta/10"],
    }
