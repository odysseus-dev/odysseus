"""Execute reply insertion and request guards with private-safe email fixtures."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.helpers.document_source import declaration, function_body


ROOT = Path(__file__).resolve().parents[1]


def _between(source, start, end):
    offset = source.index(start)
    return source[offset:source.index(end, offset)]


def _run(scenario, functions="", fixtures=""):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for browser-side behavior checks")
    helper = (ROOT / "static/js/emailReplyText.js").as_uri()
    stream_helper = (ROOT / "static/js/emailReplyStream.js").as_uri()
    script = (f"import {{cleanEmailReplyText}} from {json.dumps(helper)};\n"
              f"import {{readEmailReplyResponse}} from {json.dumps(stream_helper)};\n") + r"""
      import assert from 'node:assert/strict';
      const original = '---------- Previous message ----------\n' +
        'On Monday, Taylor <taylor@example.invalid> wrote:\n' +
        '> Could you confirm a suitable day for the visit?';
      const finalReply = 'Hi Taylor,\n\nThursday works. You can come Thursday.\n\nMorgan';
      const hint = 'Thursday works, you can come Thursday';
      function response(reply = finalReply, extra = {}, ok = true) {
        return new Response(JSON.stringify({success: true, reply, ...extra}), {
          status: ok ? 200 : 503, headers: {"content-type": "application/json"},
        });
      }
      function deferred() {
        let resolve;
        const promise = new Promise(done => { resolve = done; });
        return {promise, resolve};
      }
    """ + fixtures + functions + scenario
    result = subprocess.run(
        [node, "--input-type=module"], input=script, text=True,
        capture_output=True, cwd=ROOT, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def _editor(scenario, replacement=False):
    functions = declaration("_AI_REPLY_CONTEXT_STORE_PREFIX") + "\n" + "\n".join(
        function_body(name) for name in (
            "_docAiReplyContextKey", "_loadDocAiReplyContext", "_saveDocAiReplyContext",
            "_clearDocAiReplyContext", "_aiReply", "_splitEmailReplyQuote",
        )
    )
    if replacement:
        functions += "\n" + function_body("replaceEmailReplyBody").replace("export ", "")
        functions += "\n" + "\n".join(function_body(name) for name in (
            "_unfoldEmailHeaderLines", "_parseEmailHeader", "_buildEmailContent",
        ))
    fixtures = r"""
      const API_BASE = '';
      const window = {__odysseusActiveEmailAccount: 'account-a'};
      let activeDocId = 'draft-a', _docAiReplyRequestSeq = 0, _emailAiReplyGeneration = 0;
      let _autoSaveDebounce = null, richbody = null;
      const docs = new Map([['draft-a', {
        language: 'email', sourceEmailAccountId: 'account-a', content: original,
      }]]);
      function editable(target) {
        if (target.addEventListener) return target;
        target.inputListeners = new Set();
        target.addEventListener = (event, listener) => { if (event === 'input') target.inputListeners.add(listener); };
        target.removeEventListener = (event, listener) => { if (event === 'input') target.inputListeners.delete(listener); };
        target.emitInput = () => { for (const listener of target.inputListeners) listener(); };
        return target;
      }
      const textarea = editable({value: original});
      const button = {innerHTML: 'Reply', disabled: false};
      const elements = {
        'doc-editor-textarea': textarea, 'doc-email-ai-reply-btn': button,
        'doc-email-to': {value: 'Taylor <taylor@example.invalid>'},
        'doc-email-subject': {value: 'Re: Visit'},
        'doc-email-source-uid': {value: '42'},
        'doc-email-source-folder': {value: 'INBOX'},
        'doc-email-in-reply-to': {value: '<visit@example.invalid>'},
      };
      const document = {getElementById(id) { return elements[id] || null; }};
      const storage = new Map([['note-key', hint]]);
      const localStorage = {
        getItem(key) { return storage.get(key) || null; },
        setItem(key, value) { storage.set(key, value); },
        removeItem(key) { storage.delete(key); },
      };
      const notifications = [], inserted = [], requests = [];
      const uiModule = {
        showToast(text) { notifications.push({kind: 'toast', text}); },
        showError(text) { notifications.push({kind: 'error', text}); },
      };
      const sessionModule = {getCurrentModel() { return ''; }, getCurrentSessionId() { return 'session-a'; }};
      function _emailRichbodyActive() {
        if (richbody) {
          editable(richbody);
          richbody.cloneNode ||= () => ({innerHTML: richbody.innerHTML, querySelectorAll() { return []; }});
        }
        return richbody;
      }
      function _syncEmailRichbody(rich) { textarea.value = rich.innerText; }
      function _setEmailBodyText(target, value) {
        inserted.push(value); target.value = value;
        if (richbody) { richbody.innerHTML = value; richbody.innerText = value; }
      }
      function saveCurrentToMap() { docs.get(activeDocId).content = textarea.value; }
      function saveDocument() {}
      function _emailQuoteStartOffset() { return -1; }
      function _emailQuoteStartIndex() { return -1; }
      function _emailHtmlToPlainText(text) { return text.replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]*>/g, '').replaceAll('&lt;', '<').replaceAll('&gt;', '>'); }
      function setTimeout() { return 1; }
      function clearTimeout() {}
      let fetch = async (url, opts) => {
        requests.push({url, payload: JSON.parse(opts.body)});
        return response();
      };
    """
    _run(scenario, functions, fixtures)


@pytest.mark.parametrize("unusable", [
    "Done", "Completed.", "Finished", "Drafted", "Drafting...", "Ready", "", "   ", None, {"content": "Done"},
    "<think>I should confirm Thursday.</think>",
    "<think>I should confirm Thursday, but have not finished.",
    "<<<REPLY>>>Hi Taylor, Thursday works.",
    "<<<REPLY>>>\n<<<END>>>",
    "<|im_start|>user\nThursday works\n<|im_end|>\n<|im_start|>assistant\nDone",
    "User: Thursday works\nAssistant: Done",
    "Reasoning: I need to write a reply confirming Thursday.",
    "The user wants a reply confirming Thursday.",
    "The user requested a concise reply confirming Thursday.",
    "I need to draft a reply confirming Thursday.",
    "Writing style: keep the response short.",
    "Identity rule: I should speak as Morgan.",
])
def test_final_reply_cleaner_rejects_control_text_and_unfinished_answers(unusable):
    _run(f"assert.equal(cleanEmailReplyText({json.dumps(unusable)}), '');")


def test_final_reply_cleaner_keeps_only_the_marked_final_after_reasoning():
    _run(r"""
      const raw = '<think>Example: <<<REPLY>>>wrong day<<<END>>></think>' +
        '<<<REPLY>>>' + finalReply + '<<<END>>>\nDone';
      assert.equal(cleanEmailReplyText(raw), finalReply);
      assert.equal(cleanEmailReplyText('<think>private reasoning</think>' + finalReply), finalReply);
      assert.equal(cleanEmailReplyText(finalReply + '\n\nOn Monday, Taylor wrote:\n> old text'), finalReply);
      assert.equal(cleanEmailReplyText(finalReply + '\n\nDen mån 5 okt skrev Taylor:\n> old text'), finalReply);
      assert.equal(cleanEmailReplyText('Yes.'), 'Yes.');
      assert.equal(cleanEmailReplyText('No, Thursday is not suitable.'), 'No, Thursday is not suitable.');
      const support = 'Hi Taylor,\nUser: read-only\nSystem: staging\n\nMorgan';
      assert.equal(cleanEmailReplyText(support), support);
      assert.equal(cleanEmailReplyText('<<<REPLY>>>' + support + '<<<END>>>'), support);
      assert.equal(cleanEmailReplyText('```text\n' + support + '\n```'), support);
      assert.equal(cleanEmailReplyText('The user requested read-only access.'), 'The user requested read-only access.');
    """)


def test_literal_status_is_allowed_only_when_the_user_requests_it():
    _run(r"""
      assert.equal(cleanEmailReplyText('Done', {userHint: 'Reply with only "Done".'}), 'Done');
      assert.equal(cleanEmailReplyText('Done', {userHint: 'Reply only Done. Do not add any other words.'}), 'Done');
      assert.equal(cleanEmailReplyText('Completed.', {currentDraft: 'Completed.'}), 'Completed.');
      assert.equal(cleanEmailReplyText('Done', {userHint: hint, currentDraft: 'Done'}), '');
      assert.equal(cleanEmailReplyText('Done', {userHint: 'Reply when the work is done.'}), '');
      assert.equal(cleanEmailReplyText('Done', {userHint: 'Do not say Done.'}), '');
      assert.equal(cleanEmailReplyText('Ready', {userHint: 'Reply only Done.'}), '');
    """)


def test_editor_sends_typed_intent_separately_and_inserts_only_final_reply():
    _editor(r"""
      textarea.value = 'Thursday works, you can come Thursday\n\n' + original;
      fetch = async (url, opts) => {
        requests.push({url, payload: JSON.parse(opts.body)});
        return response('<think>Draft a Thursday reply.</think>\n<<<REPLY>>>' + finalReply + '<<<END>>>\nDone');
      };
      assert.equal(await _aiReply({noteHint: hint, contextKey: 'note-key'}), true);
      assert.equal(requests.length, 1);
      assert.equal(requests[0].payload.current_draft, hint);
      assert.equal(requests[0].payload.user_hint, hint);
      assert.equal(requests[0].payload.original_body, original);
      assert.equal(requests[0].payload.account_id, 'account-a');
      assert.deepEqual(inserted, [finalReply + '\n\n' + original]);
      assert.equal(storage.has('note-key'), false);
      assert.equal(button.disabled, false);
      assert.equal(button.innerHTML, 'Reply');
      assert.deepEqual(notifications, [
        {kind: 'toast', text: 'Writing AI reply'}, {kind: 'toast', text: 'AI draft inserted'},
      ]);
    """)


def test_editor_can_polish_a_typed_draft_without_a_context_note():
    _editor(r"""
      textarea.value = hint + '\n\n' + original;
      assert.equal(await _aiReply(), true);
      assert.equal(requests[0].payload.current_draft, hint);
      assert.equal(requests[0].payload.user_hint, '');
      assert.equal(textarea.value, finalReply + '\n\n' + original);
    """)


def test_editor_keeps_typed_text_after_a_placeholder_as_draft_guidance():
    _editor(r"""
      const typed = '[AI reply draft will appear here]\nThursday works';
      textarea.value = typed + '\n\n' + original;
      assert.equal(await _aiReply(), true);
      assert.equal(requests[0].payload.current_draft, typed);
    """)


def test_existing_draft_replacement_preserves_typed_text_after_placeholder():
    _editor(r"""
      const typed = '[AI reply draft will appear here]\nThursday works';
      textarea.value = typed + '\n\n' + original;
      assert.equal(await replaceEmailReplyBody('draft-a', finalReply), false);
      assert.equal(textarea.value, typed + '\n\n' + original);
      assert.deepEqual(inserted, []);
    """, replacement=True)


def test_existing_rich_quote_only_draft_accepts_reply_and_keeps_original_once():
    _editor(r"""
      const htmlQuote = original.replaceAll('<', '&lt;').replaceAll('>', '&gt;');
      richbody = {innerHTML: '<div class="email-quoted-history">' + htmlQuote + '</div>', innerText: original};
      docs.get('draft-a').content = richbody.innerHTML;
      saveCurrentToMap = () => { docs.get(activeDocId).content = richbody.innerHTML; };
      assert.equal(await replaceEmailReplyBody('draft-a', finalReply), true);
      assert.equal(textarea.value, finalReply + '\n\n' + original);
      assert.equal((textarea.value.match(/Previous message/g) || []).length, 1);
      assert.deepEqual(inserted, [finalReply + '\n\n' + original]);
    """, replacement=True)


@pytest.mark.parametrize("kind", ["status", "empty_after_cleaning", "http_error", "network_error"])
def test_editor_failed_generation_preserves_draft_and_context_note(kind):
    _editor(r"""
      const kind = """ + json.dumps(kind) + r""";
      textarea.value = hint + '\n\n' + original;
      const before = textarea.value;
      fetch = async () => {
        if (kind === 'network_error') throw new Error('offline');
        if (kind === 'http_error') return response(finalReply, {}, false);
        return response(kind === 'status' ? 'Done' : '<think>unfinished reply</think>');
      };
      assert.equal(await _aiReply({noteHint: hint, contextKey: 'note-key'}), false);
      assert.equal(textarea.value, before);
      assert.deepEqual(inserted, []);
      assert.equal(storage.get('note-key'), hint);
      assert.equal(button.disabled, false);
      assert.equal(notifications.at(-1).kind, 'error');
      assert.doesNotMatch(textarea.value, /AI returned|Failed to generate/);
    """)


@pytest.mark.parametrize("change", ["body", "richbody", "recipient", "doc", "account", "source_account", "textarea"])
def test_editor_inflight_generation_does_not_overwrite_changes(change):
    _editor(r"""
      const change = """ + json.dumps(change) + r""";
      if (change === 'richbody') richbody = {innerHTML: original, innerText: original};
      const pending = deferred(); fetch = async () => pending.promise;
      const generation = _aiReply({noteHint: hint, contextKey: 'note-key'});
      if (change === 'body') textarea.value = 'Morgan typed during generation';
      if (change === 'richbody') richbody.innerHTML = 'Morgan formatted the draft';
      if (change === 'recipient') elements['doc-email-to'].value = 'morgan@example.invalid';
      if (change === 'doc') { activeDocId = 'draft-b'; docs.set('draft-b', {language: 'email', content: 'other draft'}); }
      if (change === 'account') window.__odysseusActiveEmailAccount = 'account-b';
      if (change === 'source_account') docs.get('draft-a').sourceEmailAccountId = 'account-b';
      if (change === 'textarea') elements['doc-editor-textarea'] = {value: 'new editor'};
      pending.resolve(response());
      assert.equal(await generation, false);
      assert.deepEqual(inserted, []);
      assert.equal(storage.get('note-key'), hint);
      if (change === 'body') assert.equal(textarea.value, 'Morgan typed during generation');
      if (change === 'richbody') assert.equal(richbody.innerHTML, 'Morgan formatted the draft');
    """)


def test_editor_latest_request_wins_and_restores_button():
    _editor(r"""
      const first = deferred(), second = deferred(); let count = 0;
      fetch = async () => ++count === 1 ? first.promise : second.promise;
      const older = _aiReply({noteHint: 'Tuesday works'});
      const newer = _aiReply({noteHint: hint});
      first.resolve(response('Hi Taylor, Tuesday works.'));
      assert.equal(await older, false);
      assert.equal(button.disabled, true);
      second.resolve(response());
      assert.equal(await newer, true);
      assert.deepEqual(inserted, [finalReply + '\n\n' + original]);
      assert.equal(button.disabled, false);
      assert.equal(button.innerHTML, 'Reply');
    """)


def test_editor_context_notes_are_scoped_to_mail_accounts():
    _editor(r"""
      const first = _docAiReplyContextKey();
      _saveDocAiReplyContext(first, hint);
      docs.get('draft-a').sourceEmailAccountId = 'account-b';
      const second = _docAiReplyContextKey();
      assert.notEqual(first, second);
      assert.equal(_loadDocAiReplyContext(second), '');
      docs.get('draft-a').sourceEmailAccountId = 'account-a';
      assert.equal(_loadDocAiReplyContext(_docAiReplyContextKey()), hint);
    """)



def test_editor_keeps_stream_frames_out_of_the_draft_until_completed_result():
    _editor(r"""
      let controller;
      const encoder = new TextEncoder();
      fetch = async () => new Response(new ReadableStream({start(c) { controller = c; }}), {
        headers: {'content-type': 'text/event-stream'},
      });
      const emit = event => controller.enqueue(encoder.encode('data: ' + JSON.stringify(event) + '\n\n'));
      const generation = _aiReply({noteHint: hint, originalBody: 'Original reader message'});
      emit({type: 'reply', text: '<think>Need to reason about Thursday.</think>Done'});
      for (let i = 0; i < 20; i++) await Promise.resolve();
      assert.equal(textarea.value, original);
      assert.deepEqual(inserted, []);
      emit({type: 'reply', text: finalReply.slice(0, 20)});
      emit({type: 'result', success: true, reply: finalReply});
      assert.equal(await generation, true);
      assert.deepEqual(inserted, [finalReply + '\n\n' + original]);
      assert.equal(textarea.inputListeners.size, 0);
    """)


def test_editor_interrupted_stream_preserves_draft_and_context():
    _editor(r"""
      const encoder = new TextEncoder();
      fetch = async () => new Response(new ReadableStream({start(controller) {
        controller.enqueue(encoder.encode('data: ' + JSON.stringify({type: 'reply', text: 'Hi Taylor, partial'}) + '\n\n'));
        controller.close();
      }}), {headers: {'content-type': 'text/event-stream'}});
      const before = textarea.value;
      assert.equal(await _aiReply({noteHint: hint, contextKey: 'note-key'}), false);
      assert.equal(textarea.value, before);
      assert.deepEqual(inserted, []);
      assert.equal(storage.get('note-key'), hint);
      assert.equal(textarea.inputListeners.size, 0);
      assert.equal(notifications.at(-1).kind, 'error');
    """)


def test_editor_typing_then_undoing_still_invalidates_inflight_insertion():
    _editor(r"""
      const pending = deferred(); fetch = async () => pending.promise;
      const generation = _aiReply({noteHint: hint});
      textarea.value = 'An edit that was undone'; textarea.emitInput();
      textarea.value = original; textarea.emitInput();
      pending.resolve(response());
      assert.equal(await generation, false);
      assert.equal(textarea.value, original);
      assert.deepEqual(inserted, []);
      assert.equal(textarea.inputListeners.size, 0);
      assert.match(notifications.at(-1).text, /draft was edited/);
    """)


def test_editor_stream_stops_after_user_edit_without_inserting_provisional_reply():
    _editor(r"""
      let controller;
      const encoder = new TextEncoder();
      fetch = async () => new Response(new ReadableStream({start(c) { controller = c; }}), {
        headers: {'content-type': 'text/event-stream'},
      });
      const generation = _aiReply({noteHint: hint});
      textarea.value = 'Morgan typed during generation'; textarea.emitInput();
      controller.enqueue(encoder.encode('data: ' + JSON.stringify({type: 'reply', text: finalReply}) + '\n\n'));
      assert.equal(await generation, false);
      assert.equal(textarea.value, 'Morgan typed during generation');
      assert.deepEqual(inserted, []);
      assert.equal(textarea.inputListeners.size, 0);
    """)


def test_editor_preserves_stream_option_and_reader_original_separate_from_draft():
    _editor(r"""
      textarea.value = hint + '\n\n' + original;
      assert.equal(await _aiReply({noteHint: hint, originalBody: 'Original reader message', mode: 'ai-reply-full'}), true);
      assert.equal(requests[0].payload.original_body, 'Original reader message');
      assert.equal(requests[0].payload.current_draft, hint);
      assert.equal(requests[0].payload.stream, true);
      assert.equal(requests[0].payload.fast, false);
    """)



def test_reply_dependencies_are_precached_at_their_actual_import_urls():
    service_worker = (ROOT / "static/sw.js").read_text()
    for helper in ("emailReplyStream", "emailReplyText"):
        assert f"'/static/js/{helper}.js'" in service_worker
