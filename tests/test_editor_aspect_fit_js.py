import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / 'static/js/editor/aspect-fit.js').as_uri()


def run_node(body):
    script = textwrap.dedent(f"""
        import * as A from {json.dumps(MODULE)};
        {body}
    """)
    result = subprocess.run(
        ['node', '--input-type=module', '-e', script],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_aspect_fit_preserves_subject_ratio_within_two_percent():
    # A 900x600 canvas with a subject (face) whose bbox has ratio 0.62, ask for
    # a 1080x1350 (4:5) Instagram post. The crop-to-aspect step then the uniform
    # scale must keep the subject bbox w/h ratio within 2%.
    out = run_node("""
        const docW=900, docH=600;
        const subj={x:330,y:120,w:260,h:420};          // ratio 0.619
        const crop=A.aspectFitCropRect({width:docW,height:docH,focusX:subj.x+subj.w/2,focusY:subj.y+subj.h/2,targetAspect:1080/1350});
        // subject clipped to the crop window
        const ix=Math.max(subj.x,crop.x), iy=Math.max(subj.y,crop.y);
        const ix2=Math.min(subj.x+subj.w,crop.x+crop.width), iy2=Math.min(subj.y+subj.h,crop.y+crop.height);
        const sx=(ix2-ix)*(1080/crop.width), sy=(iy2-iy)*(1350/crop.height); // uniform scale
        console.log(JSON.stringify({ crop, subjRatio:subj.w/subj.h, afterRatio:sx/sy }));
    """)
    assert abs(out['afterRatio'] - out['subjRatio']) / out['subjRatio'] <= 0.02, out
    # The crop really is 4:5 and not the full 3:2 canvas.
    assert abs(out['crop']['width'] / out['crop']['height'] - 1080 / 1350) <= 0.01


def test_aspect_fit_no_crop_when_aspect_already_matches():
    out = run_node("""
        const r=A.aspectFitCropRect({width:1080,height:1350,targetAspect:1080/1350});
        console.log(JSON.stringify(r));
    """)
    assert out == {'x': 0, 'y': 0, 'width': 1080, 'height': 1350}


def test_aspect_fit_centres_on_focus_and_clamps_into_bounds():
    out = run_node("""
        // Tall subject near the left edge -> crop stays inside the document.
        const crop=A.aspectFitCropRect({width:900,height:600,focusX:60,focusY:300,targetAspect:4/5});
        const edge=A.aspectFitCropRect({width:900,height:600,focusX:450,focusY:300,targetAspect:4/5});
        console.log(JSON.stringify({crop,edge}));
    """)
    # focus at x=60 -> centred crop would start at -180 -> clamped to 0
    assert out['crop']['x'] == 0
    assert out['crop']['height'] == 600 and out['crop']['width'] == 480
    # centred focus -> starts at 210
    assert out['edge']['x'] == 210
