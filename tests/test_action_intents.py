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
    assert message_needs_tools("delete that actually")
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


def test_chinese_web_lookup_requests_route_to_web_tools():
    intent = classify_tool_intent("帮我查一下这些店铺的地址，我要去打卡")

    assert intent.needs_tools
    assert intent.category == "web"


def test_nearest_place_lookup_promotes_to_web_agent():
    intent = classify_tool_intent("from vasaplan stockholm where is closest parking")
    assert intent.needs_tools
    assert intent.category == "web"


def test_workspace_agent_requests_promote_to_shell_workspace():
    prompts = [
        "fix the bug in this repo",
        "run the tests for this project",
        "debug the server logs",
        "run a performance benchmark on this project",
        "inspect the traceback and patch the code",
    ]
    for prompt in prompts:
        intent = classify_tool_intent(prompt)
        assert intent.needs_tools
        assert intent.category == "workspace"


def test_page_references_are_not_mistaken_for_named_computers():
    for prompt in (
        "What heading is visible on that page?",
        "Read it from the current page.",
        "Compare this with the same page.",
    ):
        intent = classify_tool_intent(prompt)
        assert intent.category != "workspace"

    intent = classify_tool_intent("check the service on odysseus")
    assert intent.needs_tools and intent.category == "workspace"


def test_direct_code_requests_promote_to_workspace_agent():
    prompts = [
        "write a Python function that parses CSV",
        "write answer.json",
        "create app.ts",
        "edit src/app.py",
        "Can you create a script in this project?",
        "edit the React component to show a loading state",
        "I want you to build a small command-line tool",
        "Can you code this in the repo?",
    ]
    for prompt in prompts:
        intent = classify_tool_intent(prompt)
        assert intent.needs_tools
        assert intent.category == "workspace"


def test_code_explanations_stay_plain_chat():
    assert not message_needs_tools("How do I write a Python function?")
    assert not message_needs_tools("Can you explain how a React component works?")


def test_shell_diagnostic_commands_promote_to_agent():
    prompts = [
        "lsblk",
        "run lsblk",
        "df -h",
        "docker ps",
        "nvidia-smi",
        "can you run journalctl -u odysseus",
    ]
    for prompt in prompts:
        intent = classify_tool_intent(prompt)
        assert intent.needs_tools
        assert intent.category in {"shell", "workspace"}


def test_shell_command_explanations_stay_plain_chat():
    assert not message_needs_tools("How do I use lsblk?")
    assert not message_needs_tools("Can you explain docker ps?")


def test_explanatory_calendar_questions_stay_plain_chat():
    assert not message_needs_tools("How do I add an entry to my calendar?")
    assert not message_needs_tools("What about the built-in Odysseus calendar, is that linked to email?")
    assert not message_needs_tools("Can you explain how calendar reminders work?")
    intent = classify_tool_intent("How do I add an entry to my calendar?")
    assert not intent.needs_tools
    assert intent.reason == "explanatory feature question"


def test_router_reports_non_calendar_categories():
    assert classify_tool_intent("reply to that email").category == "email"
    assert classify_tool_intent("open my calendar").category == "ui"
    assert classify_tool_intent("research cost effective local models").category == "research"
