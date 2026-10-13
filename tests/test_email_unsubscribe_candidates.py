from email.message import EmailMessage

from routes.email_routes import (
    _dedupe_unsubscribe_candidates,
    _email_unsubscribe_candidate_from_msg,
    _parse_list_unsubscribe_header,
)


def test_parse_list_unsubscribe_mailto_and_url():
    methods = _parse_list_unsubscribe_header(
        '<mailto:list@example.com?subject=unsubscribe&body=remove%20me>, '
        '<https://example.com/unsubscribe/token>'
    )

    assert methods == [
        {
            "kind": "mailto",
            "target": "list@example.com",
            "subject": "unsubscribe",
            "body": "remove me",
            "executable": True,
        },
        {
            "kind": "url",
            "target": "https://example.com/unsubscribe/token",
            "executable": False,
        },
    ]


def test_unsubscribe_candidate_requires_unsubscribe_header():
    msg = EmailMessage()
    msg["From"] = "Shop <deals@example.com>"
    msg["Subject"] = "Limited time discount"
    msg["Precedence"] = "bulk"

    assert _email_unsubscribe_candidate_from_msg(msg, "12", "INBOX") is None


def test_unsubscribe_candidate_scores_bulk_newsletter():
    msg = EmailMessage()
    msg["From"] = "Shop <deals@example.com>"
    msg["Subject"] = "Limited time discount"
    msg["Precedence"] = "bulk"
    msg["List-Id"] = "Shop Deals <deals.example.com>"
    msg["List-Unsubscribe"] = "<mailto:unsubscribe@example.com?subject=unsubscribe>"

    candidate = _email_unsubscribe_candidate_from_msg(
        msg,
        "12",
        "INBOX",
        spam_cached={"spam": True, "reason": "marketing blast"},
    )

    assert candidate is not None
    assert candidate["uid"] == "12"
    assert candidate["can_execute"] is True
    assert candidate["recommended_method"]["kind"] == "mailto"
    assert "marketing blast" in candidate["reasons"]


def test_dedupe_unsubscribe_candidates_collapses_same_list():
    first = EmailMessage()
    first["From"] = "Shop <deals@example.com>"
    first["Subject"] = "Sale one"
    first["List-Id"] = "Shop Deals <deals.example.com>"
    first["List-Unsubscribe"] = "<mailto:unsubscribe@example.com?subject=unsubscribe>"

    second = EmailMessage()
    second["From"] = "Shop <deals@example.com>"
    second["Subject"] = "Sale two"
    second["List-Id"] = "Shop Deals <deals.example.com>"
    second["List-Unsubscribe"] = "<mailto:unsubscribe@example.com?subject=unsubscribe>"

    candidates = [
        _email_unsubscribe_candidate_from_msg(first, "12", "INBOX"),
        _email_unsubscribe_candidate_from_msg(second, "13", "INBOX"),
    ]

    deduped = _dedupe_unsubscribe_candidates(candidates)

    assert len(deduped) == 1
    assert deduped[0]["duplicate_count"] == 2
    assert deduped[0]["duplicate_uids"] == ["12", "13"]


def test_dedupe_unsubscribe_candidates_collapses_tokenized_links_from_same_sender():
    first = {
        "uid": "12",
        "from_address": "no-reply@example.com",
        "subject": "Sale one",
        "list_id": "list-one.example.com",
        "recommended_method": {"kind": "url", "target": "https://example.com/u/a"},
    }
    second = {
        "uid": "13",
        "from_address": "NO-REPLY@example.com",
        "subject": "Sale two",
        "list_id": "list-two.example.com",
        "recommended_method": {"kind": "url", "target": "https://example.com/u/b"},
    }

    deduped = _dedupe_unsubscribe_candidates([first, second])

    assert len(deduped) == 1
    assert deduped[0]["duplicate_count"] == 2
    assert deduped[0]["duplicate_uids"] == ["12", "13"]
