from services.youtube.youtube_handler import format_comments_for_context


def test_format_comments_skips_non_dict_entries():
    # comments come from json.loads of yt-dlp output; a malformed entry (None
    # or a bare string) made the old loop call .get on a non-dict and crash.
    data = {"success": True, "comments": [
        {"author": "alice", "text": "great", "likes": 4},
        "junk-row",
        None,
        {"author": "bob", "text": "nice", "likes": 1},
    ]}
    out = format_comments_for_context(data, "https://youtu.be/x")
    assert "@alice" in out and "@bob" in out
    assert "junk-row" not in out
