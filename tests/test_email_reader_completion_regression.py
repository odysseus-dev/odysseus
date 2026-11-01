from src.agent_evidence import infer_completion_requirements
from src.clean_agent_preview import active_email_context_message, email_identifier_error


def test_open_reader_uid_is_valid_email_evidence():
    reader = active_email_context_message({
        'uid': '3971', 'folder': 'INBOX', 'account': 'account-1',
    })
    assert email_identifier_error(
        'mcp__email__read_email',
        {'uid': '3971', 'folder': 'INBOX', 'account': 'account-1'},
        user_text='Summarize this email', history=[reader],
    ) is None
    assert email_identifier_error(
        'mcp__email__read_email', {'uid': '3972'},
        user_text='Summarize this email', history=[reader],
    )


def test_pasted_email_address_does_not_become_required_artifact():
    instruction = (
        'Summarize this email\r\n'
        'If you do not wish to make use of this service, please send a mail '
        'to news.alerts@example.test.\r\n'
    )
    assert infer_completion_requirements(instruction).required_artifacts == ()
