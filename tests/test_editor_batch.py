import json
import re
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BATCH_MODULE = (ROOT / "static/js/editor/batch-runner.js").as_uri()
GALLERY = ROOT / "static/js/galleryEditor.js"
ACTIONS_PANEL = ROOT / "static/js/editor/actions-panel.js"
SW = ROOT / "static/sw.js"


def run_node(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_batch_runner_processes_every_document_in_order():
    source = textwrap.dedent(
        f"""
        import {{runBatchOverDocuments}} from {json.dumps(BATCH_MODULE)};
        const seen = [];
        const progress = [];
        const docs = [{{id:'a',name:'A'}},{{id:'b',name:'B'}},{{id:'c',name:'C'}}];
        const results = await runBatchOverDocuments({{
          documents: docs,
          runOne: async (doc, index) => {{ seen.push(doc.id); return index; }},
          onProgress: (done, total, doc) => progress.push([done, total, doc.id]),
        }});
        console.log(JSON.stringify({{seen, progress, results}}));
        """
    )
    data = run_node(source)
    assert data["seen"] == ["a", "b", "c"]
    assert data["results"] == [
        {"id": "a", "name": "A", "ok": True, "output": 0},
        {"id": "b", "name": "B", "ok": True, "output": 1},
        {"id": "c", "name": "C", "ok": True, "output": 2},
    ]
    assert data["progress"] == [[1, 3, "a"], [2, 3, "b"], [3, 3, "c"]]


def test_batch_runner_isolates_a_failing_document_and_continues():
    source = textwrap.dedent(
        f"""
        import {{runBatchOverDocuments}} from {json.dumps(BATCH_MODULE)};
        const docs = [{{id:'a',name:'A'}},{{id:'b',name:'B'}},{{id:'c',name:'C'}}];
        const results = await runBatchOverDocuments({{
          documents: docs,
          runOne: async (doc) => {{ if (doc.id === 'b') throw new Error('boom'); return doc.id; }},
        }});
        console.log(JSON.stringify(results));
        """
    )
    results = run_node(source)
    assert len(results) == 3
    assert results[0]["ok"] is True and results[0]["output"] == "a"
    assert results[1]["ok"] is False and "boom" in results[1]["error"]
    assert results[2]["ok"] is True and results[2]["output"] == "c"


def test_batch_runner_tolerates_an_empty_document_list():
    source = textwrap.dedent(
        f"""
        import {{runBatchOverDocuments}} from {json.dumps(BATCH_MODULE)};
        const results = await runBatchOverDocuments({{ documents: [], runOne: async () => 1 }});
        console.log(JSON.stringify(results));
        """
    )
    assert run_node(source) == []


def test_gallery_editor_exposes_the_batch_documents_op():
    src = GALLERY.read_text()
    assert "'actions.batchDocuments':" in src
    assert "import { runBatchOverDocuments } from './editor/batch-runner.js';" in src
    assert "import { createActionsPanel, ACTION_COMMANDS } from './editor/actions-panel.js';" in src
    assert "ACTION_COMMANDS.map(([id, , sel]) => [id, sel])" in src
    assert "runBatchOverDocuments({ documents: _openDocuments.map((d) => ({ id: d.id, name: d.name }))" in src
    assert "await invoke('document.switch', { id: doc.id });" in src
    assert "JSON.parse(localStorage.getItem('ge-actions') || '{}')[name]" in src


def test_actions_panel_has_a_batch_docs_button():
    src = ACTIONS_PANEL.read_text()
    assert "const batchDocsButton = el('button', 'ge-btn ge-btn-sm', 'Batch docs');" in src
    assert "batchRow.append(batchSelect, batchButton, batchDocsButton);" in src
    assert "window.geEditorControl?.invoke?.('actions.batchDocuments', { name })" in src
    assert "batchDocsButton.disabled = true;" in src
    assert "batchDocsButton.disabled = false;" in src


def test_service_worker_precaches_the_batch_runner():
    src = SW.read_text()
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", src)
    assert "'/static/js/editor/batch-runner.js'," in src
