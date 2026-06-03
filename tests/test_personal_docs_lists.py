from src import personal_docs


def test_string_list_filters_non_strings():
    assert personal_docs._string_list(["/tmp/a", None, 3, "/tmp/b"]) == ["/tmp/a", "/tmp/b"]
    assert personal_docs._string_list(None) == []
