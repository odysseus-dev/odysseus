import pytest

from src.clean_agent_preview import email_reader_event


RESULT = {'stdout': '**Subject:** Weekly roundup\n**UID:** 162\n**Account:** Inbox (alex@example.com)\n'}


@pytest.mark.parametrize('prompt', ['open the weekly email', 'please open it', 'can you view that email'])
def test_explicit_open_uses_successful_read_identity(prompt):
    assert email_reader_event(prompt, 'mcp__email__read_email', {'folder': 'Archive'}, RESULT) == {
        'type': 'email_open', 'uid': '162', 'folder': 'Archive', 'account': 'alex@example.com'}


@pytest.mark.parametrize('prompt', ['summarize this email', 'read the weekly email', 'do not open it'])
def test_normal_read_does_not_open_reader(prompt):
    assert email_reader_event(prompt, 'read_email', {}, RESULT) is None


def test_failed_or_unidentified_message_never_opens():
    assert email_reader_event('open it', 'read_email', {}, RESULT, failed=True) is None
    assert email_reader_event('open it', 'list_emails', {}, RESULT) is None
    assert email_reader_event('open it', 'read_email', {'uid': '162'}, {'stdout': 'Not found'}) is None
