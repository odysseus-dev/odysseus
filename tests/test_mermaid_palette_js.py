"""Mermaid follows palette changes without losing diagram source or racing."""
from tests.test_markdown_lazy_lib_loading_js import _run_node, node_available


_PALETTE = r'''
const root = {}, nodes = [], observers = [], initialized = [], inputs = [];
const colors = {'--bg':'#282c34','--fg':'#9cdef2','--panel':'#111111',
  '--border':'#355a66','--red':'#e06c75','--font-family':"'Fira Code', monospace"};
document.documentElement = root;
document.body = {};
globalThis.getComputedStyle = () => ({getPropertyValue: name => colors[name] || '',fontFamily:colors['--font-family']});
globalThis.MutationObserver = class {
  constructor(callback) {this.callback=callback;}
  observe(target, options) {if(target===root) observers.push({callback:this.callback,options});}
};
document.querySelectorAll = selector => selector==='pre.mermaid'
  ? nodes : selector==='pre.mermaid:not([data-processed])' ? nodes.filter(n=>!n.processed) : [];
function diagram(text='flowchart LR\nA[Owned start] --> B[Owned next]') {
  const node={textContent:text,isConnected:true,processed:false,
    removeAttribute(name) {if(name==='data-processed')this.processed=false;}};
  nodes.push(node);return node;
}
let active=0, peak=0, runCount=0, firstRelease=null;
function install({holdFirst=false,failFirst=false}={}) {
  window.mermaid={initialize(config){initialized.push(config);},async run({nodes:batch}){
    active++;peak=Math.max(peak,active);runCount++;
    inputs.push(batch.map(n=>n.textContent));
    try {
      if(holdFirst&&runCount===1)await new Promise(resolve=>{firstRelease=resolve;});
      if(failFirst&&runCount===1)throw new Error('owned render failure');
      for(const n of batch){n.processed=true;n.textContent='rendered SVG labels';}
    }finally{active--;}
  }};
  injected.scripts.at(-1).fire('load');
}
async function changed(values) {
  Object.assign(colors,values);
  return observers[0]?.callback([]);
}
'''


def test_initial_mermaid_config_uses_the_existing_palette_and_font(node_available):
    out = _run_node(_PALETTE + r'''
      diagram();const pending=mod.renderMermaid(document);install();await pending;
      emit({config:initialized.at(-1),observers:observers.length,fetches:injected.scripts.length});
    ''')
    assert out['config']['theme'] == 'base'
    variables = out['config']['themeVariables']
    assert variables['background'] == '#282c34'
    assert variables['primaryColor'] == '#111111'
    assert variables['primaryTextColor'] == '#9cdef2'
    assert variables['primaryBorderColor'] == '#e06c75'
    assert 'Fira Code' in variables['fontFamily']
    assert out['observers'] == out['fetches'] == 1


def test_theme_switch_restores_original_source_before_rendering_again(node_available):
    out = _run_node(_PALETTE + r'''
      const node=diagram(),diagramSource=node.textContent;
      const pending=mod.renderMermaid(document);install();await pending;
      await changed({'--bg':'#f3f5f7','--panel':'#ffffff','--fg':'#26313a','--red':'#b34d5c'});
      emit({source:diagramSource,inputs,theme:initialized.at(-1)?.themeVariables?.primaryColor,fetches:injected.scripts.length});
    ''')
    assert out['inputs'] == [[out['source']], [out['source']]]
    assert out['theme'] == '#ffffff'
    assert out['fetches'] == 1


def test_rapid_theme_switches_wait_for_inflight_render_and_use_the_latest_palette(node_available):
    out = _run_node(_PALETTE + r'''
      const node=diagram(),diagramSource=node.textContent;
      const pending=mod.renderMermaid(document);install({holdFirst:true});
      await new Promise(resolve=>setTimeout(resolve,0));
      const p1=changed({'--panel':'#ffffff'});
      const p2=changed({'--panel':'#161b22'});
      const beforeRelease=runCount;firstRelease();await Promise.all([pending,p1,p2]);
      emit({source:diagramSource,inputs,peak,beforeRelease,runCount,theme:initialized.at(-1)?.themeVariables?.primaryColor});
    ''')
    assert out['beforeRelease'] == out['peak'] == 1
    assert out['runCount'] == 2
    assert out['inputs'] == [[out['source']], [out['source']]]
    assert out['theme'] == '#161b22'


def test_unrelated_root_style_changes_do_not_redraw_diagrams(node_available):
    out = _run_node(_PALETTE + r'''
      diagram();const pending=mod.renderMermaid(document);install();await pending;
      await changed({'--bg-effect-size':'0.5'});
      emit({runCount,initializeCount:initialized.length});
    ''')
    assert out == {'runCount': 1, 'initializeCount': 1}


def test_empty_page_still_does_not_load_mermaid(node_available):
    out = _run_node(_PALETTE + r'''
      await mod.renderMermaid(document);emit({fetches:injected.scripts.length,observers:observers.length});
    ''')
    assert out == {'fetches': 0, 'observers': 0}


def test_failed_render_does_not_poison_the_next_render(node_available):
    out = _run_node(_PALETTE + r'''
      const node=diagram(),diagramSource=node.textContent;
      const pending=mod.renderMermaid(document);install({failFirst:true});await pending;
      await mod.renderMermaid(document);
      emit({runCount,peak,inputs,source:diagramSource});
    ''')
    assert out['runCount'] == 2
    assert out['peak'] == 1
    assert out['inputs'] == [[out['source']], [out['source']]]
