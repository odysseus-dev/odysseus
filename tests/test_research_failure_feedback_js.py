"""Exercise failure diagnostics through research jobs and their visible cards."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _run(source, scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for browser-side behavior checks")
    fixtures = """
      import assert from 'node:assert/strict';
      const storage = new Map();
      const localStorage = {
        getItem(key) { return storage.get(key) || null; },
        setItem(key, value) { storage.set(key, value); },
      };
      const window = {};
      const notifications = [];
      const timers = new Map();
      let timer = 0;
      function setTimeout(cb) { timers.set(++timer, cb); return timer; }
      function setInterval(cb) { timers.set(++timer, cb); return timer; }
      function clearInterval(id) { timers.delete(id); }
      const requests = [];
      let payload = {};
      const fetch = async url => {
        requests.push(url);
        return {ok: true, json: async () => payload};
      };
      class EventSource {
        constructor(url) { this.url = url; }
        close() { this.closed = true; }
      }
      async function flush() { for (let i = 0; i < 8; i++) await Promise.resolve(); }
    """
    result = subprocess.run(
        [node, "--input-type=module"], input=fixtures + source + scenario,
        text=True, capture_output=True, cwd=ROOT, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def _jobs(scenario):
    source = (ROOT / "static/js/research/jobs.js").read_text()
    _run(source.replace("export ", ""), scenario)


def _cards(scenario):
    source = (ROOT / "static/js/research/panel.js").read_text()
    start = source.index("function _buildJobCard(")
    end = source.index("const _CAT_ICONS =", start)
    esc_start = source.index("function _esc(")
    esc_end = source.index("function _safeSourceHref(", esc_start)
    peek_start = source.index("async function _ensureResult(")
    peek_end = source.index("async function _copyResult(", peek_start)
    fixtures = """
      class Element {
        className = ''; dataset = {}; style = {}; innerHTML = '';
        classList = {values: new Set(), add(value) { this.values.add(value); }};
        set textContent(value) {
          this.innerHTML = String(value).replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;').replaceAll('>', '&gt;');
        }
        addEventListener() {}
        querySelector() { return new Element(); }
      }
      const document = {createElement() { return new Element(); }};
      const jobs = {formatElapsed() { return '1:45'; }};
      const _expandedJobId = null;
      const _historySelectMode = false;
      const _cancelIcon = '', _externalIcon = '', _chatIcon = '';
      const _copyIcon = '', _trashIcon = '', _apiBase = '';
      const _moreIcon = '';
      function _researchVisualVariant() { return null; }
      function _jobOverflowHTML() { return ''; }
      function _wireJobOverflow() { return {close() {}}; }
      function card(extra = {}) {
        return _buildJobCard({id: 'synthetic', status: 'done', query: 'test',
          sources: [], ...extra});
      }
    """
    _run(fixtures + source[start:end] + source[esc_start:esc_end]
         + source[peek_start:peek_end], scenario)


def test_search_and_extraction_failures_display_the_actual_stage():
    _cards("""
      const search = card({failure_stage: 'search',
        failure_message: 'Search unavailable: upstream CAPTCHA'});
      assert.match(search.innerHTML, /search failed/);
      assert.match(search.innerHTML, /Search unavailable: upstream CAPTCHA/);
      assert.doesNotMatch(search.innerHTML, /Couldn't extract anything/);
      const extraction = card({failure_stage: 'extraction'});
      assert.match(extraction.innerHTML, /extraction failed/);
      assert.match(extraction.innerHTML, /Pages were found/);
      assert.doesNotMatch(extraction.innerHTML, /switch the search engine/);
    """)


def test_failure_message_is_escaped_and_unknown_stages_are_supported():
    _cards(r"""
      const result = card({failure_stage: 'query_generation',
        failure_message: '<img src=x onerror=alert(1)>'});
      assert.match(result.innerHTML, /research failed/);
      assert.match(result.innerHTML, /&lt;img src=x onerror=alert\(1\)&gt;/);
      assert.doesNotMatch(result.innerHTML, /<img src=x/);
    """)


def test_lazy_result_load_preserves_failure_explanation():
    _cards("""
      payload = {result: 'failure report', sources: [],
        failure_stage: 'search', failure_message: 'Search blocked'};
      const job = {id: 'saved', result: null};
      await _ensureResult(job);
      assert.equal(job.failure_stage, 'search');
      assert.equal(job.failure_message, 'Search blocked');
      assert.match(card(job).innerHTML, /Search blocked/);
    """)


def test_legacy_zero_sources_and_successful_library_cards_keep_existing_behavior():
    _cards("""
      assert.match(card().innerHTML, /Couldn't extract anything/);
      const success = card({sources: null, sourceCount: 3, _fromLibrary: true});
      assert.doesNotMatch(success.innerHTML, /research-job-failnote/);
      assert.match(success.innerHTML, /3 sources/);
    """)


def test_explain_only_report_without_web_sources_is_not_a_failure():
    _cards("""
      const explanation = card({mode: 'explain', sources: []});
      assert.doesNotMatch(explanation.innerHTML, /research-job-failnote|search failed|no results/);
      assert.match(explanation.innerHTML, /model only/);
    """)


def test_library_load_and_refresh_propagate_failure_metadata():
    _jobs("""
      payload = {research: [{id: 'saved', status: 'done', source_count: 0,
        failure_stage: 'search', failure_message: 'Search blocked'}]};
      await _syncLibrary({force: true});
      assert.equal(_jobs[0].failure_stage, 'search');
      assert.equal(_jobs[0].failure_message, 'Search blocked');
      payload.research[0].failure_stage = 'extraction';
      payload.research[0].failure_message = 'No findings';
      await _syncLibrary({force: true});
      assert.equal(_jobs.length, 1);
      assert.equal(_jobs[0].failure_stage, 'extraction');
      assert.equal(_jobs[0].failure_message, 'No findings');
    """)


def test_terminal_stream_preserves_failure_before_result_request_completes():
    _jobs("""
      const job = {id: 'live', query: 'test', status: 'running', startedAt: Date.now()};
      payload = {result: 'explanation', sources: [], raw_findings: [],
        failure_stage: 'search', failure_message: 'Search blocked'};
      _connectStream(job);
      job._es.onmessage({data: JSON.stringify({final: true, status: 'done',
        failure_stage: 'search', failure_message: 'Search blocked'})});
      assert.equal(job.status, 'done');
      assert.equal(job.failure_stage, 'search');
      assert.equal(job.failure_message, 'Search blocked');
      await flush();
      assert.equal(job.failure_stage, 'search');
    """)


def test_poll_and_result_peek_propagate_and_clear_failure_metadata():
    _jobs("""
      const job = {id: 'live', query: 'test', status: 'running', startedAt: Date.now()};
      payload = {status: 'done', result: 'explanation', sources: [],
        failure_stage: 'extraction', failure_message: 'No findings'};
      await _pollFallback(job);
      assert.equal(job.failure_stage, 'extraction');
      assert.equal(job.failure_message, 'No findings');
      await flush();
      payload = {result: 'successful report', sources: [{url: 'https://example.org'}]};
      await _fetchResult(job);
      assert.equal(job.failure_stage, '');
      assert.equal(job.failure_message, '');
    """)
