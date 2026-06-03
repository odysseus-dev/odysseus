from src.youtube_handler import format_comments_for_context


def test_format_comments_skips_non_dict_entries():
    # The comments list comes from json.loads of yt-dlp output; a malformed
    # entry (None or a bare string) made the old loop call .get on a non-dict
    # and crash, losing every well-formed comment in the same batch.
    data = {"success": True, "comments": [
        {"author": "alice", "text": "great video", "likes": 10},
        "junk-row",
        None,
        {"author": "bob", "text": "agreed", "likes": 2},
    ]}
    out = format_comments_for_context(data, "https://youtu.be/x")
    assert "@alice" in out
    assert "@bob" in out
    assert "junk-row" not in out
