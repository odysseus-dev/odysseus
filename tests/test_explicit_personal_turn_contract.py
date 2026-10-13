import pytest

from src.turn_contract import requested_capabilities, selected_tools_for_request


@pytest.mark.parametrize('prompt', [
    "Find and read Morgan's latest email about the design review.",
    "Search my inbox and read the latest message about customer reviews.",
    "Find and open Casey's latest email about ratings. Do not send a reply.",
])
def test_review_subject_and_freshness_do_not_override_mailbox_source(prompt):
    assert selected_tools_for_request(prompt) == {'search_emails', 'read_email'}
    assert requested_capabilities(prompt) == {'email'}


@pytest.mark.parametrize('prompt', [
    'Find the latest reviews of noise cancelling headphones.',
    'What are the reviews of this hotel like?',
    'Search for current battery recycling developments.',
])
def test_public_discovery_still_uses_web_search(prompt):
    assert selected_tools_for_request(prompt) == {'web_search'}


def test_personal_notes_lookup_does_not_fall_back_to_public_search():
    prompt = 'Find my latest notes about design reviews.'
    assert 'web_search' not in (selected_tools_for_request(prompt) or ())
    assert 'notes' in requested_capabilities(prompt)


def test_calendar_to_email_draft_offers_complete_read_to_draft_chain():
    prompt = (
        "Read my calendar Tuesday, identify the earliest free slot, and create "
        "only an unsent email draft with the option. Do not change the calendar."
    )
    assert selected_tools_for_request(prompt) == {"manage_calendar", "draft_email"}
    assert requested_capabilities(prompt) == {"calendar", "email"}


def test_email_attachment_to_draft_keeps_complete_evidence_chain():
    prompt = (
        "Search my inbox for the latest email from Lena, read its attachment, "
        "and create an unsent draft with the calculation."
    )
    assert selected_tools_for_request(prompt) == {
        "search_emails", "read_email", "download_attachment", "draft_email",
    }


def test_exact_calendar_tool_name_overrides_file_like_event_title():
    prompt = (
        "Use manage_calendar to create the event 'Q3 Regulatory Filing' "
        "on the last Wednesday of next month."
    )
    assert selected_tools_for_request(prompt) == {"manage_calendar"}


def test_multiple_exact_personal_tool_names_are_preserved():
    prompt = "Search with manage_notes, then schedule with manage_tasks."
    assert selected_tools_for_request(prompt) == {"manage_notes", "manage_tasks"}


def test_personal_tool_name_substrings_do_not_select_tools():
    assert selected_tools_for_request("Explain xmanage_calendar_backup") is None


def test_calendar_email_calendar_chain_gets_complete_request_scoped_path():
    prompt = (
        "Look at my calendar for this Thursday's launch review with Priya Shah. "
        "Then check for her latest email about that meeting and update the calendar "
        "event to match exactly what she asks."
    )
    assert selected_tools_for_request(prompt) == {
        "manage_calendar", "search_emails", "read_email",
    }


def test_calendar_only_lookup_does_not_gain_email_tools():
    selected = selected_tools_for_request(
        "Look at my calendar for Thursday's launch review."
    ) or set()
    assert not {"search_emails", "read_email"}.intersection(selected)


def test_calendar_evidence_then_email_draft_keeps_both_capabilities():
    prompt = (
        "Look at my calendar this week, find a free hour, then create a reviewable "
        "email draft to jordan@example.com suggesting that slot. Do not send it or "
        "modify my calendar."
    )
    assert requested_capabilities(prompt) == {"calendar", "email"}


def test_find_read_report_email_request_excludes_mutators():
    prompt = (
        "Find and read Priya Shah's latest email about the launch review. "
        "Report the final logistics. Do not draft or send a reply."
    )
    assert selected_tools_for_request(prompt) == {"search_emails", "read_email"}


def test_find_read_and_reply_is_not_narrowed_to_read_only():
    prompt = "Find and read Priya's latest email, then draft a reply."
    assert selected_tools_for_request(prompt) != {"search_emails", "read_email"}
