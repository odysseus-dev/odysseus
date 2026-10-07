import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).parents[1]


def test_same_local_model_keeps_distinct_endpoint_keys():
    source = (ROOT / 'static/js/modelPicker.js').read_text()
    helper = re.search(r'function _pickerModelKey\(m\) \{.*?\n\}', source, re.S).group()
    result = subprocess.run(['node', '-e', helper + '''
      const a = _pickerModelKey({mid:'same-model',endpointId:'normal'});
      const b = _pickerModelKey({mid:'same-model',endpointId:'preview'});
      if (a === b) throw Error('routes collapsed');
      if (a !== _pickerModelKey({mid:'same-model',endpointId:'normal'})) throw Error('unstable key');
      console.log(JSON.stringify([a,b]));
    '''], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == ['normal::same-model', 'preview::same-model']
    assert 'const seenKey = _pickerModelKey(' in source
    assert ': mid;' not in source


def test_selected_route_is_visible_and_last_pick_is_session_scoped():
    picker = (ROOT / 'static/js/modelPicker.js').read_text()
    chat = (ROOT / 'static/js/chat.js').read_text()
    assert 's?.endpoint_name || selectedEndpoint?.endpoint_name' in picker
    assert 'label.appendChild(document.createTextNode(displayName))' in picker
    send = chat.split('const selectedRouteForSend =', 1)[1].split('})();', 1)[0]
    assert '(lastPicked.session_id || null) === (sessionModule.getCurrentSessionId?.() || null)' in send
