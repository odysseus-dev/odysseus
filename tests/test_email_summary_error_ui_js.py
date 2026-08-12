import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parent.parent
_UTILS = (_REPO / "static" / "js" / "emailLibrary" / "utils.js").as_posix()
_HAS_NODE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def test_email_summary_renderer_ignores_untrusted_provider_error_text():
    secret = (
        "endpoint=https://private.example.internal/v1 provider=ollama "
        "model=private-model response_body=private-response "
        "Authorization: Bearer token-secret-value"
    )
    script = f"""
      import {{ _renderEmailSummaryError }} from '{_UTILS}';
      const host = {{
        ownerDocument: {{
          createElement() {{
            return {{
              style: {{}},
              textContent: '',
              attrs: {{}},
              setAttribute(name, value) {{ this.attrs[name] = value; }},
            }};
          }},
        }},
        replaceChildren(node) {{ this.child = node; }},
      }};
      _renderEmailSummaryError(host, {{
        error_code: 'email_summary_unavailable',
        error: {json.dumps(secret)},
      }});
      console.log(JSON.stringify({{
        text: host.child.textContent,
        color: host.child.style.color,
        key: host.child.attrs['data-i18n'],
      }}));
    """

    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    rendered = json.loads(proc.stdout)
    assert rendered == {
        "text": "Failed to summarize",
        "color": "var(--red)",
        "key": "ui.failed.to.summarize",
    }
    assert secret not in proc.stdout


def test_email_summary_renderer_uses_semantic_translation_keys():
    script = f"""
      import {{ _renderEmailSummaryError }} from '{_UTILS}';
      const translations = {{
        'ui.no.email.body.to.summarize': 'Kein E-Mail-Text zum Zusammenfassen',
        'ui.no.model.configured.for.email.summaries': 'Kein Modell konfiguriert',
        'ui.the.model.returned.an.empty.summary': 'Leere Zusammenfassung',
      }};
      globalThis.window = {{
        odysseusI18n: {{ t: (key) => translations[key] || key }},
      }};
      function render(error_code) {{
        const host = {{
          ownerDocument: {{
            createElement() {{
              return {{
                style: {{}},
                textContent: '',
                attrs: {{}},
                setAttribute(name, value) {{ this.attrs[name] = value; }},
              }};
            }},
          }},
          replaceChildren(node) {{ this.child = node; }},
        }};
        _renderEmailSummaryError(host, {{ error_code }});
        return {{
          text: host.child.textContent,
          key: host.child.attrs['data-i18n'],
        }};
      }}
      console.log(JSON.stringify([
        render('email_summary_missing_body'),
        render('email_summary_not_configured'),
        render('email_summary_empty'),
      ]));
    """

    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [
        {
            "text": "Kein E-Mail-Text zum Zusammenfassen",
            "key": "ui.no.email.body.to.summarize",
        },
        {
            "text": "Kein Modell konfiguriert",
            "key": "ui.no.model.configured.for.email.summaries",
        },
        {
            "text": "Leere Zusammenfassung",
            "key": "ui.the.model.returned.an.empty.summary",
        },
    ]
