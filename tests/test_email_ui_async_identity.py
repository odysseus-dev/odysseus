from pathlib import Path
from tests.helpers.document_source import document_source
from tests.helpers.js_modules import email_library_source


ROOT = Path(__file__).resolve().parents[1]


def test_card_delete_waits_for_durable_success_and_uses_email_identity():
    source = email_library_source()
    assert "function _emailMutationQuery(em" in source
    assert "em?.folder || fallbackFolder" in source
    assert "em?.account_id || state._libAccountId" in source
    assert "data?.success !== true" in source
    assert source.count("await _requireSuccessfulEmailMutation(response, 'Failed to delete email');") >= 3


def test_finished_send_does_not_close_whichever_library_opened_later():
    source = document_source()
    start = source.index("async function _sendEmail()")
    end = source.index("async function _saveDraft()", start)
    send = source[start:end]
    # Closing the initiating composer when SMTP starts is valid. Completion
    # must not close a different document/email opened while SMTP was pending.
    assert send.count("if (isLibraryOpen()) closeLibrary();") == 1
    success_cleanup = send.index("// Delete the compose document after successful send.")
    assert "closeLibrary()" not in send[success_cleanup:]
