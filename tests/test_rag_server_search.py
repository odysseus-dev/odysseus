"""Regression: rag_server exposes document search through manage_rag."""

import asyncio

import pytest

pytest.importorskip("mcp")

import mcp_servers.rag_server as rs


def test_search_returns_rag_results(monkeypatch):
    class FakeRag:
        def search(self, query, k=5):
            assert query == "future NAS"
            return [
                {
                    "metadata": {"filename": "notes.txt"},
                    "similarity": 0.9,
                    "document": "Future NAS plans are documented here.",
                }
            ]

    monkeypatch.setattr(rs, "_ensure_init", lambda: None)
    monkeypatch.setattr(rs, "_rag_manager", FakeRag())

    out = asyncio.run(
        rs.call_tool(
            "manage_rag",
            {
                "action": "search",
                "query": "future NAS",
            },
        )
    )

    assert "notes.txt" in out[0].text
    assert "Future NAS plans" in out[0].text
