"""Chat drags remain native unless they start at a screen edge unselected."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def gesture(**options):
    if not shutil.which('node'):
        pytest.skip('node is not installed')
    result = subprocess.run(
        ['node', 'tests/helpers/sidebar_edge_swipe.mjs', json.dumps(options)],
        cwd=_REPO, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('x,dx', [(195, 70), (195, -70), (33, 70), (357, -70)])
def test_middle_drags_are_never_claimed(x, dx):
    assert gesture(x=x, dx=dx) == {'opened': [], 'prevented': 0}


@pytest.mark.parametrize('options', [{'selected': True}, {'select_during_drag': True}])
def test_active_selection_at_start_or_during_drag_is_never_claimed(options):
    assert gesture(**options) == {'opened': [], 'prevented': 0}


@pytest.mark.parametrize('x,dx,side', [(10, 70, 'left'), (32, 70, 'left'), (358, -70, 'right'), (380, -70, 'right')])
def test_unselected_edge_swipes_still_open_the_correct_side(x, dx, side):
    assert gesture(x=x, dx=dx) == {'opened': [side], 'prevented': 1}


@pytest.mark.parametrize('options', [
    {'width': 1440}, {'sidebar_open': True}, {'compare': True},
    {'body_view': 'doc-view'}, {'body_view': 'notes-view'},
    {'modal_open': True}, {'excluded': True}, {'outside': True},
    {'chip_dragging': True}, {'touch_count': 2}, {'dy': 100},
])
def test_existing_desktop_modal_scroll_and_control_exclusions_remain_native(options):
    assert gesture(**options) == {'opened': [], 'prevented': 0}
