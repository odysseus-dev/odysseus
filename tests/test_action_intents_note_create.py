"""Note/todo creation requests must promote plain chat to agent mode.

In Chat mode no tool schema is sent unless the lexical classifier escalates,
so a missed create request leaves the model announcing a note it cannot
write. The original imperative only accepted ``add|create|make`` followed
directly by the noun, which missed "Create a new note ...".
"""

import pytest

from src.action_intents import classify_tool_intent


NOTE_CREATE_REQUESTS = [
    # A qualifier between the article and the noun.
    "Create a new note with a todo list of your own invention",
    "Create a new note",
    "create a new note called groceries",
    "make a quick note: buy more coffee",
    "Please add a short checklist for the move",
    "add a new todo to buy milk",
    "create a quick shopping checklist",
    "create another note",
    # Plural and the hyphenated spelling.
    "create two new notes, one for work and one for home",
    "add a to-do: renew the passport",
    "create a new to-do list",
    "add some tasks about the house move",
    # Naming the tool, including the follow-up from the reported chat.
    "Use the note tool to create it in odysseus",
    "do it with the notes tool",
    "save that using the note tool",
    # Explicit note targets on a follow-up.
    "save it to my notes",
    "put that in my notes",
    "save this as a new note",
    "turn that list into a checklist",
]

# Requests that are about notes but do not ask for an action, plus code
# requests whose subject happens to be a note or todo app.
NOT_A_NOTE_ACTION = [
    "how do I create a note?",
    "How do I create a new note?",
    "How do I use the note tool?",
    "Here are my notes on this paper",
    "notes on this paper",
    "take notes during the meeting",
    "I take notes during the meeting",
    "What does the notes tool do?",
    "Write a short note to my landlord",
    "create a summary of these notes",
    "make a list of pros and cons",
]

CODE_REQUESTS_ABOUT_NOTES = [
    "create a simple todo app in React",
    "create a new note-taking app",
    "build a todo list app",
]


@pytest.mark.parametrize("text", NOTE_CREATE_REQUESTS)
def test_note_create_requests_promote_to_notes(text):
    intent = classify_tool_intent(text)

    assert intent.needs_tools, intent.reason
    assert intent.category == "notes", intent


@pytest.mark.parametrize("text", NOT_A_NOTE_ACTION)
def test_note_mentions_without_an_action_stay_plain_chat(text):
    intent = classify_tool_intent(text)

    assert intent.category != "notes", intent
    assert not intent.needs_tools, intent


@pytest.mark.parametrize("text", CODE_REQUESTS_ABOUT_NOTES)
def test_code_requests_about_note_apps_stay_on_the_workspace_route(text):
    intent = classify_tool_intent(text)

    assert intent.needs_tools
    assert intent.category == "workspace", intent


def test_existing_note_phrasings_still_promote():
    for text in (
        "Add a note with a todo list",
        "create a todo list note",
        "add milk to my todo list",
        "take a note that the server needs checking",
        "Can you make a new note with a shopping list?",
    ):
        intent = classify_tool_intent(text)
        assert intent.needs_tools and intent.category == "notes", (text, intent)
