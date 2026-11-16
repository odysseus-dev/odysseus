"""Node-driven tests for the shared provider device-flow runner."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "providerDeviceFlow.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")


def _run_node(script: str):
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_copilot_success_uses_complete_verification_uri():
    js = f"""
      import {{ runProviderDeviceFlow }} from '{_HELPER.as_posix()}';
      const calls = [];
      const opened = [];
      let polls = 0;
      const response = (ok, status, payload) => ({{ ok, status, async json() {{ return payload; }} }});
      const fetchImpl = async (url) => {{
        calls.push(url);
        if (url.endsWith('/device/start')) {{
          return response(true, 200, {{
            poll_id: 'poll-1',
            user_code: 'GH-CODE',
            verification_uri: 'https://github.com/login/device',
            verification_uri_complete: 'https://github.com/login/device?user_code=GH-CODE',
            interval: 2,
            expires_in: 30,
          }});
        }}
        polls += 1;
        return response(true, 200, polls === 1
          ? {{ status: 'pending' }}
          : {{ status: 'authorized', endpoint: {{ id: 'ep1', models: ['gpt-4o'] }} }}
        );
      }};
      const result = await runProviderDeviceFlow('copilot', {{
        fetchImpl,
        openWindow: (url) => opened.push(url),
        sleep: async () => {{}},
        now: () => 0,
      }});
      console.log(JSON.stringify({{ result, calls, opened }}));
    """
    out = _run_node(js)
    assert out["result"]["status"] == "authorized"
    assert out["result"]["endpoint"]["id"] == "ep1"
    assert out["opened"] == ["https://github.com/login/device?user_code=GH-CODE"]
    assert out["calls"] == ["/api/copilot/device/start", "/api/copilot/device/poll", "/api/copilot/device/poll"]


def test_chatgpt_success_uses_plain_verification_uri():
    js = f"""
      import {{ runProviderDeviceFlow }} from '{_HELPER.as_posix()}';
      const opened = [];
      const response = (ok, status, payload) => ({{ ok, status, async json() {{ return payload; }} }});
      const fetchImpl = async (url) => {{
        if (url.endsWith('/device/start')) {{
          return response(true, 200, {{
            poll_id: 'poll-1',
            user_code: 'OA-CODE',
            verification_uri: 'https://auth.openai.com/codex/device',
            interval: 2,
            expires_in: 30,
          }});
        }}
        return response(true, 200, {{ status: 'authorized', endpoint: {{ id: 'chatgpt', models: ['gpt-5.5'] }} }});
      }};
      const result = await runProviderDeviceFlow('chatgpt-subscription', {{
        fetchImpl,
        openWindow: (url) => opened.push(url),
        sleep: async () => {{}},
        now: () => 0,
      }});
      console.log(JSON.stringify({{ result, opened }}));
    """
    out = _run_node(js)
    assert out["result"]["status"] == "authorized"
    assert out["opened"] == ["https://auth.openai.com/codex/device"]


def test_start_errors_surface_backend_detail():
    js = f"""
      import {{ runProviderDeviceFlow }} from '{_HELPER.as_posix()}';
      const response = (ok, status, payload) => ({{ ok, status, async json() {{ return payload; }} }});
      try {{
        await runProviderDeviceFlow('copilot', {{
          fetchImpl: async () => response(false, 502, {{ detail: 'GitHub device-code request failed: upstream down' }}),
          openWindow: () => {{}},
          sleep: async () => {{}},
          now: () => 0,
        }});
      }} catch (err) {{
        console.log(JSON.stringify({{ message: err.message }}));
      }}
    """
    out = _run_node(js)
    assert out["message"] == "GitHub device-code request failed: upstream down"


def test_thrown_fetch_errors_are_preserved():
    js = f"""
      import {{ runProviderDeviceFlow }} from '{_HELPER.as_posix()}';
      try {{
        await runProviderDeviceFlow('chatgpt-subscription', {{
          fetchImpl: async () => {{ throw new Error('network offline'); }},
          openWindow: () => {{}},
          sleep: async () => {{}},
          now: () => 0,
        }});
      }} catch (err) {{
        console.log(JSON.stringify({{ message: err.message }}));
      }}
    """
    out = _run_node(js)
    assert out["message"] == "network offline"


def test_expired_flow_returns_expired_status():
    js = f"""
      import {{ runProviderDeviceFlow }} from '{_HELPER.as_posix()}';
      let currentTime = 0;
      const response = (ok, status, payload) => ({{ ok, status, async json() {{ return payload; }} }});
      const result = await runProviderDeviceFlow('copilot', {{
        fetchImpl: async (url) => url.endsWith('/device/start')
          ? response(true, 200, {{
              poll_id: 'poll-1',
              user_code: 'GH-CODE',
              verification_uri: 'https://github.com/login/device',
              interval: 2,
              expires_in: 1,
            }})
          : response(true, 200, {{ status: 'pending' }}),
        openWindow: () => {{}},
        sleep: async () => {{ currentTime += 2000; }},
        now: () => currentTime,
      }});
      console.log(JSON.stringify(result));
    """
    out = _run_node(js)
    assert out == {"status": "expired"}


def test_microsoft_mail_sends_account_id_and_uses_plain_verification_uri():
    js = f"""
      import {{ runProviderDeviceFlow }} from '{_HELPER.as_posix()}';
      const calls = [];
      const opened = [];
      const response = (ok, status, payload) => ({{ ok, status, async json() {{ return payload; }} }});
      const fetchImpl = async (url, opts) => {{
        calls.push([url, opts.body.get('account_id'), opts.body.get('poll_id')]);
        if (url.endsWith('/device/start')) {{
          // Microsoft returns no code-prefilled verification_uri_complete.
          return response(true, 200, {{
            poll_id: 'poll-ms',
            user_code: 'MS-CODE',
            verification_uri: 'https://login.microsoft.com/device',
            interval: 5,
            expires_in: 900,
          }});
        }}
        return response(true, 200, {{ status: 'authorized', endpoint: {{ account_id: 'acc-1', email: 'me@contoso.com' }} }});
      }};
      const formData = new FormData();
      formData.append('account_id', 'acc-1');
      const result = await runProviderDeviceFlow('microsoft-mail', {{
        fetchImpl,
        formData,
        openWindow: (url) => opened.push(url),
        sleep: async () => {{}},
        now: () => 0,
      }});
      console.log(JSON.stringify({{ result, calls, opened }}));
    """
    out = _run_node(js)
    assert out["result"] == {"status": "authorized", "endpoint": {"account_id": "acc-1", "email": "me@contoso.com"}}
    assert out["calls"] == [
        ["/api/email/oauth/microsoft/device/start", "acc-1", None],
        ["/api/email/oauth/microsoft/device/poll", None, "poll-ms"],
    ]
    assert out["opened"] == ["https://login.microsoft.com/device"]


def test_abort_signal_stops_polling_and_notifies_cancel():
    js = f"""
      import {{ runProviderDeviceFlow }} from '{_HELPER.as_posix()}';
      const calls = [];
      const response = (ok, status, payload) => ({{ ok, status, async json() {{ return payload; }} }});
      const controller = new AbortController();
      const fetchImpl = async (url, opts) => {{
        calls.push(url);
        if (url.endsWith('/device/start')) {{
          return response(true, 200, {{
            poll_id: 'poll-abort-test',
            user_code: 'ABORT-CODE',
            verification_uri: 'https://microsoft.com/devicelogin',
            interval: 5,
            expires_in: 900,
          }});
        }}
        if (url.endsWith('/device/cancel')) {{
          return response(true, 200, {{ status: 'cancelled' }});
        }}
        return response(true, 200, {{ status: 'pending' }});
      }};
      const result = await runProviderDeviceFlow('microsoft-mail', {{
        fetchImpl,
        signal: controller.signal,
        openWindow: () => {{}},
        onWaiting: () => {{
          controller.abort();
        }},
        sleep: async () => {{}},
        now: () => 0,
      }});
      console.log(JSON.stringify({{ result, calls }}));
    """
    out = _run_node(js)
    assert out["result"] == {"status": "cancelled"}
    assert "/api/email/oauth/microsoft/device/cancel" in out["calls"]


def test_pre_aborted_signal_does_not_fetch():
    js = f"""
      import {{ runProviderDeviceFlow }} from '{_HELPER.as_posix()}';
      const calls = [];
      const controller = new AbortController();
      controller.abort();
      const fetchImpl = async (url) => {{
        calls.push(url);
        return {{ ok: true, status: 200, async json() {{ return {{}}; }} }};
      }};
      const result = await runProviderDeviceFlow('copilot', {{
        fetchImpl,
        signal: controller.signal,
        openWindow: () => {{}},
        sleep: async () => {{}},
        now: () => 0,
      }});
      console.log(JSON.stringify({{ result, calls }}));
    """
    out = _run_node(js)
    assert out["result"] == {"status": "cancelled"}
    assert out["calls"] == []


def test_abort_during_sleep_wakes_and_cancels():
    js = f"""
      import {{ runProviderDeviceFlow }} from '{_HELPER.as_posix()}';
      const calls = [];
      const response = (ok, status, payload) => ({{ ok, status, async json() {{ return payload; }} }});
      const controller = new AbortController();
      const fetchImpl = async (url) => {{
        calls.push(url);
        if (url.endsWith('/device/start')) {{
          return response(true, 200, {{
            poll_id: 'poll-sleep-test',
            user_code: 'CODE',
            verification_uri: 'https://microsoft.com/devicelogin',
            interval: 5,
            expires_in: 900,
          }});
        }}
        return response(true, 200, {{ status: 'cancelled' }});
      }};
      // Abort after 20ms while default sleep is waiting
      setTimeout(() => controller.abort(), 20);
      const start = Date.now();
      const result = await runProviderDeviceFlow('microsoft-mail', {{
        fetchImpl,
        signal: controller.signal,
        openWindow: () => {{}},
        now: () => 0,
      }});
      const elapsed = Date.now() - start;
      console.log(JSON.stringify({{ result, elapsed, calls }}));
    """
    out = _run_node(js)
    assert out["result"] == {"status": "cancelled"}
    assert out["elapsed"] < 3000
    assert "/api/email/oauth/microsoft/device/cancel" in out["calls"]
