from services.youtube.youtube_handler import extract_youtube_id


def test_extract_youtube_id_handles_non_string_url():
    # urllib.parse.urlparse raises AttributeError on a non-string, so a non-str
    # url (e.g. from a JSON-decoded request body) crashed the extractor instead
    # of being treated as "not a YouTube URL".
    assert extract_youtube_id(123) is None
    assert extract_youtube_id({"bad": 1}) is None
    assert extract_youtube_id(["https://youtu.be/x"]) is None


def test_extract_youtube_id_still_parses_real_urls():
    assert extract_youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_youtube_id("https://www.youtube.com/watch?v=abc123") == "abc123"
