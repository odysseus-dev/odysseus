import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIXEL_MODULE = (ROOT / "static/js/editor/fx/pixel-pass.js").as_uri()
ADJUSTMENT_MODULE = (ROOT / "static/js/editor/adjustment-layer.js").as_uri()
ADJUSTMENT_WORKER = ROOT / "static/js/editor/adjustments-worker.js"


def run_node(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_curve_lut_is_identity_and_interpolates_control_points():
    source = textwrap.dedent(
        f"""
        import {{curveLut}} from {json.dumps(PIXEL_MODULE)};
        const identity=curveLut([[0,0],[255,255]]);
        const shaped=curveLut([[0,0],[128,220],[255,255]]);
        console.log(JSON.stringify({{
          identity:[identity[0],identity[64],identity[128],identity[255]],
          shaped:[shaped[0],shaped[64],shaped[128],shaped[192],shaped[255]],
        }}));
        """
    )
    result = run_node(source)
    assert result["identity"] == [0, 64, 128, 255]
    assert result["shaped"] == [0, 110, 220, 238, 255]


def test_adjustment_normalization_deep_merges_channel_and_selective_defaults():
    source = textwrap.dedent(
        f"""
        import {{normalizeAdjustmentData}} from {json.dumps(ADJUSTMENT_MODULE)};
        const levels=normalizeAdjustmentData({{type:'levels',params:{{channels:{{red:{{outWhite:90}}}}}}}});
        const selective=normalizeAdjustmentData({{type:'selective-color',params:{{range:'blues',ranges:{{blues:{{cyan:30}}}}}}}});
        console.log(JSON.stringify({{levels,selective}}));
        """
    )
    result = run_node(source)
    assert result["levels"]["params"]["channels"]["red"] == {
        "inBlack": 0, "inWhite": 255, "gamma": 1,
        "outBlack": 0, "outWhite": 90,
    }
    assert result["selective"]["params"]["ranges"]["blues"] == {
        "cyan": 30, "magenta": 0, "yellow": 0, "black": 0,
    }
    assert "reds" in result["selective"]["params"]["ranges"]


def test_color_adjustments_produce_deterministic_pixels_and_preserve_alpha():
    source = textwrap.dedent(
        f"""
        import {{applyAdjustment}} from {json.dumps(PIXEL_MODULE)};
        class FakeContext {{
          constructor(canvas) {{ this.canvas=canvas; this.filter='none'; }}
          drawImage(source) {{ this.canvas.pixels=new Uint8ClampedArray(source.pixels); }}
          getImageData() {{ return {{data:new Uint8ClampedArray(this.canvas.pixels)}}; }}
          putImageData(image) {{ this.canvas.pixels=new Uint8ClampedArray(image.data); }}
        }}
        class FakeCanvas {{
          constructor(pixels=[0,0,0,0]) {{ this.width=1; this.height=1; this.pixels=new Uint8ClampedArray(pixels); this.ctx=new FakeContext(this); }}
          getContext() {{ return this.ctx; }}
        }}
        globalThis.document={{createElement:()=>new FakeCanvas()}};
        const source=new FakeCanvas([160,80,40,173]);
        const specs=[
          {{type:'exposure',params:{{exposure:1,offset:0,gamma:1}}}},
          {{type:'white-balance',params:{{temperature:80,tint:-20}}}},
          {{type:'hue-saturation',params:{{hue:90,saturation:1.4,lightness:10}}}},
          {{type:'vibrance',params:{{vibrance:80}}}},
          {{type:'shadows-highlights',params:{{shadows:65,highlights:-40}}}},
          {{type:'selective-color',params:{{ranges:{{reds:{{cyan:40,magenta:0,yellow:0,black:0}}}}}}}},
          {{type:'gradient-map',params:{{shadows:'#102040',highlights:'#f0d090',midpoint:50,reverse:false}}}},
        ];
        console.log(JSON.stringify(specs.map(spec=>Array.from(applyAdjustment(source,spec).pixels))));
        """
    )
    result = run_node(source)
    assert len(result) == 7
    assert all(pixel[3] == 173 for pixel in result)
    assert all(pixel[:3] != [160, 80, 40] for pixel in result)
    assert len({tuple(pixel) for pixel in result}) == 7


def test_adjustment_worker_reuses_shared_pixel_pass_and_returns_transferable_bitmap():
    worker = ADJUSTMENT_WORKER.read_text()
    adjustment = (ROOT / "static/js/editor/adjustment-layer.js").read_text()
    assert "await import('./fx/pixel-pass.js')" in worker
    assert "self.document = { createElement:" in worker
    assert "transferToImageBitmap()" in worker
    assert "drawAdjustmentLayerAsync" in adjustment
    assert "new Worker(new URL('./adjustments-worker.js', import.meta.url)" in adjustment
