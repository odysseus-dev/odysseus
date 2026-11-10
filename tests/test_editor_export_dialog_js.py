import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/export-dialog.js").as_uri()


def run_node(body: str):
    script = textwrap.dedent(
        f"""
        import {{
          encodeExportCanvas, normalizeExportSettings, prepareExportCanvas,
        }} from {json.dumps(MODULE)};
        function canvas(width, height, tag='canvas') {{
          const value = {{width,height,tag,calls:[],encoded:null}};
          const ctx = {{
            fillStyle:'', imageSmoothingEnabled:false, imageSmoothingQuality:'low',
            fillRect(...args) {{ value.calls.push(['fill',this.fillStyle,...args]); }},
            drawImage(...args) {{ value.calls.push(['draw',args[0]?.tag,...args.slice(1)]); }},
          }};
          value.getContext = () => ctx;
          value.toBlob = (callback,mime,quality) => {{
            value.encoded = {{mime,quality}};
            callback({{size:321,type:mime}});
          }};
          return value;
        }}
        const created = [];
        globalThis.document = {{createElement:() => {{ const c=canvas(0,0,`out-${{created.length+1}}`); created.push(c); return c; }}}};
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


def test_export_settings_are_bounded_and_jpeg_forces_matte():
    result = run_node(
        """
        const source = canvas(800,600,'source');
        const settings = normalizeExportSettings({
          format:'jpeg', width:0, height:99999, quality:2,
          transparency:true, matte:'#123456', filename:'  cover  ',
        }, source.width, source.height);
        const output = prepareExportCanvas(source, settings);
        console.log(JSON.stringify({settings,size:[output.width,output.height],calls:output.calls}));
        """
    )

    assert result["settings"] == {
        "format": "jpeg", "width": 1, "height": 16384, "quality": 1,
        "transparency": False, "matte": "#123456", "filename": "cover",
    }
    assert result["size"] == [1, 16384]
    assert result["calls"][0] == ["fill", "#123456", 0, 0, 1, 16384]
    assert result["calls"][1] == ["draw", "source", 0, 0, 1, 16384]


def test_png_preserves_transparency_and_webp_uses_quality():
    result = run_node(
        """
        const source = canvas(320,200,'source');
        const png = prepareExportCanvas(source,{format:'png',width:160,height:100,transparency:true});
        const encoded = await encodeExportCanvas(source,{format:'webp',width:320,height:200,quality:0.73});
        console.log(JSON.stringify({png:png.calls,encoded,meta:created.at(-1).encoded}));
        """
    )

    assert result["png"] == [["draw", "source", 0, 0, 160, 100]]
    assert result["encoded"]["extension"] == "webp"
    assert result["encoded"]["settings"]["quality"] == 0.73
    assert result["meta"] == {"mime": "image/webp", "quality": 0.73}


def test_export_dialog_is_wired_to_save_menu_and_offline_graph():
    editor = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
    topbar = (ROOT / "static/js/editor/build/topbar.js").read_text(encoding="utf-8")
    service_worker = (ROOT / "static/sw.js").read_text(encoding="utf-8")

    assert "_openExportDialog({" in editor
    assert "_encodeExportCanvas(source, settings)" in editor
    assert "Export image..." in topbar
    assert "/static/js/editor/export-dialog.js" in service_worker
