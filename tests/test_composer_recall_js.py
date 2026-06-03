import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "composerRecall.js"
_APP = _REPO / "static" / "app.js"
_HAS_NODE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def _run(js: str):
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_arrowup_empty_composer_recalls_last_user_message():
    result = _run(
        f"""
        import {{ recallLastUserMessageFromHistory }} from '{_HELPER.as_uri()}';

        const dispatched = [];
        const input = {{
          value: '',
          selection: null,
          setSelectionRange(start, end) {{ this.selection = [start, end]; }},
          dispatchEvent(event) {{ dispatched.push({{ type: event.type, bubbles: event.bubbles }}); }},
        }};
        const history = {{
          querySelectorAll(selector) {{
            if (selector !== '.msg-user') throw new Error('unexpected selector: ' + selector);
            return [
              {{ dataset: {{ raw: 'first prompt' }} }},
              {{ dataset: {{ raw: 'last prompt\\nwith newline' }} }},
            ];
          }},
        }};
        let prevented = false;
        const handled = recallLastUserMessageFromHistory(input, history, {{
          key: 'ArrowUp',
          isComposing: false,
          preventDefault() {{ prevented = true; }},
        }});

        console.log(JSON.stringify({{
          handled,
          prevented,
          value: input.value,
          selection: input.selection,
          dispatched,
        }}));
        """
    )

    assert result == {
        "handled": True,
        "prevented": True,
        "value": "last prompt\nwith newline",
        "selection": [24, 24],
        "dispatched": [{"type": "input", "bubbles": True}],
    }


def test_arrowup_recall_leaves_non_empty_composer_alone():
    result = _run(
        f"""
        import {{ recallLastUserMessageFromHistory }} from '{_HELPER.as_uri()}';

        const input = {{
          value: 'already typing',
          selection: null,
          setSelectionRange(start, end) {{ this.selection = [start, end]; }},
        }};
        let prevented = false;
        const handled = recallLastUserMessageFromHistory(
          input,
          {{ querySelectorAll() {{ return [{{ dataset: {{ raw: 'last prompt' }} }}]; }} }},
          {{
            key: 'ArrowUp',
            isComposing: false,
            preventDefault() {{ prevented = true; }},
          }}
        );

        console.log(JSON.stringify({{
          handled,
          prevented,
          value: input.value,
          selection: input.selection,
        }}));
        """
    )

    assert result == {
        "handled": False,
        "prevented": False,
        "value": "already typing",
        "selection": None,
    }


@pytest.mark.parametrize(
    "event_patch",
    [
        {"key": "Enter", "isComposing": False},
        {"key": "ArrowUp", "isComposing": True},
        {"key": "ArrowUp", "isComposing": False, "shiftKey": True},
        {"key": "ArrowUp", "isComposing": False, "altKey": True},
        {"key": "ArrowUp", "isComposing": False, "ctrlKey": True},
        {"key": "ArrowUp", "isComposing": False, "metaKey": True},
    ],
)
def test_arrowup_recall_ignores_wrong_key_composition_and_modifiers(event_patch):
    result = _run(
        f"""
        import {{ recallLastUserMessageFromHistory }} from '{_HELPER.as_uri()}';

        const input = {{ value: '' }};
        let prevented = false;
        const event = {{
          ...{json.dumps(event_patch)},
          preventDefault() {{ prevented = true; }},
        }};
        const handled = recallLastUserMessageFromHistory(
          input,
          {{ querySelectorAll() {{ return [{{ dataset: {{ raw: 'last prompt' }} }}]; }} }},
          event
        );

        console.log(JSON.stringify({{
          handled,
          prevented,
          value: input.value,
        }}));
        """
    )

    assert result == {"handled": False, "prevented": False, "value": ""}


def test_arrowup_recall_requires_last_user_dataset_raw():
    result = _run(
        f"""
        import {{ recallLastUserMessageFromHistory }} from '{_HELPER.as_uri()}';

        const input = {{ value: '' }};
        let prevented = false;
        const handled = recallLastUserMessageFromHistory(
          input,
          {{ querySelectorAll() {{ return [{{ dataset: {{ raw: 'first prompt' }} }}, {{ dataset: {{ raw: '' }} }}]; }} }},
          {{
            key: 'ArrowUp',
            isComposing: false,
            preventDefault() {{ prevented = true; }},
          }}
        );

        console.log(JSON.stringify({{
          handled,
          prevented,
          value: input.value,
        }}));
        """
    )

    assert result == {"handled": False, "prevented": False, "value": ""}


def test_app_wires_recall_into_message_keydown():
    body = _APP.read_text(encoding="utf-8")
    assert "import { recallLastUserMessageFromHistory } from './js/composerRecall.js';" in body
    assert "recallLastUserMessageFromHistory(messageInput, el('chat-history'), e)" in body
