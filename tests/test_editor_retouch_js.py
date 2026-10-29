import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / 'static/js/editor/retouch.js').as_uri()


def run_node(body):
    script = textwrap.dedent(f"""
        import * as R from {json.dumps(MODULE)};
        {body}
    """)
    result = subprocess.run(
        ['node', '--input-type=module', '-e', script],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_auto_tone_lifts_dark_low_contrast_and_stretches():
    out = run_node("""
        const w=40,h=40; const d=new Uint8ClampedArray(w*h*4);
        for(let i=0;i<d.length;i+=4){ const v=40+ (i/4)%20; d[i]=v; d[i+1]=v+2; d[i+2]=v+6; d[i+3]=255; }
        const p=R.analyzeAutoTone(d,w,h);
        const o=R.applyAutoTone(d,w,h,p);
        let before=0,after=0; for(let i=0;i<d.length;i+=4){before+=d[i];after+=o[i];}
        console.log(JSON.stringify({ brightness:p.brightness, contrast:p.contrast, gamma:p.gamma, meanBefore:before/(w*h), meanAfter:after/(w*h) }));
    """)
    assert (out['brightness'] and out['brightness'] > 0) or (out.get('gamma') is not None and out['gamma'] < 1)
    assert out['meanAfter'] > out['meanBefore'] + 20


def test_skin_mask_detects_skin_and_excludes_blue():
    out = run_node("""
        const w=4,h=1; const d=new Uint8ClampedArray(w*h*4);
        const px=(i,r,g,b)=>{d[i]=r;d[i+1]=g;d[i+2]=b;d[i+3]=255;};
        px(0,224,180,150); // skin
        px(4,30,60,200);   // blue
        px(8,60,130,60);   // green (not skin)
        px(12,200,170,140); // skin
        const m=R.skinMaskFromData(d,w,h,null);
        console.log(JSON.stringify({ a0:m[3], a1:m[7], a2:m[11], a3:m[15] }));
    """)
    assert out['a0'] == 255 and out['a3'] == 255
    assert out['a1'] == 0 and out['a2'] == 0


def test_frequency_separation_smooths_low_keeps_marker_and_teeth_reduces_yellow():
    out = run_node("""
        const w=9,h=9; const d=new Uint8ClampedArray(w*h*4);
        for(let i=0;i<d.length;i+=4){ const x=(i/4)%w, y=Math.floor((i/4)/w);
          // a bright blotch in the middle amid a mid-grey field
          const blot = (x>=3&&x<=5&&y>=3&&y<=5)? 40:0;
          d[i]=150+blot; d[i+1]=150+blot; d[i+2]=150+blot; d[i+3]=255; }
        const mask=new Uint8ClampedArray(w*h*4); for(let i=0;i<mask.length;i+=4){mask[i+3]=255;mask[i]=mask[i+1]=mask[i+2]=255;}
        const s=R.frequencySeparationSmooth(d,w,h,mask,{lowRadius:1,smoothRadius:2,opacity:1});
        // blotch centre pixel should be pulled down toward the field
        const ci=(4*w+4)*4;
        const teeth=R.teethWhitening(d,w,h,mask,{desat:0.8,bright:20});
        // teeth on a yellow field
        const yd=new Uint8ClampedArray([210,200,120,255]); const ym=new Uint8ClampedArray([255,255,255,255]);
        const tw=R.teethWhitening(yd,1,1,ym,{desat:0.8,bright:20});
        const dlt=R.meanAbsDelta(d,s,w,h);
        console.log(JSON.stringify({ smoothed:s[ci], orig:d[ci], delta:dlt, teethB:tw[2], teethR:tw[0] }));
    """)
    assert out['smoothed'] < out['orig']
    assert out['delta'] > 0
    assert out['teethB'] > 120  # blue channel raised -> yellow reduced


def test_frequency_separation_default_is_gentle_and_never_brightens():
    out = run_node("""
        const w=9,h=9; const d=new Uint8ClampedArray(w*h*4);
        for(let i=0;i<d.length;i+=4){ const x=(i/4)%w, y=Math.floor((i/4)/w);
          const blot = (x>=3&&x<=5&&y>=3&&y<=5)? 4:0;   // slightly bright blotch
          const dark = (x<=1&&y<=1)? -60:0;             // a dark pore region
          d[i]=150+blot+dark; d[i+1]=150+blot+dark; d[i+2]=150+blot+dark; d[i+3]=255; }
        const mask=new Uint8ClampedArray(w*h*4); for(let i=0;i<mask.length;i+=4){mask[i+3]=255;}
        const s=R.frequencySeparationSmooth(d,w,h,mask,{}); // DEFAULTS
        let anyUp=false, maxUp=0;
        for(let i=0;i<d.length;i+=4){ if(s[i]>d[i]){ anyUp=true; if(s[i]-d[i]>maxUp)maxUp=s[i]-d[i]; } }
        const ci=(4*w+4)*4;
        console.log(JSON.stringify({ anyUp, maxUp, centre:s[ci], orig:d[ci] }));
    """)
    # Gentle skin smoothing must NEVER lighten the face (the "pale/airbrushed" bug).
    assert out['anyUp'] is False
    assert out['centre'] <= out['orig']


def test_mean_abs_delta_identity_is_zero():
    out = run_node("""
        const w=5,h=5; const d=new Uint8ClampedArray(w*h*4).fill(120);
        for(let i=3;i<d.length;i+=4)d[i]=255;
        const e=new Uint8ClampedArray(d);
        console.log(JSON.stringify({ same:R.meanAbsDelta(d,e,w,h), diff:R.meanAbsDelta(d,new Uint8ClampedArray(d).map((v,i)=>i%4===3?v:v),w,h) }));
    """)
    assert out['same'] == 0


def test_guided_clarity_is_edge_aware_and_halo_free():
    out = run_node("""
        const w=64,h=64; const d=new Uint8ClampedArray(w*h*4);
        for(let i=0;i<d.length;i+=4){ const x=(i/4)%w,y=Math.floor((i/4)/w);
          let v=120+Math.round(10*Math.sin(x*1.3)*Math.cos(y*1.1)); if(x>=32)v+=100;
          d[i]=v;d[i+1]=v;d[i+2]=v;d[i+3]=255; }
        const src=new Uint8ClampedArray(d);
        const o=R.guidedClarity(d,w,h,{amount:80,radius:12,clamp:12});
        const at=(x,y)=>{const i=(y*w+x)*4; return o[i];};
        let maxFlat=0;
        for(let y=8;y<56;y++){ for(let x=6;x<26;x++){ const i=(y*w+x)*4; maxFlat=Math.max(maxFlat,Math.abs(o[i]-src[i])); } }
        let maxEdge=0;
        for(let y=8;y<56;y++){ maxEdge=Math.max(maxEdge, Math.abs(at(31,y)-src[(y*w+31)*4]), Math.abs(at(32,y)-src[(y*w+32)*4])); }
        let maxAny=0; for(let i=0;i<src.length;i+=4) maxAny=Math.max(maxAny,Math.abs(o[i]-src[i]));
        console.log(JSON.stringify({ maxFlat, maxEdge, maxAny }));
    """)
    assert out['maxFlat'] > 2          # clarity actually enhances flat/texture regions
    assert out['maxEdge'] <= 2         # no overshoot/halo at a strong edge
    assert out['maxAny'] <= 12         # clamped
