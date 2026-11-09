"""Execute the actual DOCX handlers with deferred network responses."""
import subprocess
from pathlib import Path
from tests.helpers.document_source import document_source


def test_docx_responses_do_not_overwrite_new_tabs_or_hidden_previews():
    source = document_source()
    handlers = source.split("  let _docxPreviewRequest = 0;", 1)[1].split("  /** Parse CSV", 1)[0]
    script = r'''
import assert from 'node:assert/strict';
let _docxPreviewRequest = 0;
let activeDocId = 'a';
const original = {language: 'docx', content: 'original'};
const docs = new Map([['a', original], ['b', {content: 'untouched'}]]);
const preview = {style: {}, innerHTML: '', replaceChildren() {this.innerHTML = '';}};
const wrap = {style: {}};
const textarea = {value: 'untouched'};
const document = {getElementById(id) { return id === 'doc-docx-preview' ? preview : id === 'doc-editor-wrap' ? wrap : textarea; }};
const API_BASE = '';
const _syncHeaderActions = () => {};
const _escHtml = String;
const markdownModule = {sanitizeAllowedHtml: x => x};
const _isDocxLang = x => x === 'docx';
let saves = 0;
const saveDocument = async () => {saves++;};
const switchToDoc = () => {};
const uiModule = {};
let respond;
const fetch = () => new Promise(resolve => { respond = () => resolve({ok: true, json: async () => ({html: '<p>converted</p>'})}); });
''' + handlers + r'''
let pending = _convertDocxToRichText();
activeDocId = 'b'; respond(); await pending;
assert.equal(original.content, 'original');
assert.equal(textarea.value, 'untouched');
assert.equal(saves, 0);
activeDocId = 'a';
pending = _convertDocxToRichText();
original.content = 'new edit'; respond(); await pending;
assert.equal(original.content, 'new edit');
assert.equal(saves, 0);
pending = _setDocxPreviewActive(true);
await _setDocxPreviewActive(false);
respond(); await pending;
assert.equal(preview.innerHTML, '');
assert.equal(original._docxPreviewActive, false);
pending = _setDocxPreviewActive(true);
activeDocId = 'b'; preview.innerHTML = 'other tab';
respond(); await pending;
assert.equal(preview.innerHTML, 'other tab');
activeDocId = 'a';
pending = _convertDocxToRichText(); respond(); await pending;
assert.equal(original.content, '<p>converted</p>');
assert.equal(saves, 1);
'''
    subprocess.run(["node", "--input-type=module", "-e", script], check=True, capture_output=True, text=True)
