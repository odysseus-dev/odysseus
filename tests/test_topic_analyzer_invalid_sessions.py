from types import SimpleNamespace

from src.topic_analyzer import analyze_topics


def test_analyze_topics_ignores_non_mapping_session_store():
    sm = SimpleNamespace(sessions=["bad"])

    assert analyze_topics(sm, owner="alice") == {"topics": [], "total_topics": 0}


def test_analyze_topics_skips_invalid_session_rows():
    sm = SimpleNamespace(sessions={
        "bad": "not-a-session",
        "good": {
            "owner": "alice",
            "name": "Good",
            "history": [{"role": "user", "content": "I wrote Python code."}],
        },
    })

    result = analyze_topics(sm, owner="alice")

    assert result["total_topics"] > 0
    assert result["topics"][0]["topic"] == "Technology"


def test_analyze_topics_accepts_object_session_rows():
    sm = SimpleNamespace(sessions={
        "s1": SimpleNamespace(
            owner="alice",
            name="Object session",
            archived=False,
            history=[SimpleNamespace(role="user", content="I wrote Python code.")],
        )
    })

    result = analyze_topics(sm, owner="alice")

    assert result["total_topics"] > 0
