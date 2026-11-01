import os
from pathlib import Path

import pytest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def page():
    if sync_playwright is None:
        pytest.fail("playwright is required for editor browser tests. Install requirements-dev.txt")
    with sync_playwright() as playwright:
        launch_kwargs = {"headless": True, "args": ["--disable-dev-shm-usage", "--no-sandbox"]}
        if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"):
            launch_kwargs["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"]
        browser = playwright.chromium.launch(**launch_kwargs)
        page = browser.new_page(viewport={"width": 1000, "height": 700})

        def serve(route):
            url = route.request.url
            if '/api/' in url:
                route.fulfill(status=404, body='{}', content_type='application/json')
                return
            relative = url.split('http://editor.test/', 1)[1].split('?', 1)[0]
            if not relative:
                route.fulfill(body='<html><body></body></html>', content_type='text/html')
            elif (ROOT / relative).is_file():
                route.fulfill(path=str(ROOT / relative))
            else:
                route.fulfill(status=404, body='Not found')

        page.route('http://editor.test/**', serve)
        page.goto('http://editor.test/')
        yield page
        browser.close()


def test_remove_background_sends_only_active_layer_and_preserves_others(page):
    result = page.evaluate("""async () => {
      const {state} = await import('/static/js/editor/state.js');
      const {createApplyImageTool} = await import('/static/js/editor/ai-tool-runner.js');
      function layer(id, color) {
        const canvas = document.createElement('canvas'); canvas.width = canvas.height = 4;
        const ctx = canvas.getContext('2d'); ctx.fillStyle = color; ctx.fillRect(0,0,4,4);
        return {id, canvas, ctx, visible:true, locks:{}};
      }
      const red = layer('red','red'), blue = layer('blue','blue');
      red.kind = 'placed'; red.placed = {sourceName:'original'};
      state.layers = [red, blue]; state.activeLayerId = red.id; state.editorOpen = true;
      const beforeBlue = blue.canvas.toDataURL(); const expected = red.canvas.toDataURL().split(',')[1];
      let sent, snapshots = 0;
      const clear = document.createElement('canvas'); clear.width = clear.height = 4;
      window.fetch = async (url, opts) => {
        sent = JSON.parse(opts.body);
        state.activeLayerId = blue.id; // Changing selection during the request must not change its target.
        return {ok:true, json:async () => ({image:clear.toDataURL()})};
      };
      const run = createApplyImageTool({
        flatten: () => {throw Error('Must not flatten');}, saveState: () => snapshots++,
        createLayer: () => {throw Error('Must not create a merged layer');},
        composite: () => {}, renderLayerPanel: () => {}, deriveBusyLabel: () => 'Removing',
        getSelectedAIEndpoint: () => ({}), spinnerModule: {}, uiModule: {showToast(){}},
      });
      await run('/api/image/remove-bg', {}, 'BG Removed', document.createElement('button'));
      return {correctInput:sent.image === expected, blueUnchanged:blue.canvas.toDataURL() === beforeBlue,
        count:state.layers.length, visible:state.layers.every(l => l.visible), snapshots,
        alpha:red.ctx.getImageData(0,0,1,1).data[3], kind:red.kind, placed:red.placed};
    }""")
    assert result == dict(correctInput=True, blueUnchanged=True, count=2, visible=True, snapshots=1, alpha=0, kind='raster', placed=None)


def test_levels_shortcut_opens_controls_without_menu_commit(page):
    result = page.evaluate("""async () => {
      const {state} = await import('/static/js/editor/state.js');
      const {wireKeyboardShortcuts} = await import('/static/js/editor/keyboard-shortcuts.js');
      state.editorOpen = true;
      let selected;
      wireKeyboardShortcuts({toolbar:document.createElement('div'), toolKeyMap:{},
        openLayerAdjustment:type => selected = type});
      const event = new KeyboardEvent('keydown', {key:'l', code:'KeyL', ctrlKey:true, altKey:true,
        bubbles:true, cancelable:true});
      document.dispatchEvent(event);
      return {selected, prevented:event.defaultPrevented};
    }""")
    assert result == dict(selected='levels', prevented=True)


def test_hue_saturation_shortcut_opens_adjustment(page):
    result = page.evaluate("""async () => {
      const {state} = await import('/static/js/editor/state.js');
      const {wireKeyboardShortcuts} = await import('/static/js/editor/keyboard-shortcuts.js');
      state.editorOpen = true;
      let selected;
      wireKeyboardShortcuts({toolbar:document.createElement('div'), toolKeyMap:{},
        openLayerAdjustment:type => selected = type});
      const event = new KeyboardEvent('keydown', {key:'U', code:'KeyU', ctrlKey:true, shiftKey:true,
        bubbles:true, cancelable:true});
      document.dispatchEvent(event);
      return {selected, prevented:event.defaultPrevented};
    }""")
    assert result == dict(selected='hue-saturation', prevented=True)


def test_inner_shadow_clips_inside_and_reverses_direction(page):
    result = page.evaluate("""async () => {
      const {renderEffects} = await import('/static/js/editor/effects.js');
      const source = document.createElement('canvas'); source.width=source.height=60;
      const ctx=source.getContext('2d');ctx.fillStyle='white';ctx.fillRect(15,15,30,30);
      function sample(inside,x) {
        const out=renderEffects(source,[{type:'drop-shadow',params:{inside,x,y:0,blur:0,opacity:1,color:'#000000'}}]);
        const c=out.getContext('2d');
        return {left:[...c.getImageData(16,30,1,1).data],right:[...c.getImageData(43,30,1,1).data],
          outside:[...c.getImageData(47,30,1,1).data]};
      }
      return {right:sample(true,6),left:sample(true,-6),outer:sample(false,6)};
    }""")
    assert result['right']['left'] == [0,0,0,255]
    assert result['right']['right'] == [255,255,255,255]
    assert result['left']['right'] == [0,0,0,255]
    assert result['right']['outside'][3] == 0
    assert result['outer']['outside'] == [0,0,0,255]


def test_cut_placed_image_rasterizes_once_without_confirmation(page):
    page.route('http://editor.test/api/**', lambda route: route.fulfill(json={}))
    page.evaluate("""async () => {
      document.body.innerHTML='<div id="toast"></div><div id="gallery-editor-container"></div>';
      const editor=await import('/static/js/galleryEditor.js');
      const {state}=await import('/static/js/editor/state.js');
      editor.openEditor(null,null,{w:32,h:32},'Cut test');
      const layer=state.layers.at(-1);
      layer.ctx.fillStyle='red';layer.ctx.fillRect(0,0,32,32);
      layer.kind='placed';layer.placed={sourceName:'Original image'};
      document.activeElement?.blur();
      document.dispatchEvent(new KeyboardEvent('keydown',{key:'x',code:'KeyX',ctrlKey:true,bubbles:true,cancelable:true}));
    }""")
    page.wait_for_function("""async () => {
      const {state}=await import('/static/js/editor/state.js');
      return !!state.internalClipboard && state.layers.at(-1).ctx.getImageData(0,0,1,1).data[3]===0;
    }""")
    result=page.evaluate("""async () => {
      const {state}=await import('/static/js/editor/state.js');
      return {kind:state.layers.at(-1).kind,placed:state.layers.at(-1).placed,
        copied:[...state.internalClipboard.getContext('2d').getImageData(0,0,1,1).data],
        prompted:document.querySelector('#styled-confirm-modal')?.textContent?.includes('Rasterize layer')||false};
    }""")
    assert result==dict(kind='raster',placed=None,copied=[255,0,0,255],prompted=False)


def test_drop_shadow_dialog_inside_and_direction_apply(page):
    page.route('http://editor.test/api/**', lambda route: route.fulfill(json={}))
    page.add_style_tag(content=(ROOT/'static/css/13-image-editor.css').read_text())
    page.add_style_tag(content=':root{--panel:#242426;--bg:#171719;--fg:#eee;--border:#454549;--red:#d47965;}body{background:var(--bg);color:var(--fg);font-family:Arial,sans-serif;}')
    page.evaluate("""async () => {
      document.body.innerHTML='<div id="toast"></div><div id="gallery-editor-container" style="height:680px"></div>';
      const editor=await import('/static/js/galleryEditor.js');
      editor.openEditor(null,null,{w:64,h:64},'Shadow test');
      document.querySelector('[data-filter-action="effect-drop-shadow"]').click();
    }""")
    page.locator('.ge-filter-overlay select[data-key="inside"]').select_option('1')
    page.locator('.ge-filter-overlay input[data-key="angle"]').evaluate("el=>{el.value='90';el.dispatchEvent(new Event('input',{bubbles:true}));}")
    page.screenshot(path='/tmp/editor-shadow-inside-direction.png')
    page.locator('.ge-filter-overlay [data-action="apply"]').click()
    page.wait_for_function("""async () => {
      const {state}=await import('/static/js/editor/state.js');
      return state.layers.at(-1).effects.length===1;
    }""")
    result=page.evaluate("""async () => {
      const {state}=await import('/static/js/editor/state.js');
      const {inside,x,y}=state.layers.at(-1).effects[0].params;
      return {inside,x,y};
    }""")
    assert result==dict(inside=True,x=0,y=-7)


def test_reorder_rebuilds_effect_rows_under_their_parent(page):
    result=page.evaluate("""async () => {
      const {state}=await import('/static/js/editor/state.js');
      const {createLayerPanelRenderer}=await import('/static/js/editor/layer-panel.js');
      document.body.innerHTML='<div id="ge-layers-list"></div>';
      state.container=document.body;state.editorOpen=true;
      function layer(id,effects=[]) {
        const canvas=document.createElement('canvas');canvas.width=canvas.height=16;
        return {id,name:id,kind:'raster',canvas,ctx:canvas.getContext('2d'),visible:true,
          opacity:1,locks:{},effects,masks:[],adjLayers:[]};
      }
      const a=layer('a',[{id:'shadow-a',type:'drop-shadow',params:{}}]), b=layer('b');
      state.layers=[a,b];state.activeLayerId=a.id;state.layerOffsets=new Map();state.layerGroups=[];
      let options;
      const renderer=createLayerPanelRenderer({composite(){},saveState(){},
        dragSortModule:{enable:(id,selector,config)=>{options=config;}},uiModule:{showToast(){}}});
      renderer.render();
      const list=document.getElementById('ge-layers-list');
      const row=id=>list.querySelector(`.ge-layer-item[data-layer-id="${id}"]`);
      list.insertBefore(row('a'),row('b'));
      options.onReorder([...list.querySelectorAll('.ge-layer-item[data-layer-id]')]);
      return {order:state.layers.map(layer=>layer.id),excluded:options.excludeSelector,
        rows:[...list.children].map(el=>el.dataset.layerId||el.dataset.effectId)};
    }""")
    assert result==dict(order=['b','a'],excluded='.ge-adj-sub-item',rows=['a','shadow-a','b'])


@pytest.mark.parametrize('tool', ['marquee','lasso'])
@pytest.mark.parametrize('pointer_type', ['mouse','pen','touch'])
@pytest.mark.parametrize('pan', [False,True])
def test_selections_start_outside_canvas_unless_explicitly_panning(page,tool,pointer_type,pan):
    result=page.evaluate("""async ({tool,pointerType,pan}) => {
      const {state}=await import('/static/js/editor/state.js');
      const {wireCanvasEvents}=await import('/static/js/editor/canvas-events.js');
      document.body.innerHTML='<div id="area"><canvas width="100" height="100"></canvas><button>Chrome</button></div>';
      const area=document.getElementById('area');
      state.mainCanvas=area.querySelector('canvas');state.tool=tool;
      state.editorOpen=true;state.spacePanActive=pan;state.panX=state.panY=0;
      let starts=0,moves=0,ends=0;
      wireCanvasEvents({canvasArea:area,beginDraw:()=>starts++,continueDraw:()=>moves++,
        endDraw:()=>ends++,updateBrushCursor(){},syncZoomControls(){}});
      function pointer(type,target=area) {
        target.dispatchEvent(new PointerEvent(type,{pointerId:5,pointerType,button:0,
          clientX:type==='pointerdown'?10:30,clientY:type==='pointerdown'?10:30,bubbles:true,cancelable:true}));
      }
      pointer('pointerdown');
      if(pointerType==='mouse')area.dispatchEvent(new MouseEvent('mousedown',{button:0,bubbles:true}));
      pointer('pointermove');
      if(pointerType==='mouse')window.dispatchEvent(new MouseEvent('mousemove'));
      pointer('pointerup');
      if(pointerType==='mouse')window.dispatchEvent(new MouseEvent('mouseup'));
      const after={starts,moves,ends,panX:state.panX};
      pointer('pointerdown',area.querySelector('button'));
      return {...after,chromeStarted:starts!==after.starts};
    }""",dict(tool=tool,pointerType=pointer_type,pan=pan))
    assert result['starts']==(0 if pan else 1)
    assert result['panX']==(20 if pan else 0)
    assert not result['chromeStarted']
    if not pan:
        assert result['moves']==1
        assert result['ends']==1


def test_marquee_drag_from_workspace_creates_clipped_selection(page):
    result=page.evaluate("""async () => {
      const {state}=await import('/static/js/editor/state.js');
      const {wireCanvasEvents}=await import('/static/js/editor/canvas-events.js');
      const {createMarqueeTool}=await import('/static/js/editor/tools/marquee.js');
      document.body.innerHTML='<div id="area"><canvas width="100" height="100" style="position:absolute;left:100px;top:100px;width:100px;height:100px"></canvas></div>';
      const area=document.getElementById('area');state.mainCanvas=area.querySelector('canvas');
      state.tool='marquee';state.imgWidth=state.imgHeight=100;state.editorOpen=true;
      state.spacePanActive=false;state.panX=state.panY=0;state.wandMask=null;
      const layer={id:'a'};state.layers=[layer];state.activeLayerId='a';state.layerOffsets=new Map();
      const tool=createMarqueeTool({activeLayer:()=>layer,saveState(){},composite(){},drawOverlay(){},syncSelectionUi(){}});
      wireCanvasEvents({canvasArea:area,beginDraw:e=>tool.begin(e),continueDraw:e=>tool.drag(e),
        endDraw:e=>tool.end(e),updateBrushCursor(){},syncZoomControls(){}});
      area.dispatchEvent(new PointerEvent('pointerdown',{pointerType:'mouse',pointerId:1,button:0,clientX:80,clientY:80,bubbles:true}));
      area.dispatchEvent(new MouseEvent('mousedown',{button:0,clientX:80,clientY:80,bubbles:true}));
      window.dispatchEvent(new MouseEvent('mousemove',{clientX:150,clientY:150}));
      window.dispatchEvent(new MouseEvent('mouseup',{clientX:150,clientY:150}));
      const ctx=state.wandMask.getContext('2d');
      return {inside:ctx.getImageData(0,0,1,1).data[3],outside:ctx.getImageData(70,70,1,1).data[3],pan:state.panX};
    }""")
    assert result==dict(inside=255,outside=0,pan=0)


def test_save_menu_remains_clickable_after_png_export_and_window_raise(page):
    page.add_style_tag(content='''
      .modal {position:fixed;inset:0;z-index:20000;background:#222;}
      #ge-save-menu-btn {position:absolute;right:20px;top:20px;}
      .ge-save-menu {position:fixed;z-index:10005;width:200px;background:#333;}
      .ge-save-menu[hidden] {display:none;}
      .ge-save-menu button {display:block;width:100%;height:30px;}
    ''')
    page.evaluate("""async () => {
      const {wireTopbar}=await import('/static/js/editor/wire-topbar.js');
      document.body.innerHTML='<div id="gallery-modal" class="modal"><div id="gallery-editor-container"><div class="ge-topbar"><div class="ge-save-wrap"><button id="ge-save-menu-btn">Save</button><div id="ge-save-menu" class="ge-save-menu" hidden><button id="ge-save">Save image</button><button id="ge-export-png">Save as PNG</button></div></div></div></div></div>';
      window.pngExports=0;window.saves=0;
      document.getElementById('ge-save').addEventListener('click',()=>window.saves++);
      wireTopbar({registerDocClickAway(){},exportPNGFile:()=>window.pngExports++});
    }""")
    page.locator('#ge-save-menu-btn').click()
    page.locator('#ge-export-png').click(timeout=2000)
    page.evaluate("document.getElementById('gallery-modal').style.zIndex='50000'")
    page.locator('#ge-save-menu-btn').click()
    page.locator('#ge-save').click(timeout=2000)
    assert page.evaluate('({png:window.pngExports,saves:window.saves})') == dict(png=1,saves=1)


def test_submenu_hover_aligns_to_row_and_flips_at_right_edge(page):
    page.add_style_tag(content='''
      #menu {position:absolute; left:780px; top:100px; width:180px;}
      button {display:block; height:35px; width:180px;}
      .ge-submenu {display:none; position:absolute; width:160px; height:120px; top:0; left:100%;}
      .ge-menu-parent:hover + .ge-submenu {display:block;}
    ''')
    page.evaluate("""async () => {
      document.body.innerHTML = '<div id="menu"><button>First</button><button>Second</button>'
        + '<button class="ge-menu-parent">Blur &gt;</button><div class="ge-submenu">Gaussian</div></div>';
      const {wireSubmenuToggles} = await import('/static/js/editor/submenu-toggles.js');
      wireSubmenuToggles(document.getElementById('menu'));
    }""")
    page.locator('.ge-menu-parent').hover()
    row = page.locator('.ge-menu-parent').bounding_box()
    panel = page.locator('.ge-submenu').bounding_box()
    assert abs(panel['y'] - row['y']) <= 1
    assert panel['x'] + panel['width'] <= row['x'] + 1
    page.screenshot(path='/tmp/editor-submenu-alignment.png')


@pytest.mark.parametrize('prompt', ['remove bg', 'remove background', 'remove the background from the selected layer'])
def test_quick_edit_planner_uses_background_tool(prompt):
    from routes.editor_agent_routes import _plan_special
    steps, _, _ = _plan_special(prompt, {}, True)
    assert steps == [{'op': 'ai.removeBackground', 'params': {}}]


def test_quick_edit_background_removal_precedes_creative_routing():
    source = (ROOT / 'static/js/galleryEditor.js').read_text()
    local = source.split('const _executeQuickEditLocal = async (prompt) => {', 1)[1]
    assert local.index('if (_looksBackgroundRemoval(p))') < local.index('if (_looksRecolor(p))')


@pytest.mark.parametrize('run', [False, True])
def test_background_tool_suggestion_prepares_command_and_enter_submits(page, run):
    source = (ROOT / 'static/js/galleryEditor.js').read_text()
    picker = 'const pickSuggestion = ' + source.split('const pickSuggestion = ', 1)[1].split("  wrap.addEventListener('pointerdown'", 1)[0]
    assert "runPrompt: id === 'rembg' ? 'remove background' : null" in source
    result = page.evaluate("""({picker, run}) => {
      const suggestionItems = [{toolId:'rembg', label:'Bg Remove', runPrompt:'remove background'}];
      const input = document.createElement('input'); input.value = 'bg';
      let submitted = 0, selected = null, clearSynced = false;
      const form = {requestSubmit: () => submitted++};
      const _clickToolButton = id => selected = id;
      const _setAiCommandStatus = () => {};
      const hideSuggestions = () => {};
      const syncClearButton = () => clearSynced = true;
      eval(picker + '; pickSuggestion(0, run);');
      return {prompt:input.value, submitted, selected, clearSynced};
    }""", {'picker':picker, 'run':run})
    assert result == dict(prompt='remove background', submitted=int(run), selected='rembg', clearSynced=True)


@pytest.mark.parametrize('tool', ['dodge', 'burn'])
def test_retouch_invalidates_adjustment_preview_and_stops_before_undo(page, tool):
    result = page.evaluate("""async tool => {
      const {state} = await import('/static/js/editor/state.js');
      const {createStrokePipeline} = await import('/static/js/editor/stroke-pipeline.js');
      const {renderLayerWithAdjLayers} = await import('/static/js/editor/fx/pixel-pass.js');
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 30;
      const ctx = canvas.getContext('2d'); ctx.fillStyle = 'rgb(100,100,100)'; ctx.fillRect(0,0,30,30);
      const layer = {id:'target', canvas, ctx, adjLayers:[{id:'adj', type:'brightness-contrast',
        params:{brightness:1.1,contrast:1}, visible:true, opacity:1}]};
      state.layers = [layer]; state.layerOffsets = new Map(); state.tool = tool;
      Object.assign(state,{brushSize:12, brushOpacity:100, brushFlow:100, brushSoftness:0,
        brushSpacing:20, brushSmoothing:0, dodgeBurnIntensity:50});
      const original = ctx.getImageData(0,0,30,30);
      const pixel = () => renderLayerWithAdjLayers(layer).getContext('2d').getImageData(15,15,1,1).data[0];
      const before = pixel();
      const pipeline = createStrokePipeline({activeLayer:()=>layer, getActiveMaskLayer:()=>null, composite:()=>{}});
      pipeline.beginStroke({x:15,y:15,pressure:1},tool);
      const during = pixel();
      pipeline.endStroke(null); // Finish before Undo restores the saved pixels.
      ctx.putImageData(original,0,0); layer._adjFinalKey = null; layer._adjCacheKey = null;
      pipeline.endStroke({x:28,y:28,pressure:1}); // A late pointer-up cannot replay this stroke.
      return {before,during,after:pixel(),corner:ctx.getImageData(28,28,1,1).data[0]};
    }""", tool)
    assert result['during'] > result['before'] if tool == 'dodge' else result['during'] < result['before']
    assert result['after'] == result['before']
    assert result['corner'] == 100


def test_undo_finishes_active_stroke_before_restoring_history():
    source = (ROOT / 'static/js/galleryEditor.js').read_text()
    undo = source.split('function undo() {', 1)[1].split('function redo()', 1)[0]
    assert undo.index('if (state.drawing) _strokeTool.tryEnd();') < undo.index('state.undoStack.pop()')


@pytest.mark.parametrize('width,height', [(1000,700), (390,844)])
def test_filter_submenu_rows_have_equal_heights_with_and_without_icons(page, width, height):
    page.set_viewport_size({'width':width,'height':height})
    page.add_style_tag(content=(ROOT / 'static/css/13-image-editor.css').read_text())
    page.evaluate("""() => {
      document.body.innerHTML = '<div class="ge-submenu" style="display:block;left:8px;top:8px;">'
        + '<button class="dropdown-item-compact"><span>Plain</span></button>'
        + '<button class="dropdown-item-compact"><svg width="24" height="24"><circle cx="12" cy="12" r="8"/></svg><span>Icon</span></button>'
        + '<button class="dropdown-item-compact"><span>Longer label</span><svg width="10" height="10"></svg></button></div>';
    }""")
    heights = page.locator('.dropdown-item-compact').evaluate_all('(rows)=>rows.map(row=>row.getBoundingClientRect().height)')
    assert heights == ([28]*3 if width > 820 else [32]*3)
    page.screenshot(path=f'/tmp/editor-filter-row-heights-{width}.png')


def test_ai_settings_escape_closes_only_the_dialog(page):
    page.route('**/api/auth/settings', lambda route: route.fulfill(json={'image_model':'', 'image_edit_model':''}))
    result = page.evaluate("""async () => {
      const {createAiSettings} = await import('/static/js/editor/ai-settings.js');
      const container = document.createElement('div'); document.body.append(container);
      const settings = createAiSettings({container}); settings.open();
      let reachedEditor = false;
      document.addEventListener('keydown', () => reachedEditor = true, {capture:true, once:true});
      document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true}));
      const result = {closed:settings.element.hidden, reachedEditor}; settings.destroy();
      return result;
    }""")
    assert result == dict(closed=True, reachedEditor=False)


def test_image_model_settings_sync_both_directions_and_survive_reopening(page):
    saved = {'image_model':'openai/gpt-5-image', 'image_edit_model':'', 'image_quality':'medium', 'image_gen_enabled':True}
    writes = []
    user_prefs = {}

    def settings_api(route):
        if route.request.method == 'POST':
            patch = route.request.post_data_json
            writes.append(patch)
            saved.update(patch)
        route.fulfill(json=dict(saved))

    def prefs_api(route):
        url = route.request.url
        if route.request.method == 'PUT':
            key = url.split('/api/prefs/')[-1]
            data = route.request.post_data_json or {}
            val = data.get('value')
            if val is None:
                user_prefs.pop(key, None)
            else:
                user_prefs[key] = val
            route.fulfill(status=200, json={'key': key, 'value': val})
            return
        route.fulfill(status=200, json=dict(user_prefs))

    page.route('**/api/auth/settings', settings_api)
    page.route('**/api/prefs**', prefs_api)
    page.route('**/api/model-endpoints', lambda route: route.fulfill(json=[{
        'is_enabled':True, 'online':True, 'model_type':'llm',
        'models':['openai/gpt-5-image','openai/gpt-image-2'],
    }]))
    page.evaluate("""async () => {
      document.body.innerHTML = '<select id="set-imgModelSelect"><option value="">Auto</option></select>'
        + '<select id="set-imgEditModelSelect"><option value="">Same</option></select>'
        + '<select id="set-imgQualitySelect"><option value="medium">Medium</option></select>'
        + '<input type="checkbox" id="set-imgEnabledToggle"><span id="set-imgSettingsMsg"></span>';
      const {initImageSettings} = await import('/static/js/settings/imageModels.js');
      await initImageSettings();
      const {createAiSettings} = await import('/static/js/editor/ai-settings.js');
      window.dialog = createAiSettings({container:document.body});
      for (const id of ['ge-ai-model','ge-ai-command-model']) {
        const select = document.getElementById(id);
        for (const model of ['', 'openai/gpt-5-image','openai/gpt-image-2']) {
          const option = document.createElement('option');
          option.value = model ? 'https://openrouter.ai/api/v1::'+model : '';
          option.textContent = model || 'Auto'; select.append(option);
        }
      }
      dialog.open();
    }""")
    page.wait_for_function("document.getElementById('ge-ai-model').value.endsWith('::openai/gpt-5-image')")
    page.select_option('#ge-ai-model', 'https://openrouter.ai/api/v1::openai/gpt-image-2')
    page.wait_for_function("document.getElementById('set-imgModelSelect').value === 'openai/gpt-image-2'")
    assert saved['image_model'] == 'openai/gpt-image-2'
    page.select_option('#set-imgEditModelSelect', 'openai/gpt-image-2')
    page.wait_for_function("document.getElementById('ge-ai-command-model').value.endsWith('::openai/gpt-image-2')")
    page.evaluate('dialog.close(); dialog.open();')
    page.wait_for_function("document.getElementById('ge-ai-command-model').value.endsWith('::openai/gpt-image-2')")
    assert saved['image_edit_model'] == 'openai/gpt-image-2'
    assert len(writes) == 2


def test_non_admin_image_model_preferences_persist_and_reload(page):
    global_settings = {
        'image_model': 'global-default-gen',
        'image_edit_model': '',
        'image_quality': 'medium',
        'image_gen_enabled': True,
    }
    user_prefs = {}
    auth_posts = []
    pref_puts = []
    is_admin = False

    def handle_auth_settings(route):
        if route.request.method == 'POST':
            auth_posts.append(route.request.post_data_json)
            if not is_admin:
                route.fulfill(status=403, json={'error': 'Admin access is required to change these settings.'})
                return
            global_settings.update(route.request.post_data_json)
            route.fulfill(status=200, json=dict(global_settings))
            return
        route.fulfill(status=200, json=dict(global_settings))

    def handle_prefs(route):
        url = route.request.url
        if route.request.method == 'PUT':
            key = url.split('/api/prefs/')[-1]
            data = route.request.post_data_json or {}
            val = data.get('value')
            pref_puts.append((key, val))
            if val is None:
                user_prefs.pop(key, None)
            else:
                user_prefs[key] = val
            route.fulfill(status=200, json={'key': key, 'value': val})
            return
        route.fulfill(status=200, json=dict(user_prefs))

    page.route('**/api/auth/settings', handle_auth_settings)
    page.route('**/api/prefs**', handle_prefs)
    page.route('**/api/model-endpoints', lambda route: route.fulfill(json=[{
        'is_enabled': True, 'online': True, 'model_type': 'llm',
        'models': ['global-default-gen', 'openai/gpt-5-image', 'openai/gpt-image-2'],
    }]))

    # Step 1: Non-admin loads image settings, selects a preference
    page.evaluate("""async () => {
      document.body.innerHTML = '<select id="set-imgModelSelect"><option value="">Auto</option></select>'
        + '<select id="set-imgEditModelSelect"><option value="">Same</option></select>'
        + '<select id="set-imgQualitySelect"><option value="medium">Medium</option></select>'
        + '<input type="checkbox" id="set-imgEnabledToggle" checked><span id="set-imgSettingsMsg"></span>';
      const {initImageSettings} = await import('/static/js/settings/imageModels.js');
      await initImageSettings();
    }""")

    # Non-admin changes generation model
    page.select_option('#set-imgModelSelect', 'openai/gpt-image-2')

    # Wait for status message to show 'Saved'
    page.wait_for_function("document.getElementById('set-imgSettingsMsg').textContent === 'Saved'")
    msg_color = page.evaluate("document.getElementById('set-imgSettingsMsg').style.color")
    assert msg_color == 'var(--fg)', f"Expected var(--fg), got {msg_color}"

    # Verify per-user preference was saved and global toggle was NOT propagated
    assert user_prefs.get('image_model') == 'openai/gpt-image-2'
    assert 'image_gen_enabled' not in user_prefs, "Global policy toggle must not be saved to user prefs"

    # Step 2: Simulate page reload / re-initialization for non-admin
    page.evaluate("""async () => {
      document.getElementById('set-imgModelSelect').value = '';
      const {initImageSettings} = await import('/static/js/settings/imageModels.js');
      await initImageSettings();
    }""")
    page.wait_for_function("document.getElementById('set-imgModelSelect').value === 'openai/gpt-image-2'")
    assert page.evaluate("document.getElementById('set-imgModelSelect').value") == 'openai/gpt-image-2'

    # Step 3: Admin saves settings - pre-existing per-user preferences must be cleared
    is_admin = True
    page.evaluate("""async () => {
      const {saveImagePreferences} = await import('/static/js/settings/imagePreferences.js');
      await saveImagePreferences({ image_model: 'admin-chosen-model', image_edit_model: '', image_quality: 'high', image_gen_enabled: true });
    }""")
    # Verify per-user preference for image_model was cleared (PUT with value: null)
    assert 'image_model' not in user_prefs, "Admin save must clear pre-existing per-user preference"
    assert global_settings['image_model'] == 'admin-chosen-model'

    # Admin reloads settings - receives newly saved global setting, not stale pref
    page.evaluate("""async () => {
      const {initImageSettings} = await import('/static/js/settings/imageModels.js');
      await initImageSettings();
    }""")
    assert global_settings['image_model'] == 'admin-chosen-model'


def test_generation_places_result_without_stretching_or_refresh(page):
    result = page.evaluate("""async () => {
      const {createImageGenerationAction} = await import('/static/js/editor/image-generation.js');
      const image = document.createElement('canvas'); image.width = 80; image.height = 20;
      const ctx = image.getContext('2d'); ctx.fillStyle = 'red'; ctx.fillRect(0,0,80,20);
      const selection = document.createElement('canvas'); selection.width = selection.height = 40;
      selection.getContext('2d').fillRect(0,0,40,40);
      let placed = null, body = null;
      const run = createImageGenerationAction({
        getDocument:()=>({key:'one',width:40,height:40,mask:selection}),
        fetchImpl:async (url,opts) => {
          body = JSON.parse(opts.body);
          selection.getContext('2d').clearRect(0,0,40,40);
          return {ok:true,json:async()=>({image:image.toDataURL()})};
        },
        placeResult:(canvas,name,mask)=>{
          const data = canvas.getContext('2d');
          placed = {width:canvas.width,height:canvas.height,left:data.getImageData(0,10,1,1).data[0],
            right:data.getImageData(79,10,1,1).data[0], mask:mask.getContext('2d').getImageData(20,20,1,1).data[3]};
          return 'new-layer';
        },
      });
      const id = await run({prompt:'a cow',masked:true});
      return {id,placed,body};
    }""")
    assert result['id'] == 'new-layer'
    assert result['placed'] == dict(width=80,height=20,left=255,right=255,mask=255)
    assert result['body'] == dict(_use_image_settings=True,prompt='a cow',width=40,height=40)


@pytest.mark.parametrize('action', ['cancel','timeout','invalid-image','document-switch'])
def test_generation_finishes_cleanly_on_failure_without_late_placement(page, action):
    result = page.evaluate("""async action => {
      const {createImageGenerationAction} = await import('/static/js/editor/image-generation.js');
      let key = 'original', placements = 0;
      const controller = new AbortController();
      const run = createImageGenerationAction({
        getDocument:()=>({key,width:40,height:40}), getSignal:()=>controller.signal,
        timeoutMs:action === 'timeout' ? 20 : 2000,
        fetchImpl:async (url,opts)=>{
          if (action === 'timeout' || action === 'cancel') {
            if (action === 'cancel') setTimeout(()=>controller.abort(),10);
            return new Promise((resolve,reject)=>opts.signal.addEventListener('abort',
              ()=>reject(new DOMException('Aborted','AbortError')), {once:true}));
          }
          if (action === 'invalid-image') return {ok:true,json:async()=>({image:'bad-image-data'})};
          const image = document.createElement('canvas'); image.width = image.height = 4;
          key = 'different';
          return {ok:true,json:async()=>({image:image.toDataURL()})};
        },
        placeResult:()=>placements++,
      });
      let error = '';
      try {await run({prompt:'a cow'});} catch (e) {error = e.message;}
      return {error,placements};
    }""", action)
    assert result['placements'] == 0
    assert result['error']
    if action == 'timeout':
        assert 'timed out' in result['error']
    if action == 'document-switch':
        assert 'document changed' in result['error']


def test_generation_provider_error_is_not_retried_and_next_request_can_succeed(page):
    result = page.evaluate("""async () => {
      const {createImageGenerationAction} = await import('/static/js/editor/image-generation.js');
      let calls = 0, placements = 0;
      const image = document.createElement('canvas'); image.width = image.height = 4;
      const run = createImageGenerationAction({
        getDocument:()=>({key:'one',width:40,height:40}),
        formatError:({status})=>status === 503 ? 'Generation is disabled' : 'error',
        fetchImpl:async ()=> ++calls === 1
          ? {ok:false,status:503,json:async()=>({detail:'Disabled'})}
          : {ok:true,json:async()=>({image:image.toDataURL()})},
        placeResult:()=>++placements,
      });
      let error;
      try {await run({prompt:'one'});} catch (e) {error = e.message;}
      const callsAfterFailure = calls;
      await run({prompt:'two'});
      return {error,callsAfterFailure,calls,placements};
    }""")
    assert result == dict(error='Generation is disabled',callsAfterFailure=1,calls=2,placements=1)


def test_late_progress_response_cannot_restore_working_after_completion(page):
    source = (ROOT / 'static/js/galleryEditor.js').read_text()
    functions = source.split('function _startAiProgress(requestId) {',1)[1].split('function _setAiCommandStatus(',1)[0]
    functions = 'function _startAiProgress(requestId) {' + functions
    result = page.evaluate("""async functions => {
      let _aiProgressTimer = null, _aiProgressEpoch = 0, status = 'Working';
      let resolveRequest;
      const fetch = () => new Promise(resolve => resolveRequest = resolve);
      const _setAiProgress = () => {};
      const _setAiCommandStatus = text => status = text;
      const api = eval(functions + '; ({start:_startAiProgress,stop:_stopAiProgress});');
      api.start('old-request');
      api.stop(); status = 'Done';
      resolveRequest({ok:true,json:async()=>({stage:'editing',progress:0.5})});
      await new Promise(resolve=>setTimeout(resolve,20));
      return status;
    }""", functions)
    assert result == 'Done'


@pytest.mark.parametrize('success', [True,False])
def test_quick_edit_queue_clears_working_on_completion(page, success):
    source = (ROOT / 'static/js/galleryEditor.js').read_text()
    pump = 'const pumpQuickEdit = ' + source.split('const pumpQuickEdit = ',1)[1].split('  const enqueueQuickEditJob',1)[0]
    result = page.evaluate("""async ({pump,success}) => {
      const {finishQuickEditJob} = await import('/static/js/editor/quick-edit-job.js');
      const jobs = [{id:'one',prompt:'fill',status:'queued'}];
      let pumping = false, _quickEditActiveJob = null, stopped = 0, terminal;
      const renderJobStack = () => {};
      const dismissJob = () => {};
      const _stopAiProgress = () => stopped++;
      const _setAiCommandStatus = (text,kind) => terminal = {text,kind};
      const executeQuickEdit = async () => success ? {ok:true} : {ok:false,error:'Provider failed'};
      await eval(pump + '; pumpQuickEdit();');
      return {status:jobs[0].status,detail:jobs[0].detail,terminal,stopped,pumping};
    }""", {'pump':pump,'success':success})
    expected = 'done' if success else 'error'
    message = 'Done' if success else 'Provider failed'
    assert result == dict(status=expected,detail=message,terminal=dict(text=message,kind=expected),stopped=1,pumping=False)


@pytest.mark.parametrize('bridge', [False,True])
@pytest.mark.parametrize('instruction', [False,True])
@pytest.mark.parametrize('matching_ratio', [False,True])
def test_generated_layer_can_keep_pixels_outside_document_bounds(page, bridge, instruction, matching_ratio):
    source = (ROOT / 'static/js/galleryEditor.js').read_text()
    placement = 'const _placeResultLayer = ' + source.split('const _placeResultLayer = ',1)[1].split('  const _generateImage',1)[0]
    bridge_line = next(line for line in source.splitlines() if "'layers.placeImage':" in line)
    result = page.evaluate("""async ({placement,bridgeLine,bridge,instruction,matchingRatio}) => {
      const {imageResultGeometry} = await import('/static/js/editor/image-result-geometry.js');
      const state = {imgWidth:matchingRatio ? 1280 : 40,imgHeight:matchingRatio ? 720 : 40,layers:[],layerOffsets:new Map()};
      let saved = 0;
      const _saveState = ()=>saved++;
      const composite = ()=>{};
      const _renderLayerPanel = ()=>{};
      const createLayer = (name,w,h)=>{
        const canvas = document.createElement('canvas'); canvas.width=w;canvas.height=h;
        return {id:'new',name,canvas,ctx:canvas.getContext('2d')};
      };
      const image = document.createElement('canvas'); image.width=matchingRatio ? 1536 : 80;image.height=matchingRatio ? 864 : 20;
      image.getContext('2d').fillRect(0,0,image.width,image.height);
      if (bridge) {
        const run = eval('({' + bridgeLine + '})')["layers.placeImage"].run;
        await run({dataUrl:image.toDataURL(),nativeSize:true,masked:false,
          mode:instruction ? 'instruction' : undefined,differenceMask:instruction});
      } else {
        eval(placement + '; _placeResultLayer(image,"Generated",null);');
      }
      const layer = state.layers[0];
      return {width:layer.canvas.width,height:layer.canvas.height,offset:state.layerOffsets.get(layer.id),
        outsideAlpha:layer.ctx.getImageData(79,10,1,1).data[3],saved};
    }""", {'placement':placement,'bridgeLine':bridge_line,'bridge':bridge,'instruction':instruction,'matchingRatio':matching_ratio})
    expected = dict(width=1280,height=720,offset=dict(x=0,y=0),outsideAlpha=255,saved=1) if matching_ratio else dict(width=80,height=20,offset=dict(x=-20,y=10),outsideAlpha=255,saved=1)
    assert result == expected


def test_whole_image_edit_does_not_clip_native_result_to_select_all(page):
    source = (ROOT / 'static/js/galleryEditor.js').read_text()
    operation = 'const _aiInpaintSelection = ' + source.split('const _aiInpaintSelection = ',1)[1].split('  // Replace the background/scene',1)[0]
    result = page.evaluate("""async operation => {
      const mask = document.createElement('canvas'); mask.width=40;mask.height=40;
      const canvas = document.createElement('canvas'); canvas.width=80;canvas.height=20;
      const state = {wandMask:mask};
      const activeLayer = ()=>({id:'one'});
      const _aiEditCanvas = async ()=>({canvas,fullFrame:true});
      const _placeResultLayer = (result,name,resultMask)=>({width:result.width,height:result.height,masked:!!resultMask});
      return await eval(operation + '; _aiInpaintSelection("change the sky");');
    }""", operation)
    assert result == dict(width=80,height=20,masked=False)


@pytest.mark.parametrize('matching_ratio', [False,True])
def test_inpaint_panel_preserves_native_whole_edit_dimensions(page, matching_ratio):
    source = (ROOT / 'static/js/editor/ai-inpaint.js').read_text()
    body = 'const fullFrame = ' + source.split('const fullFrame = ',1)[1].split('          // Snapshot the AI result',1)[0]
    result = page.evaluate("""async ({body,matchingRatio}) => {
      const {imageResultGeometry} = await import('/static/js/editor/image-result-geometry.js');
      const state = {imgWidth:matchingRatio ? 1280 : 40,imgHeight:matchingRatio ? 720 : 40,layerOffsets:new Map()};
      const data = {full_frame:true}, layerName = 'Edit';
      const createLayer = (name,w,h)=>{
        const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;
        return {id:'result',canvas,ctx:canvas.getContext('2d')};
      };
      const source = document.createElement('canvas'); source.width=matchingRatio ? 1536 : 80;source.height=matchingRatio ? 864 : 20;
      source.getContext('2d').fillRect(0,0,source.width,source.height);
      const resultImg = new Image(); resultImg.src=source.toDataURL(); await resultImg.decode();
      return eval(body + '; ({width:resultLayer.canvas.width,height:resultLayer.canvas.height,offset:state.layerOffsets.get(resultLayer.id)});');
    }""", {'body':body,'matchingRatio':matching_ratio})
    assert result == (dict(width=1280,height=720,offset=dict(x=0,y=0)) if matching_ratio else dict(width=80,height=20,offset=dict(x=-20,y=10)))


def test_brush_softness_is_in_main_bar_and_saves_extended_value(page):
    page.add_style_tag(content=(ROOT / 'static/css/13-image-editor.css').read_text())
    result = page.evaluate("""async () => {
      const {controlsHTML} = await import('/static/js/editor/build/controls.js');
      const {wireStrokeToolSliders} = await import('/static/js/editor/stroke-tool-sliders.js');
      const {state} = await import('/static/js/editor/state.js');
      const bar = document.createElement('div');bar.className='ge-options-bar';
      const controls = document.createElement('div');controls.className='ge-controls ge-controls--in-options';
      controls.innerHTML=controlsHTML({color:'#000000',brushSize:40,wandTolerance:20});
      for(const child of controls.children) if(child.id !== 'ge-brush-controls') child.style.display='none';
      bar.append(controls);document.body.append(bar);
      wireStrokeToolSliders();
      const slider=document.getElementById('ge-brush-softness');
      slider.value='1200';slider.dispatchEvent(new Event('input',{bubbles:true}));
      return {inMore:!!slider.closest('#ge-brush-more-menu'),max:slider.max,value:state.brushSoftness,
        visible:slider.getBoundingClientRect().height>0,label:document.getElementById('ge-brush-softness-label').textContent};
    }""")
    assert result == dict(inMore=False,max='1200',value=1200,visible=True,label='1200%')
    page.screenshot(path='/tmp/editor-brush-softness-main-bar.png')


def test_extended_softness_produces_softer_pixels_not_just_larger_slider_values(page):
    result = page.evaluate("""async () => {
      const {state}=await import('/static/js/editor/state.js');
      const {createStrokePipeline}=await import('/static/js/editor/stroke-pipeline.js');
      const results=[];
      for(const softness of [300,1200]) {
        const canvas=document.createElement('canvas');canvas.width=canvas.height=100;
        const layer={id:'brush',canvas,ctx:canvas.getContext('2d')};
        Object.assign(state,{tool:'brush',brushSize:60,brushSoftness:softness,brushOpacity:100,brushFlow:100,
          brushBlendMode:'source-over',color:'#000000',brushSpacing:15,brushSmoothing:0,
          brushPixel:false,pressureSize:false,pressureOpacity:false,pressureFlow:false,layerOffsets:new Map()});
        const pipeline=createStrokePipeline({activeLayer:()=>layer,getActiveMaskLayer:()=>null,composite:()=>{}});
        pipeline.beginStroke({x:50,y:50,pressure:1},'brush');pipeline.endStroke(null);
        const alpha=x=>layer.ctx.getImageData(x,50,1,1).data[3];
        results.push({middle:alpha(65),center:alpha(50),outside:alpha(85)});
        document.body.append(canvas);
      }
      return results;
    }""")
    assert result[1]['middle'] < result[0]['middle'] / 3
    assert result[1]['center'] > 150
    assert result[0]['outside'] == result[1]['outside'] == 0
    page.screenshot(path='/tmp/editor-brush-softness-falloff.png')


@pytest.mark.parametrize('width', [1000,840])
def test_brush_numbers_do_not_overlap_after_real_panel_normalization(page,width):
    page.set_viewport_size({'width':width,'height':700})
    for stylesheet in ['static/css/13-image-editor.css','static/css/documents-gallery-editor.css']:
        page.add_style_tag(content=(ROOT / stylesheet).read_text())
    page.add_style_tag(content=':root{--bg:#171719;--panel:#242426;--fg:#eee;--red:#d47965;--border:#454549;}body{background:var(--bg);}')
    result = page.evaluate("""async () => {
      const {state}=await import('/static/js/editor/state.js');
      const {controlsHTML}=await import('/static/js/editor/build/controls.js');
      const {buildRightPanel}=await import('/static/js/editor/build/right-panel.js');
      const {wireStrokeToolSliders}=await import('/static/js/editor/stroke-tool-sliders.js');
      state.container=document.createElement('div');document.body.append(state.container);
      state.brushSize=800;
      const {controls}=buildRightPanel({controlsHTML,layerPanelHTML:()=>'<div></div>'});
      controls.classList.add('ge-controls--in-options');
      const bar=document.createElement('div');bar.className='ge-options-bar';bar.append(controls);state.container.append(bar);
      for(const child of controls.children) if(child.id !== 'ge-brush-controls') child.style.display='none';
      wireStrokeToolSliders();
      const slider=document.getElementById('ge-brush-softness');
      const elements=[document.querySelector('#ge-brush-softness-row label'),slider,
        document.getElementById('ge-brush-softness-label'),document.querySelector('.ge-brush-size-row label'),
        document.querySelector('.ge-brush-size-row input')];
      const bounds=()=>elements.map(element=>{
        const r=element.getBoundingClientRect();return {x:r.x,right:r.right,width:r.width};
      });
      slider.value='1';slider.dispatchEvent(new Event('input'));const small=bounds();
      slider.value='1200';slider.dispatchEvent(new Event('input'));const large=bounds();
      return {small,large,value:document.getElementById('ge-brush-softness-label').textContent};
    }""")
    assert result['value'] == '1200%'
    assert all(bounds['width'] > 0 for bounds in result['large']), result
    for left,right in zip(result['large'],result['large'][1:]):
        assert left['right'] <= right['x'] + 1, result
    for small,large in zip(result['small'],result['large']):
        assert abs(small['x']-large['x']) <= 1, result
    page.screenshot(path=f'/tmp/editor-brush-number-layout-{width}.png')


@pytest.mark.parametrize('fail_once', [False, True])
def test_new_project_create_initializes_canvas(page, fail_once):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('http://editor.test/api/**', lambda route: route.fulfill(json={}))
    page.add_style_tag(content='''
      .modal {position:fixed;inset:0;display:flex;align-items:center;justify-content:center;z-index:250;pointer-events:none;}
      .modal-content {pointer-events:auto;background:#333;padding:20px;}
      #gallery-modal {z-index:700;}
      #gallery-modal > .modal-content {width:90vw;height:90vh;}
    ''')
    page.evaluate("""async () => {
      document.body.innerHTML = '<div id="toast"></div><div class="modal" id="gallery-modal"><div class="modal-content"><div id="gallery-editor-container"></div></div></div>';
      const editor = await import('/static/js/galleryEditor.js');
      window.newProject = editor.openEditor(null, null, null, 'New canvas');
      window.newProject.catch(error => { window.createError = error.stack; });
    }""")
    if fail_once:
        page.evaluate("""() => {
          const fill = CanvasRenderingContext2D.prototype.fillRect;
          CanvasRenderingContext2D.prototype.fillRect = function(...args) {
            if (this.canvas.width === 1024 && this.canvas.height === 1024) {
              CanvasRenderingContext2D.prototype.fillRect = fill;
              throw Error('Canvas allocation test failure');
            }
            return fill.apply(this, args);
          };
        }""")
    page.locator('#ge-canvas-prompt-ok').click(timeout=3000)
    if fail_once:
        page.wait_for_function("window.createError")
        assert 'Canvas allocation test failure' in page.evaluate('window.createError')
        assert 'Could not create project' in page.locator('body').inner_text()
        page.evaluate("""async () => {
          window.createError = null;
          const editor = await import('/static/js/galleryEditor.js');
          window.newProject = editor.openEditor(null, null, null, 'New canvas');
          window.newProject.catch(error => { window.createError = error.stack; });
        }""")
        page.locator('#ge-canvas-prompt-ok').click(timeout=3000)
    page.wait_for_timeout(500)
    result = page.evaluate("""async () => {
      const {state} = await import('/static/js/editor/state.js');
      return {error:window.createError, width:state.mainCanvas?.width,
        height:state.mainCanvas?.height, layers:state.layers.map(layer=>layer.name)};
    }""")
    assert not errors, errors
    assert not result.get('error'), result
    assert result == {'error':None,'width':1024,'height':1024,'layers':['Background','Edit']}


def test_undo_clears_rembg_live_layer_and_edge_tuner_cannot_corrupt(page):
    source = (ROOT / 'static/js/galleryEditor.js').read_text()
    restore_fn = source.split('function _restoreState(snap) {', 1)[1].split('function undo() {', 1)[0]
    assert 'state.rembgLiveLayer = null;' in restore_fn
    assert 'state.rembgLiveSnap = null;' in restore_fn

    page.route('http://editor.test/api/**', lambda route: route.fulfill(json={}))
    result = page.evaluate("""async () => {
      document.body.innerHTML = '<div id="toast"></div><div id="gallery-editor-container"></div>';
      const editor = await import('/static/js/galleryEditor.js');
      const {state} = await import('/static/js/editor/state.js');
      editor.openEditor(null, null, {w: 16, h: 16}, 'Rembg edge tuner undo test');

      const layer = state.layers.at(-1);
      layer.ctx.fillStyle = 'rgba(0, 255, 0, 1)';
      layer.ctx.fillRect(0, 0, 16, 16);
      const originalPixel = [...layer.ctx.getImageData(0, 0, 1, 1).data];

      // Save a valid pre-edit snapshot to undoStack
      const origCanvas = document.createElement('canvas');
      origCanvas.width = origCanvas.height = 16;
      origCanvas.getContext('2d').drawImage(layer.canvas, 0, 0);
      const preEditSnap = {
        imgWidth: 16, imgHeight: 16,
        activeLayerId: layer.id,
        selectedLayerIds: [layer.id],
        layers: [{
          id: layer.id,
          name: 'Layer 1',
          canvas: origCanvas,
          imageData: origCanvas.getContext('2d').getImageData(0, 0, 16, 16),
          visible: true,
          opacity: 1,
          blendMode: 'source-over',
          locks: {},
        }],
      };
      state.undoStack.push(preEditSnap);

      // Create tuner snapshot for background removal
      const snapCanvas = document.createElement('canvas');
      snapCanvas.width = snapCanvas.height = 16;
      const snapCtx = snapCanvas.getContext('2d');
      snapCtx.fillStyle = 'rgba(255, 0, 0, 1)';
      snapCtx.fillRect(4, 4, 8, 8); // cutout shape

      // Attach live tuner
      state.rembgLiveLayer = layer;
      state.rembgLiveSnap = snapCanvas;
      const tunerAttached = state.rembgLiveLayer === layer && state.rembgLiveSnap === snapCanvas;

      // Mutate layer pixels to simulate background removal result
      layer.ctx.clearRect(0, 0, 16, 16);
      layer.ctx.drawImage(snapCanvas, 0, 0);

      // Trigger Undo via Ctrl+Z
      document.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'z', code: 'KeyZ', ctrlKey: true, bubbles: true, cancelable: true,
      }));

      const clearedAfterUndo = state.rembgLiveLayer === null && state.rembgLiveSnap === null;
      const restoredLayer = state.layers.at(-1);
      const restoredPixel = [...restoredLayer.ctx.getImageData(0, 0, 1, 1).data];

      // Now interact with edge tuner sliders after undo
      const featherInput = document.getElementById('ge-rembg-feather');
      const growInput = document.getElementById('ge-rembg-grow');
      if (featherInput) {
        featherInput.value = '5';
        featherInput.dispatchEvent(new Event('input', { bubbles: true }));
      }
      if (growInput) {
        growInput.value = '5';
        growInput.dispatchEvent(new Event('input', { bubbles: true }));
      }

      // Check pixel again after tuner events: restored pixels must remain untouched
      const pixelAfterTuner = [...restoredLayer.ctx.getImageData(0, 0, 1, 1).data];
      return {
        tunerAttached,
        clearedAfterUndo,
        restoredPixel,
        originalPixel,
        pixelAfterTuner,
        rembgLiveLayerNull: state.rembgLiveLayer === null,
        rembgLiveSnapNull: state.rembgLiveSnap === null,
      };
    }""")
    assert result['tunerAttached'] is True
    assert result['clearedAfterUndo'] is True
    assert result['rembgLiveLayerNull'] is True
    assert result['rembgLiveSnapNull'] is True
    assert result['restoredPixel'] == result['originalPixel']
    assert result['pixelAfterTuner'] == result['originalPixel']
