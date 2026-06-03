"""Pin the ordinal-suffix helper used by the monthly-schedule label in tasks.js.

_scheduleLabel built the suffix with `d === 1 ? 'st' : d === 2 ? 'nd' : ...`,
which only handles single digits, so a monthly task on day 21/22/23/31 rendered
"Monthly on 21th"/"22th"/"23th"/"31th". The shared ordinalSuffix() fixes this.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "util" / "ordinal.js"
_HAS_NODE = shutil.which("node") is not None


def _suffixes(nums):
    arr = json.dumps(nums)
    js = f"""
    import {{ ordinalSuffix }} from '{_HELPER.as_posix()}';
    console.log(JSON.stringify({arr}.map(n => n + ordinalSuffix(n))));
    """
    proc = subprocess.run(["node", "--input-type=module"], input=js,
                          capture_output=True, text=True, cwd=str(_REPO), timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_ordinal_suffixes_for_days_of_month():
    assert _suffixes([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 31]) == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "31st",
    ]
