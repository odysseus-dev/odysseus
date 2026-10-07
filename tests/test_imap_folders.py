"""Regression coverage for dynamic LIST discovery and special-use resolution."""

import pytest

from src.imap_folders import (
    folder_role_from_name,
    list_folders,
    parse_list_response,
    resolve_folder,
    resolve_from_folders,
)


class ListConnection:
    def __init__(self, lines=(), status="OK"):
        self.lines = lines
        self.status = status
        self.calls = 0

    def list(self):
        self.calls += 1
        return self.status, self.lines


def test_localized_sent_uses_flags_and_preserves_wire_name():
    folder = parse_list_response(rb'(\HasNoChildren \Sent) "/" "[Gmail]/Gesendet"')
    assert folder == {
        "name": "[Gmail]/Gesendet",
        "flags": [r"\HasNoChildren", r"\Sent"],
        "role": "sent",
        "display_name": "[Gmail]/Gesendet",
    }


@pytest.mark.parametrize(
    ("flag", "role"),
    [
        (r"\sEnT", "sent"),
        (r"\Drafts", "drafts"),
        (r"\All", "all"),
        (r"\Archive", "archive"),
        (r"\Flagged", "starred"),
        (r"\Junk", "junk"),
        (r"\Trash", "trash"),
        (r"\Important", "important"),
    ],
)
def test_special_use_attributes_are_case_insensitive(flag, role):
    assert parse_list_response(f'({flag}) NIL "Localized folder"')["role"] == role


def test_escaped_mailbox_names_are_unquoted_once():
    folder = parse_list_response(rb'() "/" "Label \"Needs Reply\" \\ archive"')
    assert folder["name"] == 'Label "Needs Reply" \\ archive'
    assert folder["role"] == ""


def test_literal_mailbox_name_preserves_spaces_and_quotes():
    name = b'Label "Needs Reply"'
    folder = parse_list_response((b'() "/" {' + str(len(name)).encode() + b'}', name))
    assert folder["name"] == name.decode()


def test_mailbox_flag_text_cannot_impersonate_special_use():
    folder = parse_list_response(rb'() "/" "Label \\Sent"')
    assert folder["flags"] == []
    assert folder["role"] == ""


@pytest.mark.parametrize(
    "line",
    [None, b"", b")", b"not a LIST response", b'() "/" {4}',
     b'() "/" "unterminated', b'() "/" two words', b'() "/" ""',
     (b'() "/" {4}', b"too long"), b'() "/" "line\r\nbreak"'],
)
def test_malformed_list_records_are_ignored(line):
    assert parse_list_response(line) is None


def test_modified_utf7_is_decoded_only_for_display():
    folder = parse_list_response(rb'(\Sent) "/" "[Gmail]/&ZeVnLIqe-"')
    assert folder["name"] == "[Gmail]/&ZeVnLIqe-"
    assert folder["display_name"] == "[Gmail]/日本語"
    assert folder["role"] == "sent"
    assert parse_list_response(rb'() "/" "Research &- development"')["display_name"] == "Research & development"
    assert parse_list_response(rb'() "/" "Broken &a- label"')["display_name"] == "Broken &a- label"


def test_utf8_mailbox_name_can_be_listed_without_ascii_crash():
    folder = parse_list_response('() "/" "送信済み"'.encode())
    assert folder["name"] == folder["display_name"] == "送信済み"


def test_list_skips_unselectable_parents_malformed_and_duplicate_rows():
    conn = ListConnection([
        rb'(\Noselect \HasChildren) "/" "[Gmail]"',
        rb'(\NonExistent) "/" "missing"',
        rb'(\HasNoChildren) "/" "INBOX"',
        rb'(\HasNoChildren \Sent) "/" "[Gmail]/Gesendet"',
        rb'(\HasNoChildren) "/" "INBOX"',
        None,
    ])
    assert [folder["name"] for folder in list_folders(conn)] == ["INBOX", "[Gmail]/Gesendet"]
    assert conn.calls == 1


def test_empty_list_does_not_invent_mailboxes():
    assert list_folders(ListConnection([None])) == []
    assert list_folders(ListConnection([])) == []


@pytest.mark.parametrize("status", ["NO", "BAD", b"NO"])
def test_rejected_list_is_an_error_instead_of_an_empty_success(status):
    with pytest.raises(RuntimeError, match="folder listing failed"):
        list_folders(ListConnection([b'() "/" "INBOX"'], status=status))


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Sent", "sent"), ("INBOX.Sent", "sent"), ("[Gmail]/Sent Mail", "sent"),
     ("INBOX/Drafts", "drafts"), ("Junk E-Mail", "junk"),
     ("Sent invoices", ""), ("Combine", ""), ("Draft proposal", ""),
     ("Archived project", ""), ("Deleted budget", "")],
)
def test_name_fallback_uses_exact_aliases_and_hierarchy_leaves(name, expected):
    assert folder_role_from_name(name) == expected


def test_flagged_sent_wins_over_generic_english_alias_and_misleading_labels():
    folders = list_folders(ListConnection([
        rb'() "/" "Sent invoices"',
        rb'() "/" "Sent"',
        rb'(\Sent) "/" "[Gmail]/Gesendet"',
    ]))
    assert resolve_from_folders(folders, "Sent", "sent") == "[Gmail]/Gesendet"
    assert resolve_from_folders(folders, "Sent invoices", "sent") == "Sent invoices"


def test_explicit_full_provider_mailbox_is_preserved():
    folders = list_folders(ListConnection([
        rb'() "/" "INBOX.Sent"', rb'(\Sent) "/" "[Gmail]/Gesendet"',
    ]))
    assert resolve_from_folders(folders, "INBOX.Sent", "sent") == "INBOX.Sent"


def test_alias_resolution_only_chooses_names_that_exist():
    conn = ListConnection([rb'() "." "INBOX.Sent Items"'])
    assert resolve_folder(conn, "Sent", "sent") == "INBOX.Sent Items"
    assert resolve_folder(ListConnection([]), "Sent", "sent") == "Sent"
    assert resolve_folder(ListConnection([rb'() "/" "Only label"']), "", "sent") == ""


def test_archive_role_prefers_archive_then_all_mail():
    all_only = list_folders(ListConnection([rb'(\All) "/" "[Gmail]/Alle Nachrichten"']))
    assert resolve_from_folders(all_only, "Archive", "archive") == "[Gmail]/Alle Nachrichten"
    both = all_only + [parse_list_response(rb'(\Archive) "/" "Old messages"')]
    assert resolve_from_folders(both, "Archive", "archive") == "Old messages"


def test_explicit_folder_remains_usable_when_list_is_unavailable():
    assert resolve_folder(ListConnection(status="NO"), "Known custom folder") == "Known custom folder"
    assert resolve_folder(object(), "INBOX") == "INBOX"
    assert resolve_folder(ListConnection([]), "Missing custom folder") == "Missing custom folder"


def test_shared_detectors_use_server_roles_and_keep_append_fallbacks():
    from routes.email_helpers import _detect_drafts_folder, _detect_sent_folder, _detect_spam_folder

    conn = ListConnection([
        rb'(\Sent) "/" "[GoogleMail]/Gesendet"',
        rb'(\Drafts) "/" "[GoogleMail]/Entw&APw-rfe"',
        rb'(\Junk) "/" "[GoogleMail]/Unerw&APw-nscht"',
    ])
    assert _detect_sent_folder(conn) == "[GoogleMail]/Gesendet"
    assert _detect_drafts_folder(conn) == "[GoogleMail]/Entw&APw-rfe"
    assert _detect_spam_folder(conn) == "[GoogleMail]/Unerw&APw-nscht"
    assert _detect_sent_folder(ListConnection(status="NO")) == "Sent"
    assert _detect_drafts_folder(ListConnection([])) == "Drafts"
    assert _detect_spam_folder(ListConnection([])) is None
