from pathlib import Path
import subprocess


def test_shared_picker_exports_editor_color_contract():
    root = Path(__file__).resolve().parents[1]
    script = r'''
      import assert from 'node:assert/strict';
      import { pathToFileURL } from 'node:url';
      const store = new Map();
      globalThis.localStorage = { getItem: key => store.get(key), setItem: (key, value) => store.set(key, value) };
      globalThis.HTMLInputElement = class {};
      const picker = await import(pathToFileURL(process.argv[1]));
      store.set('odysseus-recent-colors', JSON.stringify(['<img src=x onerror=alert(1)>', 123, '#ABCDEF']));
      assert.deepEqual(picker.getRecentColors(), ['#ABCDEF']);
      picker.recordRecentColor('#abcdef');
      picker.recordRecentColor('#ff0000');
      assert.deepEqual(picker.getRecentColors(), ['#ff0000', '#abcdef']);
      const suggestions = picker.suggestColorsFor('#ff0000');
      assert.equal(suggestions.length, 5);
      assert.equal(suggestions[0].hex, '#00ffff');
      assert.ok(suggestions.every(s => /^#[0-9a-f]{6}$/.test(s.hex)));
      store.set('odysseus-recent-colors', '{}');
      assert.deepEqual(picker.getRecentColors(), []);
    '''
    result = subprocess.run(['node', '--input-type=module', '-e', script,
                             str(root / 'static/js/colorPicker.js')],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
