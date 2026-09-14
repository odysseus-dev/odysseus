from src.action_intents import classify_tool_intent, message_needs_tools


def test_calendar_entry_request_promotes_to_agent():
    assert message_needs_tools("Can you add an entry to my calendar?")
    intent = classify_tool_intent("Can you add an entry to my calendar?")
    assert intent.needs_tools
    assert intent.category == "calendar"


def test_calendar_imperative_variants_promote_to_agent():
    assert message_needs_tools("add lunch with Sam to my calendar tomorrow at noon")
    assert message_needs_tools("schedule a call with Mina next Friday")
    assert message_needs_tools("put dentist appointment on my calendar")
    assert message_needs_tools("Alright. Recreate that same appointment")
    assert message_needs_tools("Okay delete that doctor appointment from the calendar")
    assert message_needs_tools("have another go at adding a test entry to the calendar")
    assert message_needs_tools(
        "Okay so you should be able to create that calendar event for tomorrow at 1:30 p.m. right for me to go to the hardware store"
    )
    assert message_needs_tools(
        "make it an appointment at 12pm for me to visit the doctor it's tomorrow the 2nd of June 2026"
    )


def test_calendar_read_requests_promote_to_agent():
    assert message_needs_tools("What upcoming events do I have?")
    assert message_needs_tools("Can you show my next appointments?")
    assert message_needs_tools("Do I have upcoming Taekwondo classes this week?")
    assert message_needs_tools("What's on my calendar tomorrow?")
    assert message_needs_tools("When is my next meeting?")


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


def test_explicit_web_search_promotes_to_agent():
    assert message_needs_tools("use web search and find a recipe for chocolate chip cookies")
    assert message_needs_tools("do a web search for the best chocolate chip cookies")
    assert message_needs_tools("search the web for current RTX 3090 prices")
    assert classify_tool_intent("use web search and find a recipe").category == "web"


def test_workspace_agent_requests_promote_to_shell_workspace():
    prompts = [
        "fix the bug in this repo",
        "run the tests for this project",
        "debug the server logs",
        "run terminal-bench on this task",
        "inspect the traceback and patch the code",
    ]
    for prompt in prompts:
        intent = classify_tool_intent(prompt)
        assert intent.needs_tools
        assert intent.category == "workspace"


def test_explanatory_calendar_questions_stay_plain_chat():
    assert not message_needs_tools("How do I add an entry to my calendar?")
    assert not message_needs_tools("What about the built-in Odysseus calendar, is that linked to email?")
    assert not message_needs_tools("Can you explain how calendar reminders work?")
    intent = classify_tool_intent("How do I add an entry to my calendar?")
    assert not intent.needs_tools
    assert intent.reason == "explanatory feature question"


def test_contacts_lookup_promotes_to_agent():
    assert message_needs_tools("mi dici il numero di Paolo?")
    assert message_needs_tools("what is the phone number of Mario?")
    assert message_needs_tools("tell me Anna address")
    assert message_needs_tools("cerca Francesca nei contatti")
    assert message_needs_tools("do you have Giorgio phone?")
    assert classify_tool_intent("mi dici il numero di Paolo?").category == "contacts"
    assert classify_tool_intent("look up Marco in contacts").category == "contacts"


def test_files_lookup_promotes_to_agent():
    assert message_needs_tools("leggi il file README")
    assert message_needs_tools("read the CHANGELOG")
    assert message_needs_tools("what's in config.yaml")
    assert message_needs_tools("open Makefile")
    assert message_needs_tools("cosa c'è in server.log")
    assert classify_tool_intent("show me package.json").category == "files"
    assert classify_tool_intent("what does app.py contain").category == "files"
    # UI panel names must NOT be classified as files
    assert classify_tool_intent("open settings").category == "ui"
    assert classify_tool_intent("open my calendar").category == "ui"
    # Explanatory questions must NOT promote
    assert not message_needs_tools("how do I read files?")
    assert not message_needs_tools("what is a file system?")


def test_sessions_lookup_promotes_to_agent():
    assert message_needs_tools("search my chats for the bitcoin discussion")
    assert message_needs_tools("find the conversation about the project")
    assert message_needs_tools("cerca nelle chat il progetto")
    assert message_needs_tools("trova la conversazione sul viaggio")
    assert message_needs_tools("cosa abbiamo detto sul budget")
    assert classify_tool_intent("look through my conversations").category == "sessions"
    # Ambiguous "find" must NOT trigger sessions
    assert not message_needs_tools("find me a restaurant")


def test_router_reports_non_calendar_categories():
    assert classify_tool_intent("reply to that email").category == "email"
    assert classify_tool_intent("open my calendar").category == "ui"
    assert classify_tool_intent("research cost effective local models").category == "research"
