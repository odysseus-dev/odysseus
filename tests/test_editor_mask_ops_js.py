import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / 'static/js/editor/mask-ops.js').as_uri()


def run_node(body):
    script = textwrap.dedent(f"""
        import {{ alphaBounds, invertMaskData, featherMaskCanvas, bakeMaskIntoLayer }} from {json.dumps(MODULE)};
        {body}
    """)
    result = subprocess.run(
        ['node', '--input-type=module', '-e', script],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_alpha_bounds_of_non_transparent_pixels():
    out = run_node("""
        const w = 5, h = 4;
        const d = new Uint8ClampedArray(w * h * 4);
        const set = (x, y, a) => { d[(y * w + x) * 4 + 3] = a; };
        set(1, 1, 255); set(3, 2, 255); set(0, 0, 4); // 4 is below the 8 threshold
        const b = alphaBounds(d, w, h, 8);
        const empty = alphaBounds(new Uint8ClampedArray(w * h * 4), w, h, 8);
        console.log(JSON.stringify({ b, empty }));
    """)
    assert out['b'] == {'x': 1, 'y': 1, 'w': 3, 'h': 2}
    assert out['empty'] is None


def test_invert_mask_flips_alpha_to_white():
    out = run_node("""
        const d = new Uint8ClampedArray(4 * 4); // two pixels
        d[3] = 255; // pixel 0 selected
        // pixel 1 alpha 0
        const inv = invertMaskData(d);
        console.log(JSON.stringify({ p0: [inv[0], inv[1], inv[2], inv[3]], p1: [inv[4], inv[5], inv[6], inv[7]], src: [d[3], d[7]] }));
    """)
    assert out['p0'] == [255, 255, 255, 0]
    assert out['p1'] == [255, 255, 255, 255]
    assert out['src'] == [255, 0]


def test_feather_mask_uses_blur_filter_and_bake_multiplies_alpha():
    out = run_node("""
        const created = [];
        function mk(w, h) {
          const cv = { width: w, height: h, calls: [] };
          const ctx = { filter: 'none', drawImage(...a) { cv.calls.push(['drawImage', this.filter, a[0] && a[0].tag]); } };
          cv.getContext = () => ctx;
          created.push(cv);
          return cv;
        }
        const src = { width: 2, height: 2, tag: 'src' };
        const out = featherMaskCanvas(mk, src, 3);
        const noFeather = featherMaskCanvas(mk, src, 0);
        const lc = { globalCompositeOperation: 'source-over', calls: [], save() { this.calls.push('save'); }, restore() { this.calls.push('restore'); }, drawImage(...a) { this.calls.push(['drawImage', this.globalCompositeOperation, a[0] && a[0].tag]); } };
        bakeMaskIntoLayer(lc, { tag: 'mask' }, 0, 0);
        console.log(JSON.stringify({ out: out.calls, noFeather: noFeather.calls, lc: lc.calls }));
    """)
    assert out['out'][0] == ['drawImage', 'blur(3px)', 'src']
    assert out['noFeather'][0] == ['drawImage', 'none', 'src']
    assert out['lc'] == ['save', ['drawImage', 'destination-in', 'mask'], 'restore']


def test_mask_ops_module_is_precached_for_offline_use():
    sw = (ROOT / 'static/sw.js').read_text(encoding='utf-8')
    assert "'/static/js/editor/mask-ops.js'" in sw
