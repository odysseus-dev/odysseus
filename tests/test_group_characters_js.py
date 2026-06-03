"""Regression for issue #1656 — custom personas couldn't be added to a group:
only the just-edited "custom" persona showed up in the group character picker.

Root cause in static/js/group.js `_getCharacterList`, three compounding bugs:
  1. it read the templates response as `data.templates`, but
     GET /api/presets/templates returns a BARE array (preset_manager
     .get_user_templates()), so user templates never loaded;
  2. it gated user templates on `t.isCharacter`, but the save-as-template flow
     (presets.js) never sets that flag;
  3. it read the prompt from `t.prompt`, but templates store `system_prompt`.

The merge now lives in a pure module (static/js/groupCharacters.js) so it can be
executed under node — group.js itself pulls in browser-only modules. Same
approach as tests/test_compare_js.py.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def _run_node(script: str) -> dict:
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"node failed:\n{res.stderr}")
    out_lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not out_lines:
        raise AssertionError("node produced no stdout")
    return json.loads(out_lines[-1])


def test_all_saved_personas_are_selectable(node_available):
    """User-saved templates (no isCharacter flag, prompt in system_prompt) must
    ALL appear — the #1656 bug surfaced only the single custom slot."""
    script = textwrap.dedent("""
        const { mergeGroupCharacters } = await import('./static/js/groupCharacters.js');
        const builtins = [
          { id: 'analyst', name: 'Analyst', prompt: 'be analytical', isCharacter: true },
          { id: 'noncharacter', name: 'Plain Preset' },  // not a character — excluded
        ];
        const customPreset = { character_name: 'Just Made', system_prompt: 'newest persona' };
        // As returned by GET /api/presets/templates — a bare array, no isCharacter:
        const userTemplates = [
          { id: 't1', name: 'Pirate', system_prompt: 'arr' },
          { id: 't2', name: 'Poet', system_prompt: 'in verse' },
          { id: 't3', name: 'Coach', system_prompt: 'motivate' },
        ];
        const out = mergeGroupCharacters(builtins, customPreset, userTemplates);
        console.log(JSON.stringify({
          names: out.map(c => c.name),
          ids: out.map(c => c.id),
          pirate_prompt: (out.find(c => c.id === 't1') || {}).prompt,
          has_plain_preset: out.some(c => c.id === 'noncharacter'),
        }));
    """)
    out = _run_node(script)
    # Built-in character + custom slot + ALL THREE user personas:
    assert out["names"] == ["Analyst", "Just Made", "Pirate", "Poet", "Coach"]
    assert out["pirate_prompt"] == "arr", "prompt must come from system_prompt"
    assert out["has_plain_preset"] is False, "non-character built-ins stay excluded"


def test_dedup_by_id_and_empty_inputs(node_available):
    """De-dupe by id (a user template id matching a built-in doesn't double up),
    and tolerate missing/empty sources."""
    script = textwrap.dedent("""
        const { mergeGroupCharacters } = await import('./static/js/groupCharacters.js');
        const builtins = [{ id: 'dup', name: 'Builtin Dup', prompt: 'x', isCharacter: true }];
        const userTemplates = [
          { id: 'dup', name: 'Should Not Duplicate', system_prompt: 'y' },
          { id: 'u1', name: 'Unique', system_prompt: 'z' },
        ];
        const merged = mergeGroupCharacters(builtins, null, userTemplates);
        const empty = mergeGroupCharacters(null, null, null);
        console.log(JSON.stringify({
          dup_count: merged.filter(c => c.id === 'dup').length,
          ids: merged.map(c => c.id),
          empty_len: empty.length,
        }));
    """)
    out = _run_node(script)
    assert out["dup_count"] == 1, "must not duplicate an id already present"
    assert out["ids"] == ["dup", "u1"]
    assert out["empty_len"] == 0
