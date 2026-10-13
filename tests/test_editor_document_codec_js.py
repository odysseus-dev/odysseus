import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/document-codec.js").as_uri()


def run_node(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_document_codec_preserves_advanced_layer_state():
    source = textwrap.dedent(
        f"""
        import {{ serializeEditorDocument }} from {json.dumps(MODULE)};
        const canvas = (name, w, h) => ({{ width:w, height:h, toDataURL:() => `data:image/png;${{name}}` }});
        const mask = {{ id:'mask-8', name:'Subject', visible:false, mode:'layer', space:'layer', linked:false, offset:{{x:7,y:-3}}, canvas:canvas('mask', 40, 30) }};
        const layer = {{
          id:'layer-7', name:'Portrait', visible:true, opacity:0.7, locked:true,
          locks:{{pixels:true,transparency:false,position:true}}, clipped:true,
          isBase:true, blendMode:'multiply', kind:'text',
          text:{{content:'Caption',fontSize:32,transform:{{scaleX:1.2,scaleY:1,rotation:4}}}},
          canvas:canvas('pixels', 40, 30),
          adjustments:{{ brightness:1.2 }},
          adjLayers:[{{ id:'adj-1', type:'levels', visible:true, opacity:0.8, params:{{ gamma:1.1 }} }}],
          effects:[{{ id:'effect-1', type:'color-overlay', params:{{color:'#336699',opacity:0.4}}, mask:{{
            id:'effect-mask-1', name:'Selected area', visible:true, canvas:canvas('effect-mask',40,30)
          }} }}],
          activeMaskId:'mask-8', masks:[mask],
        }};
        const state = {{
          imageId:'photo-1', imgWidth:100, imgHeight:80, activeLayerId:'layer-7', nextLayerId:9,
          rulersVisible:false, gridVisible:true, gridSize:24, snapEnabled:true, snapToGrid:true,
          guides:{{vertical:[70,12],horizontal:[9]}},
          layerGroups:[{{id:'group-10',name:'Hero',layerIds:['layer-7'],visible:true,opacity:0.65,blendMode:'screen',locked:true,collapsed:true}}],
          layers:[layer], layerOffsets:new Map([['layer-7', {{x:4,y:5}}]]),
        }};
        console.log(JSON.stringify(serializeEditorDocument(state)));
        """
    )
    doc = run_node(source)
    layer = doc["layers"][0]

    assert doc["v"] == 15
    assert doc["view"] == {
        "rulersVisible": False,
        "gridVisible": True,
        "gridSize": 24,
        "snapEnabled": True,
        "snapToGrid": True,
        "guides": {"vertical": [12, 70], "horizontal": [9]},
    }
    assert layer["blendMode"] == "multiply"
    assert layer["clipped"] is True
    assert layer["locks"] == {"pixels": True, "transparency": False, "position": True}
    assert layer["kind"] == "text"
    assert layer["text"]["content"] == "Caption"
    assert layer["adjustments"] == {"brightness": 1.2}
    assert layer["adjLayers"][0]["type"] == "levels"
    assert layer["effects"][0]["params"]["color"] == "#336699"
    assert layer["effects"][0]["mask"] == {
        "id": "effect-mask-1", "name": "Selected area", "visible": True,
        "canvasW": 40, "canvasH": 30, "dataUrl": "data:image/png;effect-mask",
    }
    assert layer["activeMaskId"] == "mask-8"
    assert layer["masks"][0] == {
        "id": "mask-8",
        "name": "Subject",
        "visible": False,
        "mode": "layer",
        "space": "layer",
        "linked": False,
        "offset": {"x": 7, "y": -3},
        "density": 1,
        "feather": 0,
        "canvasW": 40,
        "canvasH": 30,
        "dataUrl": "data:image/png;mask",
    }
    assert doc["groups"] == [{
        "id": "group-10",
        "name": "Hero",
        "layerIds": ["layer-7"],
        "parentId": None,
        "visible": True,
        "opacity": 0.65,
        "blendMode": "screen",
        "locked": True,
        "collapsed": True,
        "activeMaskId": None,
        "masks": [],
    }]


def test_document_codec_recovers_next_id_from_string_ids():
    source = textwrap.dedent(
        f"""
        import {{ nextLayerIdFromDocument }} from {json.dumps(MODULE)};
        const value = nextLayerIdFromDocument({{
          groups:[{{id:'group-27'}}],
          layers:[{{id:'layer-12', masks:[{{id:'mask-19'}}]}}, {{id:'custom'}}]
        }});
        console.log(JSON.stringify(value));
        """
    )
    assert run_node(source) == 28


def test_document_codec_sanitizes_precision_view_state():
    source = textwrap.dedent(
        f"""
        import {{ normalizeEditorView }} from {json.dumps(MODULE)};
        const value = normalizeEditorView({{
          rulersVisible:false, gridVisible:true, gridSize:99999,
          snapEnabled:true, snapToGrid:false,
          guides:{{vertical:[20,'10',20,-2,'bad'],horizontal:null}},
        }}, {{guides:{{vertical:[],horizontal:[7]}}}});
        console.log(JSON.stringify(value));
        """
    )
    assert run_node(source) == {
        "rulersVisible": False,
        "gridVisible": True,
        "gridSize": 1000,
        "snapEnabled": True,
        "snapToGrid": False,
        "guides": {"vertical": [10, 20], "horizontal": [7]},
    }


def test_legacy_document_migrates_to_current_version_without_losing_pixels():
    source = textwrap.dedent(
        f"""
        import {{ prepareEditorDocument }} from {json.dumps(MODULE)};
        const result = prepareEditorDocument({{
          imgWidth:40,imgHeight:30,activeLayerId:'legacy',
          layers:[{{id:'legacy',name:'Legacy',canvasW:40,canvasH:30,dataURL:'data:image/png;base64,AAAA'}}],
        }});
        console.log(JSON.stringify(result));
        """
    )
    result = run_node(source)
    assert result["document"]["v"] == 15
    assert result["document"]["layers"][0]["dataUrl"] == "data:image/png;base64,AAAA"
    assert result["document"]["layers"][0]["kind"] == "raster"
    assert result["document"]["layers"][0]["placed"] is None
    assert result["document"]["layers"][0]["locks"] == {
        "pixels": False, "transparency": False, "position": False,
    }
    assert result["document"]["view"]["rulersVisible"] is True
    assert result["migratedFrom"] == 1
    assert result["warnings"][0] == "Project upgraded from version 1 to 15."


def test_document_codec_round_trips_validated_saved_selections():
    source = textwrap.dedent(
        f"""
        import {{ prepareEditorDocument,serializeEditorDocument }} from {json.dumps(MODULE)};
        const pixels={{width:20,height:10,toDataURL:()=> 'data:image/png;base64,AAAA'}};
        const selection={{width:20,height:10,toDataURL:()=> 'data:image/png;base64,BBBB'}};
        const state={{imgWidth:20,imgHeight:10,activeLayerId:'a',nextLayerId:2,layerOffsets:new Map([['a',{{x:0,y:0}}]]),
          layers:[{{id:'a',name:'A',canvas:pixels,visible:true,opacity:1,masks:[]}}],layerGroups:[],
          savedSelections:[{{id:'selection-1',name:'Subject',canvas:selection}}]}};
        const serialized=serializeEditorDocument(state);
        const prepared=prepareEditorDocument(serialized);
        const invalid=prepareEditorDocument({{...serialized,savedSelections:[
          ...serialized.savedSelections,
          {{id:'bad',name:'Wrong size',canvasW:2,canvasH:2,dataUrl:'data:image/png;base64,CCCC'}},
        ]}});
        console.log(JSON.stringify({{serialized,prepared,invalid}}));
        """
    )
    result = run_node(source)
    assert result["serialized"]["v"] == 15
    assert result["serialized"]["savedSelections"] == [{
        "id": "selection-1", "name": "Subject", "canvasW": 20, "canvasH": 10,
        "dataUrl": "data:image/png;base64,BBBB",
    }]
    assert result["prepared"]["document"]["savedSelections"][0]["name"] == "Subject"
    assert len(result["invalid"]["document"]["savedSelections"]) == 1
    assert any("Wrong size was skipped" in warning for warning in result["invalid"]["warnings"])


def test_document_codec_round_trips_placed_source_and_recovers_bad_source_as_raster():
    source = textwrap.dedent(
        f"""
        import {{ prepareEditorDocument,serializeEditorDocument }} from {json.dumps(MODULE)};
        const preview={{width:40,height:20,toDataURL:()=> 'data:image/png;base64,PREVIEW'}};
        const sourceCanvas={{width:200,height:100,toDataURL:()=> 'data:image/png;base64,SOURCE'}};
        const state={{imgWidth:100,imgHeight:80,activeLayerId:'placed',nextLayerId:3,
          layerOffsets:new Map([['base',{{x:0,y:0}}],['placed',{{x:12,y:8}}]]),layerGroups:[],
          layers:[{{id:'base',name:'Base',kind:'raster',canvas:preview,visible:true,opacity:1,masks:[]}},
          {{id:'placed',name:'Placed',kind:'placed',canvas:preview,visible:true,opacity:.6,
            blendMode:'screen',clipped:true,masks:[],placed:{{sourceCanvas,sourceWidth:200,sourceHeight:100,
              sourceName:'photo.png',matrix:[.2,0,0,.2,12,8]}}}}]}};
        const serialized=serializeEditorDocument(state);
        const prepared=prepareEditorDocument(serialized);
        const broken=prepareEditorDocument({{...serialized,layers:[serialized.layers[0],{{...serialized.layers[1],
          placed:{{...serialized.layers[1].placed,sourceDataUrl:'broken'}}}}]}});
        console.log(JSON.stringify({{serialized,prepared,broken}}));
        """
    )
    result = run_node(source)
    placed = result["prepared"]["document"]["layers"][1]
    assert placed["kind"] == "placed"
    assert placed["opacity"] == 0.6
    assert placed["blendMode"] == "screen"
    assert placed["clipped"] is True
    assert placed["placed"] == {
        "sourceWidth": 200,
        "sourceHeight": 100,
        "sourceName": "photo.png",
        "sourceDataUrl": "data:image/png;base64,SOURCE",
        "matrix": [0.2, 0, 0, 0.2, 12, 8],
    }
    recovered = result["broken"]["document"]["layers"][1]
    assert recovered["kind"] == "raster"
    assert recovered["placed"] is None
    assert any("recovered from its raster preview" in warning for warning in result["broken"]["warnings"])


def test_document_codec_preserves_nested_groups_and_breaks_cycles():
    source = textwrap.dedent(
        f"""
        import {{ prepareEditorDocument }} from {json.dumps(MODULE)};
        const png='data:image/png;base64,AAAA';
        const layer=id=>({{id,name:id,canvasW:10,canvasH:10,dataUrl:png}});
        const nested=prepareEditorDocument({{
          v:8,imgWidth:10,imgHeight:10,layers:[layer('a'),layer('b')],groups:[
            {{id:'outer',name:'Outer',layerIds:[]}},
            {{id:'inner',name:'Inner',parentId:'outer',layerIds:['a','b']}},
          ],
        }});
        const cyclic=prepareEditorDocument({{
          v:8,imgWidth:10,imgHeight:10,layers:[layer('a')],groups:[
            {{id:'one',name:'One',parentId:'two',layerIds:['a']}},
            {{id:'two',name:'Two',parentId:'one',layerIds:[]}},
          ],
        }});
        console.log(JSON.stringify({{nested,cyclic}}));
        """
    )
    result = run_node(source)
    assert result["nested"]["document"]["groups"] == [
        {"id": "outer", "name": "Outer", "layerIds": [], "parentId": None,
         "visible": True, "opacity": 1, "blendMode": "source-over", "locked": False, "collapsed": False,
         "masks": [], "activeMaskId": None},
        {"id": "inner", "name": "Inner", "layerIds": ["a", "b"], "parentId": "outer",
         "visible": True, "opacity": 1, "blendMode": "source-over", "locked": False, "collapsed": False,
         "masks": [], "activeMaskId": None},
    ]
    cyclic_groups = result["cyclic"]["document"]["groups"]
    assert any(group["parentId"] is None for group in cyclic_groups)
    assert any("cyclic group relationship" in warning for warning in result["cyclic"]["warnings"])


def test_document_codec_round_trips_group_mask_pixels_and_active_state():
    source = textwrap.dedent(
        f"""
        import {{ prepareEditorDocument,serializeEditorDocument }} from {json.dumps(MODULE)};
        const canvas={{width:20,height:10,toDataURL:()=> 'data:image/png;base64,AAAA'}};
        const state={{imgWidth:20,imgHeight:10,activeLayerId:'a',nextLayerId:12,layerOffsets:new Map([['a',{{x:0,y:0}}]]),
          layers:[{{id:'a',name:'A',canvas,visible:true,opacity:1,masks:[]}}],
          layerGroups:[{{id:'g',name:'Masked',layerIds:['a'],masks:[{{id:'mask-11',name:'Group Mask',visible:false,canvas}}],activeMaskId:'mask-11'}}]}};
        const serialized=serializeEditorDocument(state);
        const prepared=prepareEditorDocument(serialized);
        console.log(JSON.stringify({{serialized,prepared}}));
        """
    )
    result = run_node(source)
    saved = result["serialized"]["groups"][0]
    assert saved["activeMaskId"] == "mask-11"
    assert saved["masks"][0]["dataUrl"] == "data:image/png;base64,AAAA"
    restored = result["prepared"]["document"]["groups"][0]
    assert restored["masks"][0]["mode"] == "group"
    assert restored["masks"][0]["visible"] is False
    assert restored["activeMaskId"] == "mask-11"


def test_document_releases_clipping_without_a_base_in_the_same_scope():
    source = textwrap.dedent(
        f"""
        import {{ prepareEditorDocument }} from {json.dumps(MODULE)};
        const png='data:image/png;base64,AAAA';
        const layer=(id,clipped=false)=>({{id,name:id,canvasW:10,canvasH:10,dataUrl:png,clipped}});
        const result=prepareEditorDocument({{
          v:7,imgWidth:10,imgHeight:10,layers:[layer('root'),layer('group-base',true),layer('group-clip',true)],
          groups:[{{id:'g',name:'Group',layerIds:['group-base','group-clip']}}],
        }});
        console.log(JSON.stringify(result));
        """
    )
    result = run_node(source)
    layers = result["document"]["layers"]
    assert [layer["clipped"] for layer in layers] == [False, False, True]
    assert any("group-base had no clipping base" in warning for warning in result["warnings"])


def test_document_recovery_skips_corrupt_layers_and_masks_with_warnings():
    source = textwrap.dedent(
        f"""
        import {{ prepareEditorDocument }} from {json.dumps(MODULE)};
        const png='data:image/png;base64,AAAA';
        const result = prepareEditorDocument({{
          v:5,imgWidth:100,imgHeight:80,activeLayerId:'broken',view:{{}},layers:[
            {{id:'good',name:'Good',canvasW:100,canvasH:80,dataUrl:png,offset:{{x:'12',y:'bad'}},masks:[
              {{id:'good',name:'Duplicate id',canvasW:100,canvasH:80,dataUrl:png}},
              {{id:'bad-mask',name:'Broken mask',canvasW:100,canvasH:80,dataUrl:'bad'}},
            ]}},
            {{id:'broken',name:'Broken pixels',canvasW:100,canvasH:80,dataUrl:'bad'}},
          ],
        }});
        console.log(JSON.stringify(result));
        """
    )
    result = run_node(source)
    doc = result["document"]
    assert len(doc["layers"]) == 1
    assert doc["layers"][0]["name"] == "Good"
    assert doc["layers"][0]["offset"] == {"x": 12, "y": 0}
    assert doc["layers"][0]["masks"][0]["id"].startswith("mask-recovered-")
    assert doc["activeLayerId"] == "good"
    assert any("Broken pixels was skipped" in item for item in result["warnings"])
    assert any("Broken mask" in item for item in result["warnings"])


def test_document_validation_rejects_future_oversized_and_empty_projects():
    source = textwrap.dedent(
        f"""
        import {{ prepareEditorDocument }} from {json.dumps(MODULE)};
        const png='data:image/png;base64,AAAA';
        const capture=value=>{{try{{prepareEditorDocument(value);return null;}}catch(error){{return {{code:error.code,message:error.message}};}}}};
        console.log(JSON.stringify({{
          future:capture({{v:99,imgWidth:1,imgHeight:1,layers:[]}}),
          pixels:capture({{v:5,imgWidth:20000,imgHeight:20000,layers:[]}}),
          empty:capture({{v:5,imgWidth:10,imgHeight:10,layers:[{{name:'bad',canvasW:10,canvasH:10,dataUrl:'bad'}}]}}),
          surfaces:capture({{v:5,imgWidth:1,imgHeight:1,layers:[0,1,2,3].map(i=>({{id:`l${{i}}`,canvasW:10000,canvasH:10000,dataUrl:png}}))}}),
        }}));
        """
    )
    result = run_node(source)
    assert result["future"]["code"] == "future-version"
    assert result["pixels"]["code"] == "pixel-budget"
    assert result["empty"]["code"] == "no-layers"
    assert result["surfaces"]["code"] == "surface-budget"


def test_gallery_loader_uses_validated_document_boundary_and_file_cap():
    editor = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
    assert "_prepareEditorDocument(rawData)" in editor
    assert "file.size > _EDITOR_PROJECT_MAX_BYTES" in editor
    assert "No recoverable layers could be decoded" in editor
    assert "_showDocumentRestoreReport(report)" in editor
