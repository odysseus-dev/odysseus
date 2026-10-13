from routes.chat_helpers import youtube_prefetch_sources


def test_successful_youtube_preprocessing_has_visible_provenance():
    sources = youtube_prefetch_sources(
        "Summarize https://youtu.be/jNQXAC9IVRw",
        [
            "instructions",
            "[YOUTUBE VIDEO TRANSCRIPT]\nTitle: Me at the zoo\n[END TRANSCRIPT]",
            "[YOUTUBE VIDEO COMMENTS — Top 2 by popularity]\n[END COMMENTS]",
        ],
    )

    assert sources == [{
        "url": "https://youtu.be/jNQXAC9IVRw",
        "title": "Me at the zoo",
        "acquisition": "automatic_youtube_context",
        "evidence": "transcript+comments",
    }]


def test_failed_youtube_preprocessing_does_not_claim_evidence():
    assert youtube_prefetch_sources(
        "Summarize https://youtu.be/jNQXAC9IVRw",
        ["Transcript unavailable; use comments if available."],
    ) == []
