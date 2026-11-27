"""Regression test for #6753: Vision label on user attachment while AI reply streams."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[1]
_CHAT_JS = _REPO / "static" / "js" / "chat.js"


def test_attachments_handler_does_not_use_msg_user_last_of_type():
    source = _CHAT_JS.read_text(encoding="utf-8")
    assert ".msg-user:last-of-type" not in source
    assert "querySelectorAll('#chat-history .msg-user')" in source


def test_vision_label_attaches_to_last_user_bubble_while_streaming():
    if not shutil.which("node"):
        pytest.skip("node is not installed")

    source = _CHAT_JS.read_text(encoding="utf-8")
    marker = "} else if (json.type === 'attachments') {"
    start = source.index(marker)
    end = source.index("// Caption / OCR text is no longer rendered", start)
    snippet = source[start + len("} else "):end] + "\n}\n}\n"

    script = f"""
      class FakeElement {{
        constructor(tag, classes = [], attrs = {{}}) {{
          this.tagName = tag.toUpperCase();
          this.classList = new Set(classes);
          this.dataset = {{ ...attrs }};
          this.children = [];
          this.textContent = '';
          this.style = {{}};
          this.onclick = null;
        }}
        get className() {{ return Array.from(this.classList).join(' '); }}
        set className(v) {{ this.classList = new Set(String(v).split(/\\s+/).filter(Boolean)); }}
        appendChild(child) {{ this.children.push(child); return child; }}
        insertBefore(child, ref) {{
          const idx = this.children.indexOf(ref);
          if (idx === -1) this.children.push(child);
          else this.children.splice(idx, 0, child);
          return child;
        }}
        replaceWith(other) {{}}
        querySelector(sel) {{
          return this.querySelectorAll(sel)[0] || null;
        }}
        querySelectorAll(sel) {{
          const out = [];
          const walk = (node) => {{
            for (const c of node.children) {{
              if (sel === '.attach-cards' && c.classList.has('attach-cards')) out.push(c);
              else if (sel === '.attach-vision-model' && c.classList.has('attach-vision-model')) out.push(c);
              else if (sel === '.attach-image-name' && c.classList.has('attach-image-name')) out.push(c);
              else if (sel.startsWith('[data-file-id="')) {{
                const id = sel.slice('[data-file-id="'.length, -2);
                if (c.dataset.fileId === id) out.push(c);
              }}
              walk(c);
            }}
          }};
          walk(this);
          return out;
        }}
      }}

      const earlierUser = new FakeElement('div', ['msg', 'msg-user']);
      const lastUser = new FakeElement('div', ['msg', 'msg-user']);
      const attachCards = new FakeElement('div', ['attach-cards']);
      const preview = new FakeElement('div', ['attach-image-preview'], {{ fileId: 'file-123' }});
      const fileName = new FakeElement('div', ['attach-image-name']);
      fileName.textContent = 'photo.png';
      preview.appendChild(fileName);
      attachCards.appendChild(preview);
      lastUser.appendChild(attachCards);

      // Streaming AI reply div sits after the last .msg-user in #chat-history
      const streamingAi = new FakeElement('div', ['msg', 'msg-ai', 'streaming']);
      const chatHistory = [earlierUser, lastUser, streamingAi];

      const document = {{
        createElement: (tag) => new FakeElement(tag),
        querySelector: (sel) => {{
          if (sel === '#chat-history .msg-user:last-of-type') return null;
          return null;
        }},
        querySelectorAll: (sel) => {{
          if (sel === '#chat-history .msg-user') {{
            return chatHistory.filter((el) => el.classList.has('msg-user'));
          }}
          return [];
        }},
      }};

      const API_BASE = '';
      const _isBg = false;
      const json = {{
        type: 'attachments',
        data: [{{ id: 'file-123', name: 'photo.png', mime: 'image/png', vision_model: 'Qwen/Qwen2.5-VL-7B' }}],
      }};

      for (let _once = 0; _once < 1; _once++) {{
        {snippet}
      }}

      const labelEl = preview.querySelector('.attach-vision-model');
      console.log(JSON.stringify({{
        hasLabel: Boolean(labelEl),
        labelText: labelEl ? labelEl.textContent : null,
        childOrder: preview.children.map((c) => c.className),
      }}));
    """

    result = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=_REPO,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "hasLabel": True,
        "labelText": "Vision: Qwen2.5-VL-7B",
        "childOrder": ["attach-vision-model", "attach-image-name"],
    }
