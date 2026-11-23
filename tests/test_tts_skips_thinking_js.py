"""Regression coverage for #6717: read aloud spoke the model's reasoning.

``AITTSManager.extractPlainText()`` stripped only a bare ``<think>...</think>``,
but chat.js rewrites the opener to ``<think time="...">`` once thinking ends, so
both the per-message read-aloud button and TTS Mode (``streamingEnd``) sent the
reasoning to the speech provider ahead of the answer. Read aloud now goes
through ``extractThinkingBlocks()``, the same split the chat bubble uses.

The real ``tts-ai.js`` runs under node with its real import graph. A resolve
hook swaps the two browser-only modules it reaches (``appConfig.js`` and
``ui.js``) for small stubs.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node binary not on PATH")

_HARNESS = textwrap.dedent(
    r"""
    import { register } from 'node:module';
    import { pathToFileURL } from 'node:url';

    // Load the real tts-ai.js and its real import graph, except the two
    // browser-only modules it reaches: ui.js (via markdown.js) and appConfig.js.
    const hooks = `
      const STUBS = {
        'ui.js': 'export default { esc: (value) => String(value ?? "") };',
        'appConfig.js': 'export const getSettings = () => ({}); export default {};',
      };
      export async function resolve(specifier, context, next) {
        const name = specifier.split(/[?#]/)[0].split('/').pop();
        if (Object.hasOwn(STUBS, name)) return { url: 'stub:' + name, shortCircuit: true };
        return next(specifier, context);
      }
      export async function load(url, context, next) {
        if (url.startsWith('stub:')) {
          return { format: 'module', source: STUBS[url.slice(5)], shortCircuit: true };
        }
        const result = await next(url, context);
        return url.includes('/static/js/') ? { ...result, format: 'module' } : result;
      }
    `;
    register('data:text/javascript,' + encodeURIComponent(hooks));

    globalThis.window = {
      location: { origin: 'http://localhost' },
      katex: null,
      addEventListener() {},
    };
    globalThis.MutationObserver = class { observe() {} };
    // checkAvailability() runs in the constructor; fail it so nothing is reachable.
    globalThis.fetch = async () => { throw new Error('offline'); };
    // Enough of an element for markdown.js's <template> use, the <div>
    // extractPlainText() parses into (textContent drops tags) and the hidden
    // placeholder button streamingEnd() creates.
    globalThis.document = {
      readyState: 'loading',
      addEventListener() {},
      createElement() {
        return {
          _html: '',
          style: {},
          content: { querySelectorAll() { return []; } },
          querySelectorAll() { return []; },
          set innerHTML(value) { this._html = value; },
          get innerHTML() { return this._html; },
          get textContent() { return this._html.replace(/<[^>]*>/g, ''); },
        };
      },
    };

    const { AITTSManager } = await import(pathToFileURL('./static/js/tts-ai.js').href);
    const cases = JSON.parse(process.argv[1]);
    const out = {};

    const plain = new AITTSManager();
    for (const [name, text] of Object.entries(cases.plain)) out[name] = plain.extractPlainText(text);

    // TTS Mode: chat.js hands the finished reply to streamingEnd(), which
    // enqueues whatever the sentence stream has not spoken yet.
    const streaming = new AITTSManager();
    const spoken = [];
    streaming.enqueue = (text) => spoken.push(text);
    streaming.streamingStart();
    streaming.streamingEnd(cases.streamingEnd);
    out.streamingEnd = spoken;

    console.log(JSON.stringify(out));
    """
)

_ANSWER = "The sky looks blue because air scatters short wavelengths more than long ones."
_REASONING = "The user asks why the sky is blue. I should mention Rayleigh scattering."

_PLAIN_CASES = {
    # The persisted shape chat.js produces once thinking finishes (#6717).
    "timed_think": f'<think time="5.2">{_REASONING}</think>\n\n{_ANSWER}',
    "bare_think": f"<think>{_REASONING}</think>\n\n{_ANSWER}",
    "thought_tag": f"<thought>{_REASONING}</thought>\n\n{_ANSWER}",
    "minimax_think": f"<mm:think>{_REASONING}</mm:think>\n\n{_ANSWER}",
    "two_rounds": (
        f'<think time="1.0">{_REASONING}</think>First part of the answer. '
        f'<think time="2.0">{_REASONING} Now the second part.</think>Second part of the answer.'
    ),
    "no_thinking": "Just a **normal** reply with no reasoning markup.",
}


@pytest.fixture(scope="module")
def tts_output():
    cases = {
        "plain": _PLAIN_CASES,
        "streamingEnd": f'<think time="0.8">{_REASONING}</think>\n\n{_ANSWER}',
    }
    result = subprocess.run(
        ["node", "--input-type=module", "-e", _HARNESS, json.dumps(cases)],
        cwd=_REPO,
        capture_output=True,
        timeout=30,
        text=True,
    )
    assert result.returncode == 0, f"node failed:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}"
    return json.loads(result.stdout.splitlines()[-1])


@pytest.mark.parametrize("name", ["timed_think", "bare_think", "thought_tag", "minimax_think"])
def test_read_aloud_speaks_only_the_answer(tts_output, name):
    assert tts_output[name] == _ANSWER, f"{name}: {tts_output[name]!r}"


def test_multi_round_reply_keeps_every_answer_part(tts_output):
    spoken = tts_output["two_rounds"]
    assert "Rayleigh" not in spoken, spoken
    assert "First part of the answer." in spoken, spoken
    assert "Second part of the answer." in spoken, spoken


def test_reply_without_thinking_is_still_spoken(tts_output):
    assert tts_output["no_thinking"] == "Just a normal reply with no reasoning markup."


def test_tts_mode_flush_does_not_enqueue_reasoning(tts_output):
    assert tts_output["streamingEnd"] == [_ANSWER]
