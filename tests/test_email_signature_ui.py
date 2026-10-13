"""Frontend wiring for the outgoing email signature.

The composer modules pull in the DOM and a dozen siblings, so these read the
source rather than booting it — the same idiom as the other email UI tests.

`static/js/emailLibrary/signature.js` deliberately duplicates the rules in
`src/email_signature.py`: the signature has to be visible while composing
(JS) and still reach messages nothing composed (Python). These tests pin the
parts that must not drift apart.
"""

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def sig_js():
    return (_REPO / "static" / "js" / "emailLibrary" / "signature.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def inbox():
    return (_REPO / "static" / "js" / "emailInbox.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def settings():
    return (_REPO / "static" / "js" / "settings.js").read_text(encoding="utf-8")


# ── The shared rules ──────────────────────────────────────────────

def test_the_js_delimiter_matches_the_python_one(sig_js):
    """If these drift, a draft signed in the composer stops matching the
    dedupe check on the server and the recipient gets it twice."""
    from src.email_signature import SIGNATURE_DELIMITER

    assert f"SIGNATURE_DELIMITER = '{SIGNATURE_DELIMITER}'" in sig_js


def test_the_js_block_puts_the_delimiter_on_its_own_line(sig_js):
    assert "`${SIGNATURE_DELIMITER}\\n${cleaned}`" in sig_js


def test_the_js_drops_a_user_typed_delimiter_too(sig_js):
    """Both sides normalize the same way, or the dedupe check stops matching."""
    assert "lines[0].trim() === '--'" in sig_js


def test_the_js_recognises_the_same_quote_markers(sig_js):
    assert "Previous|Forwarded|Original" in sig_js


def test_the_js_recognises_the_same_attribution_languages(sig_js):
    assert "wrote|escreveu|schrieb|a écrit" in sig_js


def test_the_js_refuses_to_sign_twice(sig_js):
    assert "if (bodyHasSignature(text, signature)) return text;" in sig_js


def test_the_js_steps_back_over_the_attribution_line(sig_js):
    """So the signature does not land between "…wrote:" and its quote."""
    assert "ATTRIBUTION_RE.test(lines[i - 1])" in sig_js


def test_a_failed_account_fetch_never_blocks_a_draft(sig_js):
    block = sig_js[sig_js.index("export async function loadOutgoingSignature"):]
    block = block[:block.index("export function invalidateSignatureCache")]
    assert "return '';" in block


def test_the_toggle_is_honoured_client_side(sig_js):
    assert "account.signature_enabled === false" in sig_js


def test_the_active_account_wins_over_the_default(sig_js):
    assert "window.__odysseusActiveEmailAccount" in sig_js
    assert "a.is_default" in sig_js


# ── The composer ──────────────────────────────────────────────────

def test_the_composer_imports_the_helper(inbox):
    assert "from './emailLibrary/signature.js'" in inbox


def test_a_new_message_opens_already_signed(inbox):
    assert "const composeContent = await _withDraftSignature('To: \\nSubject: \\n---\\n');" in inbox
    assert "content: composeContent," in inbox


def test_replies_and_forwards_open_already_signed(inbox):
    assert "content = await _withDraftSignature(content);" in inbox


def test_only_the_body_below_the_header_block_is_signed(inbox):
    """Everything before the first `---` is headers; a signature there would
    be parsed as one."""
    block = inbox[inbox.index("async function _withDraftSignature"):]
    block = block[:block.index("const _emailSetupHint")]
    assert "const marker = '\\n---\\n';" in block
    assert "content.indexOf(marker)" in block


def test_a_signature_failure_never_blocks_a_draft(inbox):
    block = inbox[inbox.index("async function _withDraftSignature"):]
    block = block[:block.index("const _emailSetupHint")]
    assert "return content;" in block


# ── Settings ──────────────────────────────────────────────────────

def test_the_account_form_has_a_signature_field(settings):
    assert 'id="uf-email-signature"' in settings
    assert 'id="uf-email-signature-on"' in settings


def test_the_form_sends_both_fields(settings):
    assert "signature: el('uf-email-signature').value," in settings
    assert "signature_enabled: el('uf-email-signature-on').checked," in settings


def test_editing_an_account_shows_its_current_signature(settings):
    assert "el('uf-email-signature').value = existing.signature || '';" in settings
    assert "el('uf-email-signature-on').checked = existing.signature_enabled !== false;" in settings


def test_saving_drops_the_composer_cache(settings):
    """Otherwise the next draft carries the signature that was just replaced."""
    assert "invalidateSignatureCache" in settings


# ── The two implementations must agree ────────────────────────────

def test_the_js_and_python_produce_byte_identical_bodies(tmp_path):
    """The real hazard is drift, not a typo in either file.

    The composer signs the draft and the server checks whether a body is
    already signed; if the two disagree about placement or normalization,
    the check misses and the recipient gets the signature twice. Skipped
    where node is unavailable rather than silently not run.
    """
    import json
    import shutil
    import subprocess

    from src.email_signature import apply_signature

    if not shutil.which("node"):
        pytest.skip("node is not available to run the browser-side module")

    sig = "Ada Lovelace\nAnalytical Engines Ltd"
    cases = [
        ["Hi there,\n\nThanks!", sig],
        ["", sig],
        ["Friday works.\n\n---------- Previous message ----------\n"
         "On Tue, Ada <a@b.c> wrote:\n> can we meet", sig],
        ["Yes.\n\nOn Tue, Ada wrote:\n> ping", sig],
        ["Here is the plan:\n\nShip on Friday.", sig],
        ["Yes.\n\n> ping", sig],
        ["See below.\n\n---------- Forwarded message ----------\nFrom: x\n\nhello", sig],
        ["Hi", "-- \n\nAda   \n\n\n\nEngines  "],
        ["Hi\n\n-- \nAda", "Ada"],
        ["Em ter., Ada escreveu:\n> oi", "Ada"],
    ]

    module = _REPO / "static" / "js" / "emailLibrary" / "signature.js"
    runner = tmp_path / "crosscheck.mjs"
    runner.write_text(
        f"import {{ withSignature }} from {json.dumps(module.as_uri())};\n"
        "const cases = JSON.parse(process.argv[2]);\n"
        "console.log(JSON.stringify(cases.map(([b, s]) => withSignature(b, s))));\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["node", str(runner), json.dumps(cases)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [apply_signature(b, s) for b, s in cases]
