from src.action_intents import message_needs_tools


def test_calendar_entry_request_promotes_to_agent():
    assert message_needs_tools("Can you add an entry to my calendar?")


def test_calendar_imperative_variants_promote_to_agent():
    assert message_needs_tools("add lunch with Sam to my calendar tomorrow at noon")
    assert message_needs_tools("schedule a call with Mina next Friday")
    assert message_needs_tools("put dentist appointment on my calendar")


def test_note_todo_and_reminder_actions_promote_to_agent():
    assert message_needs_tools("add milk to my todo list")
    assert message_needs_tools("take a note that the server needs checking")
    assert message_needs_tools("set a reminder to call Pat at 4pm")


def test_email_and_ui_actions_promote_to_agent():
    assert message_needs_tools("reply to that email")
    assert message_needs_tools("mark those emails as read")
    assert message_needs_tools("open my calendar")
    assert message_needs_tools("turn off web search")


def test_research_action_promotes_to_agent():
    assert message_needs_tools("research cost effective local models")
    assert message_needs_tools("can you look into GPU hosting options")


def test_explanatory_calendar_questions_stay_plain_chat():
    assert not message_needs_tools("How do I add an entry to my calendar?")
    assert not message_needs_tools("What about the built-in Juniperus calendar, is that linked to email?")
    assert not message_needs_tools("Can you explain how calendar reminders work?")


def test_self_analysis_requests_promote_to_agent():
    assert message_needs_tools("Analyze Juniperus itself.")
    assert message_needs_tools("Audit your own Brain and Library.")
    assert message_needs_tools("What tools, routes, and memory structures do you have?")
    assert message_needs_tools("Inspect your own app structure.")
    assert message_needs_tools("Map what is wired, scaffolded, and missing.")
    assert message_needs_tools("Please analyze your knowledge graph.")
    assert message_needs_tools("Can you audit your memories?")
    assert message_needs_tools("What parts of this app are wired, scaffolded, or missing?")
    assert message_needs_tools("Inspect your own structure and tell me what you can see.")


def test_explanatory_self_analysis_questions_stay_plain_chat():
    assert not message_needs_tools("How do I analyze Juniperus?")
    assert not message_needs_tools("What does it mean to audit your own brain?")
    assert not message_needs_tools("Can you explain how the knowledge graph works?")
