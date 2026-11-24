import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.helpers.js_modules import js_function_source
from tests.helpers.stylesheets import app_css


_REPO = Path(__file__).resolve().parents[1]


def test_panel_opacity_tokens_markup_and_css_rules():
    tokens_css = (_REPO / "static" / "css" / "00-tokens.css").read_text(encoding="utf-8")
    assert "--panel-opacity: 1;" in tokens_css

    index_html = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="theme-panel-opacity"' in index_html
    assert 'min="10"' in index_html
    assert 'max="100"' in index_html
    assert 'id="theme-panel-opacity-val"' in index_html

    css = app_css()
    assert "body.theme-custom-panel-opacity #sidebar" in css
    assert "body.theme-custom-panel-opacity .modal-content" in css
    assert "body.theme-custom-panel-opacity .cp-popover" in css
    assert "body.theme-custom-panel-opacity .dropdown" in css
    assert "color-mix(in srgb, var(--panel, var(--bg)) calc(var(--panel-opacity, 1) * 100%), transparent)" in css


def test_apply_panel_opacity_and_persistence_in_theme_js():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")

    theme_src = (_REPO / "static" / "js" / "theme.js").read_text(encoding="utf-8")
    apply_fn_src = js_function_source("applyPanelOpacity", theme_src)
    save_fn_src = js_function_source("save", theme_src)
    save_custom_fn_src = js_function_source("saveCustomTheme", theme_src)

    script = f"""
      const DEFAULT_FONT = 'mono';
      const DEFAULT_DENSITY = 'comfortable';
      const DEFAULT_PANEL_OPACITY = 1;
      const MAX_CUSTOM_THEMES = 8;
      const LS_KEY = 'odysseus-theme';
      const CUSTOM_THEMES_KEY = 'odysseus-custom-themes';
      const store = new Map();

      function normalizeFont(font) {{
        return !font || font === 'gohu' || font === 'GohuFont' ? DEFAULT_FONT : font;
      }}
      const Storage = {{
        getJSON(k, fallback) {{ return store.has(k) ? JSON.parse(store.get(k)) : fallback; }},
        setJSON(k, v) {{ store.set(k, JSON.stringify(v)); }},
      }};
      function _loadCustomThemes() {{ return Storage.getJSON(CUSTOM_THEMES_KEY, {{}}); }}
      function _saveCustomThemes(obj) {{ Storage.setJSON(CUSTOM_THEMES_KEY, obj); }}
      function _syncCustomThemesToServer() {{}}
      function _syncToServer() {{}}
      function initThemeUI() {{}}

      const rootVars = new Map();
      const bodyClasses = new Set();
      const sliderEl = {{ value: '100' }};
      const readoutEl = {{ textContent: '100%' }};

      const document = {{
        documentElement: {{
          style: {{
            setProperty(k, v) {{ rootVars.set(k, v); }},
          }},
        }},
        body: {{
          classList: {{
            toggle(cls, force) {{
              if (force) bodyClasses.add(cls);
              else bodyClasses.delete(cls);
            }},
          }},
        }},
        getElementById(id) {{
          if (id === 'theme-panel-opacity') return sliderEl;
          if (id === 'theme-panel-opacity-val') return readoutEl;
          return null;
        }},
      }};

      {apply_fn_src}
      {save_fn_src}
      {save_custom_fn_src}

      applyPanelOpacity(0.45);
      const midState = {{
        cssVar: rootVars.get('--panel-opacity'),
        hasClass: bodyClasses.has('theme-custom-panel-opacity'),
        slider: sliderEl.value,
        readout: readoutEl.textContent,
      }};

      applyPanelOpacity(0.01);
      const clampedLow = rootVars.get('--panel-opacity');

      applyPanelOpacity(1.5);
      const resetHigh = {{
        cssVar: rootVars.get('--panel-opacity'),
        hasClass: bodyClasses.has('theme-custom-panel-opacity'),
        slider: sliderEl.value,
        readout: readoutEl.textContent,
      }};

      const colors = {{ bg: '#10121a', fg: '#9bdde0', panel: '#171a26', border: '#514b78', red: '#d45aa8' }};
      save('cyberpunk', colors, {{ panelOpacity: 0.6 }});
      saveCustomTheme('glass-cyber', colors, {{ frosted: true, panelOpacity: 0.35 }});

      console.log(JSON.stringify({{
        midState,
        clampedLow,
        resetHigh,
        savedTheme: Storage.getJSON(LS_KEY, null),
        savedCustom: Storage.getJSON(CUSTOM_THEMES_KEY, null),
      }}));
    """

    proc = subprocess.run(
        [node, "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip())

    assert out["midState"] == {
        "cssVar": "0.45",
        "hasClass": True,
        "slider": "45",
        "readout": "45%",
    }
    assert out["clampedLow"] == "0.1"
    assert out["resetHigh"] == {
        "cssVar": "1",
        "hasClass": False,
        "slider": "100",
        "readout": "100%",
    }
    assert out["savedTheme"]["panelOpacity"] == 0.6
    assert out["savedCustom"]["glass-cyber"]["panelOpacity"] == 0.35
    assert out["savedCustom"]["glass-cyber"]["frosted"] is True
