"""The outgoing signature block: normalization, placement and delimiter.

`src/email_signature.py` is small but every rule in it is load-bearing:

- the RFC 3676 delimiter is `"-- "` with a trailing space. Without it,
  receiving clients stop recognising the block as a signature and quote it
  back into every reply.
- a reply's signature goes above the quoted original. Below it, nobody
  reads it.
- the composer already signs the draft, so anything appending server-side
  has to notice, or the recipient gets the sender's name twice.
"""

import pytest

from src.email_signature import (
    MAX_SIGNATURE_CHARS,
    SIGNATURE_DELIMITER,
    account_signature,
    apply_signature,
    body_has_signature,
    normalize_signature,
    signature_block,
)

SIG = "Ada Lovelace\nAnalytical Engines Ltd"


# ── The delimiter ─────────────────────────────────────────────────

def test_the_delimiter_keeps_its_trailing_space():
    """RFC 3676 §4.3 is exact: two hyphens, a space, nothing else. `--`
    alone is just a line of hyphens to a conforming client."""
    assert SIGNATURE_DELIMITER == "-- "


def test_the_block_leads_with_the_delimiter_on_its_own_line():
    assert signature_block(SIG).split("\n")[0] == "-- "


def test_an_empty_signature_produces_no_block():
    """Otherwise every message would carry a bare delimiter and nothing else."""
    for value in ("", "   ", None, "\n\n"):
        assert signature_block(value) == ""


# ── Normalization ─────────────────────────────────────────────────

def test_trailing_whitespace_is_stripped_from_every_line():
    assert normalize_signature("Ada   \nEngines\t") == "Ada\nEngines"


def test_leading_and_trailing_blank_lines_go():
    assert normalize_signature("\n\nAda\n\n\n") == "Ada"


def test_runs_of_blank_lines_collapse_to_one():
    assert normalize_signature("Ada\n\n\n\nEngines") == "Ada\n\nEngines"


def test_a_delimiter_the_user_typed_is_removed():
    """The send path adds exactly one. A second is quoted as content, and
    everything below it stops being a signature."""
    assert normalize_signature("-- \nAda\nEngines") == "Ada\nEngines"
    assert normalize_signature("--\nAda\nEngines") == "Ada\nEngines"


def test_removing_the_delimiter_does_not_leave_a_blank_first_line():
    assert normalize_signature("-- \n\nAda") == "Ada"


def test_a_delimiter_further_down_is_left_alone():
    """Only a leading one is ours to remove; mid-signature it is content."""
    assert normalize_signature("Ada\n--\nEngines") == "Ada\n--\nEngines"


def test_windows_line_endings_are_normalized():
    assert normalize_signature("Ada\r\nEngines") == "Ada\nEngines"


def test_an_oversized_signature_is_capped():
    """A paste accident must not put a novel on every outgoing message."""
    assert len(normalize_signature("x" * (MAX_SIGNATURE_CHARS * 2))) == MAX_SIGNATURE_CHARS


@pytest.mark.parametrize("value", [None, "", "   \n  "])
def test_nothing_normalizes_to_nothing(value):
    assert normalize_signature(value) == ""


# ── Placement ─────────────────────────────────────────────────────

def test_a_new_message_gets_the_signature_at_the_end():
    got = apply_signature("Hi there,\n\nThanks!", SIG)
    assert got == "Hi there,\n\nThanks!\n\n-- \nAda Lovelace\nAnalytical Engines Ltd\n"


def test_an_empty_body_gets_only_the_block():
    assert apply_signature("", SIG) == "-- \nAda Lovelace\nAnalytical Engines Ltd\n"


def test_a_reply_is_signed_above_the_quoted_original():
    """Below the quote is where signatures go to die."""
    body = (
        "Friday works.\n\n"
        "---------- Previous message ----------\n"
        "On Tue, Ada <a@b.c> wrote:\n> can we meet"
    )
    got = apply_signature(body, SIG)
    assert got.index("-- \n") < got.index("---------- Previous message ----------")
    assert got.endswith("> can we meet")


def test_a_forward_is_signed_above_the_forwarded_block():
    body = "See below.\n\n---------- Forwarded message ----------\nFrom: x\n\nhello"
    got = apply_signature(body, SIG)
    assert got.index("-- \n") < got.index("---------- Forwarded message ----------")


def test_a_quote_from_another_client_is_still_detected():
    body = "Friday works.\n\nOn Tue, 3 Jun 2026 at 09:14, Ada <a@b.c> wrote:\n> can we meet"
    got = apply_signature(body, SIG)
    assert got.index("-- \n") < got.index("On Tue, 3 Jun 2026")


def test_the_signature_lands_above_the_attribution_not_between_it_and_the_quote():
    body = "Yes.\n\nOn Tue, Ada wrote:\n> ping"
    lines = apply_signature(body, SIG).split("\n")
    assert lines.index("-- ") < lines.index("On Tue, Ada wrote:")


@pytest.mark.parametrize("attribution", [
    "On Tue, Ada <a@b.c> wrote:",
    "Em ter., Ada escreveu:",
    "Am Di., Ada schrieb:",
])
def test_attribution_lines_in_other_languages_are_recognised(attribution):
    got = apply_signature(f"Yes.\n\n{attribution}\n> ping", SIG)
    assert got.index("-- \n") < got.index(attribution)


def test_an_ordinary_sentence_ending_in_a_colon_is_not_a_quote_boundary():
    """A body that merely reads "here is the plan:" must not be split."""
    got = apply_signature("Here is the plan:\n\nShip on Friday.", SIG)
    assert got.endswith("-- \nAda Lovelace\nAnalytical Engines Ltd\n")


def test_a_bare_quote_with_no_attribution_still_works():
    got = apply_signature("Yes.\n\n> ping", SIG)
    assert got.index("-- \n") < got.index("> ping")


# ── Applying twice ────────────────────────────────────────────────

def test_a_body_that_already_carries_the_signature_is_untouched():
    once = apply_signature("Hi", SIG)
    assert apply_signature(once, SIG) == once


def test_a_reply_signed_by_the_composer_is_not_signed_again():
    body = apply_signature("Friday works.\n\n> can we meet", SIG)
    assert apply_signature(body, SIG).count("Analytical Engines Ltd") == 1


def test_detection_survives_light_reflowing():
    """The question is "would appending duplicate it", not "is it identical"."""
    assert body_has_signature("Hi\n\n-- \n  Ada Lovelace  \nAnalytical Engines Ltd", SIG)


def test_a_signature_only_in_the_quoted_history_does_not_block_signing():
    """Those lines are the previous message, not this one — the reply still
    needs its own signature."""
    assert body_has_signature("> Ada Lovelace\n> Analytical Engines Ltd", SIG) is False


def test_an_unrelated_body_is_not_mistaken_for_a_signed_one():
    assert body_has_signature("Ada said hello", SIG) is False


def test_no_signature_means_nothing_is_ever_detected():
    assert body_has_signature("anything at all", "") is False


def test_an_empty_signature_leaves_the_body_exactly_as_it_was():
    assert apply_signature("Hi there", "") == "Hi there"


# ── The account toggle ────────────────────────────────────────────

def test_the_configured_signature_is_used():
    assert account_signature({"signature": SIG, "signature_enabled": True}) == SIG


def test_turning_the_toggle_off_stops_it_without_deleting_the_text():
    assert account_signature({"signature": SIG, "signature_enabled": False}) == ""


def test_an_account_predating_the_column_defaults_to_on():
    """The migration backfills 1; a config dict assembled elsewhere should
    behave the same rather than silently dropping the signature."""
    assert account_signature({"signature": SIG}) == SIG


@pytest.mark.parametrize("cfg", [None, {}, {"signature": None}])
def test_an_account_with_no_signature_yields_nothing(cfg):
    assert account_signature(cfg) == ""
