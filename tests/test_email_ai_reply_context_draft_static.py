from pathlib import Path
from tests.helpers.document_source import document_source
from tests.helpers.js_modules import email_library_source


ROOT = Path(__file__).resolve().parent.parent


def test_email_ai_reply_context_is_saved_and_restored_per_message():
    source = email_library_source()

    assert "_AI_REPLY_CONTEXT_DRAFT_PREFIX" in source
    assert "data?.account_id || em?.account_id || state._libAccountId" in source
    assert "data?.folder || em?.folder || state._libFolder" in source
    assert "noteInput.value = _loadAiReplyContextDraft(contextDraftKey);" in source
    assert "noteInput.addEventListener('input'" in source
    assert "_saveAiReplyContextDraft(contextDraftKey, noteInput.value || '');" in source
    assert "document.removeEventListener('click', _aiReplyChoiceOutsideClose, true);" in source


def test_email_ai_reply_context_only_clears_after_draft_opens():
    library = email_library_source()
    inbox = (ROOT / "static/js/emailInbox.js").read_text(encoding="utf-8")

    assert "const draftOpened = await _runAiReplyFromButton" in library
    assert "if (draftOpened === true) _clearAiReplyContextDraft(contextDraftKey);" in library
    assert "return await _openEmail(opts.email" in inbox
    assert "return replyDraftOpened;" in inbox


def test_document_ai_reply_does_not_overwrite_an_edited_draft():
    source = document_source()
    start = source.index("  async function _aiReply(")
    end = source.index("  async function _scheduleSend(", start)
    function = source[start:end]

    assert "let _emailAiReplyGeneration = 0;" in source
    assert "Writing AI reply" in function
    assert "const draftStillUnchanged = () =>" in function
    assert "_emailAiReplyGeneration" in function
    assert "_setEmailBodyText(textarea, newBody);" in function
    assert "await _streamEmailBodyText(textarea, newBody);" not in function
    assert "AI reply ready, but draft was edited" in function

    inbox = (ROOT / "static/js/emailInbox.js").read_text(encoding="utf-8")
    assert "showToast('Writing AI reply'" in inbox
    assert "AI reply service returned HTTP" in inbox


def test_replacing_an_email_reply_checks_the_visible_draft_first():
    source = document_source()
    start = source.index("  export async function replaceEmailReplyBody(")
    end = source.index("  export async function ensureEmailDraftEnvelope(", start)
    function = source[start:end]

    assert "const initialBody = fields.body || '';" in function
    assert "textarea.value !== initialBody" in function
    assert "_setEmailBodyText(textarea, body);" in function
    assert "await _streamEmailBodyText(textarea, body);" not in function
