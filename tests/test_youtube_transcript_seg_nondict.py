from src.youtube_handler import format_transcript_for_context


def test_format_transcript_skips_non_dict_segments():
    # segments come from the parsed transcript JSON; a malformed entry (None or
    # a bare string) made seg['timestamp'] raise TypeError and lose the whole
    # timestamped transcript.
    data = {
        "success": True, "transcript": "full text", "video_id": "x",
        "segments": [
            {"timestamp": "0:01", "text": "hello"},
            "junk-seg",
            None,
            {"timestamp": "0:05", "text": "world"},
        ],
    }
    out = format_transcript_for_context(data, "https://youtu.be/x")
    assert "[0:01] hello" in out
    assert "[0:05] world" in out
    assert "junk-seg" not in out
