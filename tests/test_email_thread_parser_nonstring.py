from src.email_thread_parser import parse_thread


def test_parse_thread_ignores_non_string_bodies():
    assert parse_thread(123, {"bad": True}) is None
    assert parse_thread(["<blockquote>bad</blockquote>"], None) is None


def test_parse_thread_still_handles_plaintext_quotes():
    turns = parse_thread(None, "hi\n\nOn Tue, Alice wrote:\n> older")

    assert turns
    assert turns[0]["level"] == 0
