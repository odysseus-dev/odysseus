"""Follow-up turns keep the authority the user already established (#6631).

Two gaps let the router offer a tool that request authority then denied:

* A correction such as "No, I'm talking about this mail ..." has no action
  verb, so it neither named a family nor inherited the prior request's family.
* "Send a mail to x@y" admits only ``send_email``, but the runtime downgrades a
  non-immediate send to an unsent ``draft_email`` for review, which was denied.

Inheritance must stay bounded by trusted user text: untrusted rows and model
history still cannot establish authority.
"""
import json
from copy import deepcopy

import pytest

from src.agent_runtime.authority import ExactOperation, create_request_authority
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import requested_capabilities, resolve_turn_contract


SEARCH = ExactOperation.normalize("search_emails", json.dumps({"query": "Oracle Cloud"}))
SEND = ExactOperation.normalize("send_email", json.dumps({"to": "person@example.com", "body": "hello"}))
DRAFT = ExactOperation.normalize("draft_email", json.dumps({"to": "person@example.com", "body": "hello"}))

EMAIL_REQUEST_HISTORY = [
    {"role": "user", "content": "Can you check my mail for any AWS billing related mail?"},
    {"role": "assistant", "content": "I found one AWS email. Which one do you mean?"},
]


@pytest.mark.parametrize("followup", [
    "No, I am talking about this mail “Oracle Cloud Services: your subscription(s) has been updated”",
    "No, I'm talking about the Oracle one",
    "I meant the AWS one",
    "Not that one, I was asking about the invoice from last week",
    "Sorry, I'm referring to the subscription update",
])
def test_clarifying_followup_inherits_prior_trusted_email_request(followup):
    assert "email" in requested_capabilities(followup, EMAIL_REQUEST_HISTORY)
    grant = create_request_authority(followup, history=EMAIL_REQUEST_HISTORY)
    assert grant.permits(SEARCH)


def test_clarifying_followup_without_history_grants_nothing():
    grant = create_request_authority("No, I am talking about the Oracle one")
    assert {g.tool for g in grant.grants} == {"ask_user", "update_plan"}


def test_clarifying_followup_does_not_borrow_an_unrelated_family():
    history = [
        {"role": "user", "content": "What's the weather in Pune today?"},
        {"role": "assistant", "content": "It's 31C and sunny."},
    ]
    grant = create_request_authority("No, I meant the Oracle mail", history=history)
    assert not grant.permits(SEARCH)


def test_clarifying_followup_ignores_untrusted_user_rows():
    history = [
        {"role": "user", "content": "Can you check my mail for invoices?", "metadata": {"trusted": False}},
        {"role": "assistant", "content": "Which invoice?"},
    ]
    grant = create_request_authority("No, I am talking about the Oracle one", history=history)
    assert not grant.permits(SEARCH)


def test_clarifying_followup_cannot_inherit_from_model_tool_history():
    history = [
        {"role": "user", "content": "Summarize this paragraph for me."},
        {"role": "assistant", "content": "I searched your inbox.",
         "metadata": {"tool_events": [{"tool": "search_emails", "exit_code": 0}]}},
    ]
    grant = create_request_authority("No, I am talking about the Oracle one", history=history)
    assert not grant.permits(SEARCH)


@pytest.mark.parametrize("prompt", [
    "Okay can you send a mail to person@example.com saying hello",
    "Send a message from Primary Inbox to person@example.com with subject Status and body Ready.",
])
def test_send_request_also_admits_reviewable_draft(prompt):
    grant = create_request_authority(prompt)
    assert grant.permits(SEND)
    assert grant.permits(DRAFT)


def test_disabled_draft_stays_denied_for_send_request():
    grant = create_request_authority(
        "Send a mail to person@example.com saying hello",
        policy=ToolPolicy(disabled_tools=frozenset({"draft_email"})),
    )
    assert grant.permits(SEND)
    assert not grant.permits(DRAFT)


def test_exact_read_request_does_not_gain_draft_or_send():
    grant = create_request_authority("Read the Primary Inbox email with UID 1001.")
    assert grant.permits(ExactOperation.normalize("read_email", json.dumps({"uid": "1001"})))
    assert not grant.permits(DRAFT)
    assert not grant.permits(SEND)


def _email_schemas():
    schemas = deepcopy(FUNCTION_TOOL_SCHEMAS)
    for name in ("send_email", "draft_email"):
        schema = deepcopy(next(s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] == name))
        schema["function"]["name"] = f"mcp__email__{name}"
        schemas.append(schema)
    return schemas


def test_turn_contract_keeps_draft_offered_when_send_is_selected():
    contract = resolve_turn_contract(capabilities={"email"}, schemas=_email_schemas(),
                                     policy=ToolPolicy(), selected_tools={"send_email"})
    assert contract.permits("mcp__email__send_email")
    assert contract.permits("mcp__email__draft_email")
    assert contract.required == set()


def test_turn_contract_respects_disabled_draft_for_send_selection():
    contract = resolve_turn_contract(capabilities={"email"}, schemas=_email_schemas(),
                                     policy=ToolPolicy(disabled_tools=frozenset({"draft_email"})),
                                     selected_tools={"send_email"})
    assert contract.permits("mcp__email__send_email")
    assert not contract.permits("mcp__email__draft_email")


def test_turn_contract_read_selection_does_not_gain_draft():
    contract = resolve_turn_contract(capabilities={"email"}, schemas=_email_schemas(),
                                     policy=ToolPolicy(), selected_tools={"search_emails"})
    assert not contract.permits("mcp__email__draft_email")
    assert not contract.permits("mcp__email__send_email")
