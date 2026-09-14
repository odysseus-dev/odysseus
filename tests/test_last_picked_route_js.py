import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _node_eval(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_last_picked_route_does_not_leak_across_sessions():
    values = _node_eval(
        """
        globalThis.window = globalThis;
        const { setLastPickedRoute, getLastPickedRoute } = await import('./static/js/lastPickedRoute.js');
        setLastPickedRoute('chat-a', { model: 'model-a', endpoint_url: 'http://a', endpoint_id: 'ep-a' });
        setLastPickedRoute('chat-b', { model: 'model-b', endpoint_url: 'http://b', endpoint_id: 'ep-b' });
        const a = getLastPickedRoute('chat-a');
        const b = getLastPickedRoute('chat-b');
        console.log(JSON.stringify({
          a: a && a.model,
          b: b && b.model,
          leakedToA: a && a.model === 'model-b',
        }));
        """
    )
    assert values == {"a": "model-a", "b": "model-b", "leakedToA": False}


def test_last_picked_route_expires_after_ttl():
    values = _node_eval(
        """
        globalThis.window = globalThis;
        const { setLastPickedRoute, getLastPickedRoute } = await import('./static/js/lastPickedRoute.js');
        const rec = setLastPickedRoute('chat-a', { model: 'model-a' });
        const fresh = getLastPickedRoute('chat-a', rec.picked_at + 1000);
        const stale = getLastPickedRoute('chat-a', rec.picked_at + (10 * 60 * 1000));
        console.log(JSON.stringify({
          fresh: !!(fresh && fresh.model === 'model-a'),
          stale: stale === null,
        }));
        """
    )
    assert values == {"fresh": True, "stale": True}


def test_pending_new_chat_pick_is_isolated_from_existing_session():
    values = _node_eval(
        """
        globalThis.window = globalThis;
        const { setLastPickedRoute, getLastPickedRoute } = await import('./static/js/lastPickedRoute.js');
        setLastPickedRoute('chat-a', { model: 'model-a' });
        setLastPickedRoute(null, { model: 'model-pending' });
        const existing = getLastPickedRoute('chat-a');
        const pending = getLastPickedRoute(null);
        console.log(JSON.stringify({
          existing: existing && existing.model,
          pending: pending && pending.model,
        }));
        """
    )
    assert values == {"existing": "model-a", "pending": "model-pending"}


def test_chat_send_path_reads_session_scoped_last_pick():
    chat = (ROOT / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    picker = (ROOT / "static" / "js" / "modelPicker.js").read_text(encoding="utf-8")

    assert "import { getLastPickedRoute } from './lastPickedRoute.js';" in chat
    assert "const lastPicked = getLastPickedRoute(sid);" in chat
    assert "window.__odysseusLastPickedRoute || null" not in chat

    assert "import { setLastPickedRoute } from './lastPickedRoute.js';" in picker
    assert "setLastPickedRoute(_deps.getCurrentSessionId && _deps.getCurrentSessionId(), {" in picker
    assert "window.__odysseusLastPickedRoute = {" not in picker
