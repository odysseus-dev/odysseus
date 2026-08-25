"""Source contract for live multi-round fallback attribution."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


CHAT_JS = Path("static/js/chat.js").read_text(encoding="utf-8")
_HAS_NODE = shutil.which("node") is not None


def _resume_function_source():
    body = CHAT_JS.split("export async function resumeStream", 1)[1].split(
        "export function checkBackgroundStream", 1
    )[0]
    return "async function resumeStream" + body.rstrip()


def _run_node(source):
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=source,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_live_fallback_targets_the_active_round_and_replaces_actual_model():
    fallback_block = CHAT_JS.split("json.type === 'fallback'", 1)[1].split(
        "json.type === 'doc_stream_open'", 1
    )[0]

    assert "applyModelRouteEventState(json, holder, roundHolder, modelName)" in fallback_block
    assert "_fallbackHolder.querySelector('.role')" in fallback_block
    assert "_hasResolvedActual" not in fallback_block


def test_provider_alias_uses_the_same_round_aware_holder_selection():
    actual_block = CHAT_JS.split("json.type === 'model_actual'", 1)[1].split(
        "json.type === 'attachments'", 1
    )[0]

    assert "applyModelRouteEventState(json, holder, roundHolder, modelName)" in actual_block
    assert "_modelHolder.querySelector('.role')" in actual_block


def test_new_round_and_final_metrics_target_the_active_round():
    agent_step_block = CHAT_JS.split("} else if (json.type === 'agent_step')", 1)[1].split(
        "json.type === 'budget_exceeded'", 1
    )[0]
    metrics_block = CHAT_JS.split("json.type === 'metrics'", 1)[1].split(
        "json.type === 'message_saved'", 1
    )[0]
    final_block = CHAT_JS.split("const _isBgFinal", 1)[1].split(
        "holder.dataset.raw", 1
    )[0]

    assert "inheritModelRouteState(holder, roundHolder, newWrap" in agent_step_block
    assert "applyModelMetricsState(metrics, holder, roundHolder, modelName)" in metrics_block
    assert "_finalModelHolder.querySelector('.role')" in final_block
    assert "holder.querySelector('.role')" not in final_block


def test_terminal_sse_error_bypasses_eof_auto_recovery():
    parser_block = CHAT_JS.split("if (_nextIsError || json.status >= 400)", 1)[1].split(
        "if (json.delta", 1
    )[0]
    completion_gate = CHAT_JS.split("if (_streamTerminalError)", 1)[1].split(
        "if (!_streamSawDone)", 1
    )[0]
    recovery_block = CHAT_JS.split("isRecoverableStreamError(err)", 1)[1].split(
        "const errorHolder", 1
    )[0]

    assert "createTerminalStreamError(json)" in parser_block
    assert "throw _streamTerminalError" in completion_gate
    assert "if (err.terminalStreamError)" in recovery_block
    assert "await sessionModule.selectSession(streamSessionId, { showLoading: false })" in recovery_block


def test_connection_recovery_resumes_detached_run_without_resubmitting_selected_model():
    recovery = CHAT_JS.split("function _tryAutoRecover", 1)[1].split(
        "function _removeStallBanner", 1
    )[0]

    assert "await resumeStream(sessionId, holder || null)" in recovery
    assert "/api/chat_stream" not in recovery
    assert ".click()" not in recovery
    assert "_pendingContinue" not in recovery
    assert "if (_streamSessionId === streamSessionId) _streamSessionId = null" in CHAT_JS


def test_detached_resume_reloads_canonical_terminal_failures():
    resume = CHAT_JS.split("export async function resumeStream", 1)[1].split(
        "export function checkBackgroundStream", 1
    )[0]

    assert "l.trim() === 'event: error'" in resume
    assert "json.type === 'agent_terminal'" in resume
    assert "rich = true" in resume
    assert "Network drop or parse failure: fall through to the canonical reload" in resume
    assert "if (onThisSession && !rich && roundText.trim())" in resume
    assert "res.headers.get('X-Odysseus-Run-Id')" in resume
    assert "chatRenderer.recordSessionMetricsCost(metricsData, sessionId)" in resume


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_detached_resume_surfaces_fallback_then_provider_alias_before_reload():
    source = "\n".join([
        "import { applyModelRouteEventState } from './static/js/chatModelProvenance.js';",
        "class Element {",
        "  constructor(tag = 'div') { this.tag = tag; this.children = []; this.parentNode = null; this.style = {}; this.textContent = ''; this._html = ''; }",
        "  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }",
        "  remove() { if (!this.parentNode) return; this.parentNode.children = this.parentNode.children.filter(c => c !== this); this.parentNode = null; }",
        "  set innerHTML(value) {",
        "    this._html = value;",
        "    if (value.includes('stream-content')) {",
        "      this._role = new Element('div'); this._role.parentNode = this;",
        "      this._body = new Element('div'); this._body.parentNode = this;",
        "      this._content = new Element('div'); this._body.appendChild(this._content);",
        "    }",
        "  }",
        "  get innerHTML() { return this._html; }",
        "  querySelector(selector) { if (selector === '.role') return this._role || null; if (selector === '.body') return this._body || null; if (selector === '.stream-content') return this._content || null; return null; }",
        "}",
        "const box = new Element('main');",
        "const document = { getElementById(id) { return id === 'chat-history' ? box : null; }, createElement(tag) { return new Element(tag); } };",
        "const window = {};",
        "let selectCalls = 0; const labels = []; const toasts = [];",
        "const sessionModule = { getSessions() { return [{id: 's1', model: 'selected-model'}]; }, getCurrentSessionId() { return 's1'; }, selectSession() { selectCalls += 1; }, loadSessions() {} };",
        "const uiModule = { esc(value) { return String(value); }, scrollHistory() {}, showToast(value) { toasts.push(value); } };",
        "const spinnerModule = { create() { return { element: null, createElement() { this.element = new Element('spinner'); return this.element; }, start() {}, destroy() { if (this.element) this.element.remove(); } }; } };",
        "const markdownModule = { normalizeThinkingMarkup(v) { return v; }, mdToHtml(v) { return v; }, squashOutsideCode(v) { return v; } };",
        "const documentModule = null; const chatRenderer = { recordSessionMetricsCost() {}, addMessage() {} };",
        "const _resumingStreams = new Set(); const _streamRunIds = new Map(); const API_BASE = '';",
        "function hasActiveStream() { return false; } function _shortModel(v) { return v; } function _applyModelColor() {}",
        "function _setRoleModelLabel(role, requested, actual) { labels.push({requested, actual}); role.textContent = requested + ' -> ' + actual; }",
        "function _streamDisplayText(v) { return v; } function _showDocumentWritingStatus() {} function _finishDocumentWritingStatus() {} function _metricsCostRecordId() { return 'run'; }",
        "const events = [",
        "  'data: {\"type\":\"fallback\",\"selected_model\":\"selected-model\",\"answered_by\":\"fallback-model\",\"reason\":\"429\"}\\n\\n',",
        "  'data: {\"type\":\"model_actual\",\"model\":\"provider/fallback-alias\"}\\n\\n',",
        "  'data: {\"delta\":\"hello\"}\\n\\n',",
        "  'data: [DONE]\\n\\n',",
        "].join('');",
        "const encoded = new TextEncoder().encode(events); let reads = 0;",
        "const reader = { async read() { return reads++ === 0 ? {done:false, value:encoded} : {done:true}; }, async cancel() {} };",
        "async function fetch() { return { ok:true, body:{getReader(){return reader;}}, headers:{get(){return 'run-1';}} }; }",
        _resume_function_source(),
        "await resumeStream('s1');",
        "console.log(JSON.stringify({labels, toasts, selectCalls, holderCount: box.children.length}));",
    ])

    assert _run_node(source) == {
        "labels": [
            {"requested": "selected-model", "actual": "fallback-model"},
            {"requested": "selected-model", "actual": "provider/fallback-alias"},
        ],
        "toasts": ["Fallback: selected-model failed — answered by fallback-model"],
        "selectCalls": 1,
        "holderCount": 0,
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_detached_resume_renders_preoutput_error_without_empty_reload():
    source = "\n".join([
        "import { createTerminalStreamError } from './static/js/chatStreamErrors.js';",
        "class Element {",
        "  constructor(tag = 'div') { this.tag = tag; this.children = []; this.parentNode = null; this.style = {}; this.textContent = ''; this._html = ''; }",
        "  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }",
        "  remove() { if (!this.parentNode) return; this.parentNode.children = this.parentNode.children.filter(c => c !== this); this.parentNode = null; }",
        "  set innerHTML(value) {",
        "    this._html = value;",
        "    if (value.includes('stream-content')) {",
        "      this._role = new Element('div'); this._role.parentNode = this;",
        "      this._body = new Element('div'); this._body.parentNode = this;",
        "      this._content = new Element('div'); this._body.appendChild(this._content);",
        "    }",
        "  }",
        "  get innerHTML() { return this._html; }",
        "  querySelector(selector) { if (selector === '.role') return this._role || null; if (selector === '.body') return this._body || null; if (selector === '.stream-content') return this._content || null; return null; }",
        "}",
        "const box = new Element('main');",
        "const document = { getElementById(id) { return id === 'chat-history' ? box : null; }, createElement(tag) { return new Element(tag); } };",
        "const window = {};",
        "let selectCalls = 0;",
        "const sessionModule = { getSessions() { return [{id: 's1', model: 'selected'}]; }, getCurrentSessionId() { return 's1'; }, selectSession() { selectCalls += 1; }, loadSessions() {} };",
        "const uiModule = { esc(value) { return String(value); }, scrollHistory() {} };",
        "const spinnerModule = { create() { return { element: null, createElement() { this.element = new Element('spinner'); return this.element; }, start() {}, destroy() { if (this.element) this.element.remove(); } }; } };",
        "const markdownModule = { normalizeThinkingMarkup(v) { return v; }, mdToHtml(v) { return v; }, squashOutsideCode(v) { return v; } };",
        "const documentModule = null;",
        "const chatRenderer = { recordSessionMetricsCost() {}, addMessage() {} };",
        "const _resumingStreams = new Set(); const _streamRunIds = new Map(); const API_BASE = '';",
        "function hasActiveStream() { return false; } function _shortModel(v) { return v; } function _applyModelColor() {}",
        "function _streamDisplayText(v) { return v; } function _showDocumentWritingStatus() {} function _finishDocumentWritingStatus() {} function _metricsCostRecordId() { return 'run'; }",
        "const encoded = new TextEncoder().encode('event: error\\ndata: {\"status\":401,\"error\":\"invalid key <img src=x>\"}\\n\\n');",
        "let reads = 0; const reader = { async read() { return reads++ === 0 ? {done:false, value:encoded} : {done:true}; }, async cancel() {} };",
        "async function fetch() { return { ok:true, body:{getReader(){return reader;}}, headers:{get(){return 'run-1';}} }; }",
        _resume_function_source(),
        "const result = await resumeStream('s1');",
        "const holder = box.children[0]; const errorNode = holder && holder._content.children.find(node => node.textContent.startsWith('[Error:'));",
        "console.log(JSON.stringify({result, selectCalls, holderCount: box.children.length, errorText: errorNode && errorNode.textContent}));",
    ])

    assert _run_node(source) == {
        "result": True,
        "selectCalls": 0,
        "holderCount": 1,
        "errorText": "[Error: invalid key <img src=x>]",
    }


def test_terminal_then_session_switch_preserves_completed_background_state():
    terminal = CHAT_JS.split(
        "json.type === 'agent_terminal' || json.type === 'chat_terminal'", 1
    )[1].split("json.type === 'metrics'", 1)[0]
    detach = CHAT_JS.split("export function detachCurrentStream", 1)[1].split(
        "export async function resumeStream", 1
    )[0]
    background_catch = CHAT_JS.split("if (_isBgCatch)", 1)[1].split(
        "} else {", 1
    )[0]

    assert "_terminalSavedStreams.add(streamSessionId)" in terminal
    assert "terminalSaved ? 'completed' : 'running'" in detach
    assert "!terminalSaved && sessionModule && sessionModule.markStreaming" in detach
    assert "_terminalSavedStreams.has(streamSessionId)" in background_catch


def test_detached_run_identity_is_attached_to_live_metrics():
    routes = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    assert "headers={\"X-Odysseus-Run-Id\": _detached_run.run_id}" in routes
    assert "agent_runs.subscribe(session, _detached_run)" in routes
    assert "agent_runs.subscribe(session_id, _active_run)" in routes
    assert "const streamRunId = res.headers.get('X-Odysseus-Run-Id')" in CHAT_JS
    assert "metrics._costRecordId = _metricsCostRecordId(streamRunId, json)" in CHAT_JS
    assert "'X-Odysseus-Run-Id': runId" in CHAT_JS
    assert "agent_runs.stop(session_id, _expected_run_id)" in routes
    assert "_stopExactRun(streamSessionId)" in CHAT_JS
    timeout_block = CHAT_JS.split("timeoutId = setTimeout", 1)[1].split(
        "clearResponseTimeout", 1
    )[0]
    assert "/api/chat/stop/" not in timeout_block


def test_replay_cost_identity_distinguishes_primary_and_teacher_segments():
    identity = CHAT_JS.split("function _metricsCostRecordId", 1)[1].split("\n  }", 1)[0]
    resume = CHAT_JS.split("export async function resumeStream", 1)[1].split(
        "export function checkBackgroundStream", 1
    )[0]

    assert "event.teacher ? 'teacher' : 'primary'" in identity
    assert "_metricsCostRecordId(resumeRunId, json)" in resume
    metrics_block = resume.split("json.type === 'metrics'", 1)[1].split(
        "json.type === 'agent_terminal'", 1
    )[0]
    assert "chatRenderer.recordSessionMetricsCost(metricsData, sessionId)" in metrics_block

    routes = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    route_metrics = routes.split('elif data.get("type") == "metrics"', 1)[1].split(
        "except json.JSONDecodeError", 1
    )[0]
    assert 'if data.get("teacher") is True' in route_metrics
    assert '_metrics_event["teacher"] = True' in route_metrics


def test_foreground_terminal_error_reloads_saved_partial_without_typewriter_race():
    parser = CHAT_JS.split("if (_nextIsError || json.status >= 400)", 1)[1].split(
        "if (json.delta", 1
    )[0]
    terminal_catch = CHAT_JS.split("if (err.terminalStreamError)", 1)[1].split(
        "const errorHolder", 1
    )[0]

    assert "typewriterInto" not in parser
    assert "json.type === 'agent_terminal'" in CHAT_JS
    assert "_canonicalTerminalSaved = true" in CHAT_JS
    assert "await sessionModule.selectSession(streamSessionId, { showLoading: false })" in terminal_catch
