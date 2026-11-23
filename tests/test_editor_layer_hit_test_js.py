import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/layer-hit-test.js").as_uri()


def run_node(body: str):
    script = textwrap.dedent(
        f"""
        import {{ pickTopmostLayerAt, AUTO_SELECT_ALPHA_THRESHOLD }} from {json.dumps(MODULE)};
        {body}
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_pick_topmost_opaque_layer_skips_transparent_and_special_layers():
    result = run_node(
        """
        const mk = (id, extra = {}) => ({id, visible: true, canvas: {width: 4, height: 4}, ...extra});
        // Special layer kinds sit ABOVE an opaque raster so the walk must skip them.
        const layers = [
          mk('opaque'),
          mk('transparent'),
          mk('hidden', {visible: false}),
          mk('adjustment', {kind: 'adjustment'}),
        ];
        const alpha = {opaque: 255, transparent: 0};
        const over = pickTopmostLayerAt({
          layers,
          point: {x: 1, y: 1},
          sampleAlpha: (layer, x, y) => alpha[layer.id] ?? null,
        });
        // A genuinely topmost opaque layer still wins over a lower opaque one.
        const stacked = pickTopmostLayerAt({
          layers: [mk('under'), mk('over')],
          point: {x: 1, y: 1},
          sampleAlpha: () => 255,
        });
        console.log(JSON.stringify({over: over && over.id, stacked: stacked && stacked.id, threshold: AUTO_SELECT_ALPHA_THRESHOLD}));
        """
    )
    assert result == {"over": "opaque", "stacked": "over", "threshold": 8}


def test_pick_respects_offsets_bounds_locks_and_hidden_groups():
    result = run_node(
        """
        const mk = (id) => ({id, visible: true, canvas: {width: 10, height: 10}});
        const layers = [mk('offsetHit'), mk('far'), mk('locked'), mk('inHiddenGroup')];
        const offsets = {offsetHit: {x: 5, y: 5}, far: {x: 0, y: 0}, locked: {x: 0, y: 0}, inHiddenGroup: {x: 0, y: 0}};
        const picked = pickTopmostLayerAt({
          layers,
          point: {x: 6, y: 6},
          isLocked: layer => layer.id === 'locked',
          isHidden: layer => layer.id === 'inHiddenGroup',
          getOffset: layer => offsets[layer.id],
          // 'far' has no rendered pixel under this point.
          sampleAlpha: (layer, x, y) => (layer.id === 'far' ? null : 255),
        });
        console.log(JSON.stringify({picked: picked && picked.id}));
        """
    )
    assert result == {"picked": "offsetHit"}


def test_pick_returns_null_when_nothing_is_opaque_under_pointer():
    result = run_node(
        """
        const layers = [{id: 'only', visible: true, canvas: {width: 4, height: 4}}];
        const picked = pickTopmostLayerAt({
          layers,
          point: {x: 100, y: 100},
          sampleAlpha: () => 255,
        });
        console.log(JSON.stringify({picked: picked && picked.id}));
        """
    )
    assert result == {"picked": None}
