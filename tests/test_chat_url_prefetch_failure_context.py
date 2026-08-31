"""Automatic chat URL fetch failures must remain visible to the model."""

import logging
from types import SimpleNamespace

import pytest

import src.chat_processor as chat_processor


def _processor():
    return chat_processor.ChatProcessor(
        memory_manager=SimpleNamespace(load=lambda owner=None: []),
        personal_docs_manager=SimpleNamespace(rag_manager=None),
        skills_manager=None,
    )


def test_failed_url_prefetch_keeps_diagnostics_out_of_model_context(monkeypatch):
    monkeypatch.setattr(
        chat_processor,
        "fetch_webpage_content",
        lambda url: {
            "success": False,
            "error": "NetworkError: connection refused\nattacker supplied detail",
        },
    )

    preface, _, _ = _processor().build_context_preface(
        message=(
            "Please summarize "
            "https://example.invalid/report?token=LEAK_URL_MARKER"
        ),
        session=SimpleNamespace(endpoint_url="", model="", headers={}),
        use_web=False,
        use_rag=False,
        use_memory=False,
    )

    failure = next(
        message
        for message in preface
        if (message.get("metadata") or {}).get("source")
        == "web page fetch failure"
    )
    assert failure["metadata"]["trusted"] is False
    assert "was not read: the page was unavailable" in failure["content"]
    assert "connection refused" not in failure["content"]
    assert "attacker supplied detail" not in failure["content"]
    assert "LEAK_URL_MARKER" not in failure["content"]
    assert "LEAK_URL_MARKER" not in str(failure["metadata"])


def test_failed_url_prefetch_exposes_only_stable_http_status(monkeypatch):
    monkeypatch.setattr(
        chat_processor,
        "fetch_webpage_content",
        lambda url: {
            "success": False,
            "error": "HTTP 403: attacker-controlled response detail",
        },
    )

    preface, _, _ = _processor().build_context_preface(
        message="Read https://example.test/private",
        session=SimpleNamespace(endpoint_url="", model="", headers={}),
        use_web=False,
        use_rag=False,
        use_memory=False,
    )

    content = preface[-1]["content"]
    assert "server returned HTTP 403" in content
    assert "attacker-controlled" not in content


@pytest.mark.parametrize(
    ("error", "expected_category"),
    [
        (
            "TooLarge: response exceeded configured limit; attacker-controlled detail",
            "response exceeded the fetch size limit",
        ),
        (
            "Rate limit exceeded: attacker-controlled detail",
            "request was rate limited",
        ),
    ],
)
def test_failed_url_prefetch_exposes_only_stable_failure_category(
    monkeypatch, error, expected_category
):
    monkeypatch.setattr(
        chat_processor,
        "fetch_webpage_content",
        lambda url: {"success": False, "error": error},
    )

    preface, _, _ = _processor().build_context_preface(
        message="Read https://example.test/report",
        session=SimpleNamespace(endpoint_url="", model="", headers={}),
        use_web=False,
        use_rag=False,
        use_memory=False,
    )

    content = preface[-1]["content"]
    assert expected_category in content
    assert "attacker-controlled" not in content


def test_unexpected_url_prefetch_exception_does_not_abort_context(monkeypatch):
    def fail(url):
        raise RuntimeError("socket detail must stay in logs")

    monkeypatch.setattr(chat_processor, "fetch_webpage_content", fail)

    preface, _, _ = _processor().build_context_preface(
        message="Review https://example.test/report",
        session=SimpleNamespace(endpoint_url="", model="", headers={}),
        use_web=False,
        use_rag=False,
        use_memory=False,
    )

    content = preface[-1]["content"]
    assert "was not read: the page was unavailable" in content
    assert "socket detail" not in content


def test_unexpected_url_prefetch_exception_logs_only_stable_diagnostic(
    monkeypatch, caplog
):
    def fail(url):
        raise RuntimeError("transport detail LEAK_EXCEPTION_MARKER")

    monkeypatch.setattr(chat_processor, "fetch_webpage_content", fail)

    with caplog.at_level(logging.WARNING, logger=chat_processor.logger.name):
        _processor().build_context_preface(
            message=(
                "Review https://example.test/report?token=LEAK_URL_MARKER"
            ),
            session=SimpleNamespace(endpoint_url="", model="", headers={}),
            use_web=False,
            use_rag=False,
            use_memory=False,
        )

    assert "Automatic URL fetch failed while building context" in caplog.text
    assert "LEAK_URL_MARKER" not in caplog.text
    assert "LEAK_EXCEPTION_MARKER" not in caplog.text


def test_successful_url_prefetch_keeps_existing_content_shape(monkeypatch):
    monkeypatch.setattr(
        chat_processor,
        "fetch_webpage_content",
        lambda url: {"success": True, "content": "page body"},
    )

    preface, _, _ = _processor().build_context_preface(
        message="Read https://example.test/page",
        session=SimpleNamespace(endpoint_url="", model="", headers={}),
        use_web=False,
        use_rag=False,
        use_memory=False,
    )

    page = next(
        message
        for message in preface
        if (message.get("metadata") or {}).get("source")
        == "web page: https://example.test/page"
    )
    assert page["metadata"]["trusted"] is False
    assert "page body" in page["content"]
