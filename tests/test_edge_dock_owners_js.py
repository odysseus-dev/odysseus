"""Node-driven coverage for the shared edge-dock owner model."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "static" / "js" / "edgeDockOwners.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _node_eval(source: str):
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=source,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_left_owner_discovery_includes_legacy_email_and_rejects_nested_markup():
    values = _node_eval(
        textwrap.dedent(
            f"""
            import {{ dockOwnersForSide }} from '{HELPER.as_uri()}';

            const body = {{ tagName: 'BODY' }};
            const classes = (...names) => ({{
              contains(name) {{ return names.includes(name); }},
            }});
            const makeSurface = (id, names, parent = body) => {{
              const node = {{
                id,
                parentElement: parent,
                isConnected: true,
                classList: classes(...names),
                style: {{}},
                computed: {{ display: 'block', visibility: 'visible' }},
                matches(selector) {{
                  if (selector === '.notes-pane') return names.includes('notes-pane');
                  if (!selector.includes('body > .modal')) return false;
                  return parent === body && (
                    names.includes('modal')
                    || names.includes('research-overlay')
                    || names.includes('notes-pane-backdrop')
                  );
                }},
              }};
              node.content = {{
                isConnected: true,
                classList: classes(),
                style: {{}},
                computed: {{ display: 'block', visibility: 'visible' }},
                getBoundingClientRect() {{ return {{ width: 420, height: 700 }}; }},
              }};
              return node;
            }};

            const modern = makeSurface('calendar-modal', ['modal', 'modal-left-docked']);
            const legacy = makeSurface('email-lib-modal', ['modal', 'email-snap-left']);
            const emailModal = makeSurface('outer-email', ['modal']);
            const injected = makeSurface('sanitized-descendant', ['modal', 'modal-left-docked'], emailModal);
            let selector = '';
            const root = {{
              querySelectorAll(value) {{
                selector = value;
                return [modern, legacy, injected];
              }},
            }};
            const owners = dockOwnersForSide('left', {{
              root,
              resolveContent: (owner) => owner.content,
              getStyle: (element) => element.computed,
            }});
            console.log(JSON.stringify({{ selector, ids: owners.map((owner) => owner.id) }}));
            """
        )
    )

    assert values == {
        "selector": ".modal-left-docked, .email-snap-left",
        "ids": ["calendar-modal", "email-lib-modal"],
    }


def test_owner_discovery_counts_only_visible_usable_application_surfaces():
    values = _node_eval(
        textwrap.dedent(
            f"""
            import {{ dockOwnersForSide }} from '{HELPER.as_uri()}';

            const body = {{ tagName: 'BODY' }};
            const classes = (...names) => ({{ contains: (name) => names.includes(name) }});
            const makeOwner = (id, ownerClasses = [], ownerStyle = {{}}, contentStyle = {{}}, rect = {{ width: 400, height: 600 }}) => {{
              const owner = {{
                id,
                parentElement: body,
                isConnected: true,
                classList: classes('modal', 'modal-right-docked', ...ownerClasses),
                computed: {{ display: 'block', visibility: 'visible', ...ownerStyle }},
                matches(selector) {{
                  if (selector === '.notes-pane') return false;
                  return selector.includes('body > .modal');
                }},
              }};
              owner.content = {{
                isConnected: true,
                classList: classes(),
                computed: {{ display: 'block', visibility: 'visible', ...contentStyle }},
                getBoundingClientRect() {{ return rect; }},
              }};
              return owner;
            }};

            const candidates = [
              makeOwner('visible'),
              makeOwner('hidden-class', ['hidden']),
              makeOwner('minimized', ['modal-minimized']),
              makeOwner('display-none', [], {{ display: 'none' }}),
              makeOwner('visibility-hidden', [], {{ visibility: 'hidden' }}),
              makeOwner('content-hidden', [], {{}}, {{ visibility: 'hidden' }}),
              makeOwner('zero-rect', [], {{}}, {{}}, {{ width: 0, height: 600 }}),
            ];
            const owners = dockOwnersForSide('right', {{
              root: {{ querySelectorAll() {{ return candidates; }} }},
              resolveContent: (owner) => owner.content,
              getStyle: (element) => element.computed,
            }});
            console.log(JSON.stringify(owners.map((owner) => owner.id)));
            """
        )
    )

    assert values == ["visible"]


def test_notes_owner_is_accepted_only_through_its_top_level_backdrop():
    values = _node_eval(
        textwrap.dedent(
            f"""
            import {{ isApplicationDockOwner }} from '{HELPER.as_uri()}';
            const body = {{ tagName: 'BODY' }};
            const backdrop = {{
              parentElement: body,
              matches(selector) {{ return selector === 'body > .notes-pane-backdrop'; }},
            }};
            const nestedHost = {{
              parentElement: {{ tagName: 'DIV' }},
              matches() {{ return false; }},
            }};
            const topLevelModal = {{
              parentElement: body,
              matches(selector) {{ return selector.includes('body > .modal'); }},
            }};
            const makePane = (parent) => ({{
              parentElement: parent,
              matches(selector) {{ return selector === '.notes-pane'; }},
            }});
            console.log(JSON.stringify({{
              topLevel: isApplicationDockOwner(makePane(backdrop)),
              nested: isApplicationDockOwner(makePane(nestedHost)),
              spoofedInsideModal: isApplicationDockOwner(makePane(topLevelModal)),
            }}));
            """
        )
    )

    assert values == {
        "topLevel": True,
        "nested": False,
        "spoofedInsideModal": False,
    }
