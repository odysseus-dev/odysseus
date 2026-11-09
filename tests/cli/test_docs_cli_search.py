"""Regression for #2123 (CLI part): `odysseus-docs search` must match
multi-word queries and treat % / _ literally.

Old code built one ``%{query}%`` LIKE over title/current_content, so
"machine learning" only matched the exact adjacent phrase, and a literal
"%" matched every document.
"""

import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import Document
from tests.helpers.cli_loader import load_script


def _cli_with_documents(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'docs_cli_search.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = session_factory()
    try:
        db.add_all(
            [
                Document(
                    id="doc-multiword",
                    title="ML notes",
                    current_content="machine notes on learning",
                ),
                Document(
                    id="doc-percent",
                    title="Fabric",
                    current_content="100% cotton",
                ),
                Document(
                    id="doc-underscore",
                    title="sale_2024",
                    current_content="spring campaign",
                ),
                Document(
                    id="doc-backslash",
                    title="Path",
                    current_content=r"C:\photos\sunset",
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    cli = load_script("odysseus-docs")
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    return cli


def _search_ids(cli, capsys, query):
    cli.cmd_search(SimpleNamespace(query=query, limit=50, pretty=False))
    return {doc["id"] for doc in json.loads(capsys.readouterr().out)}


def test_search_matches_words_in_any_order(monkeypatch, tmp_path, capsys):
    cli = _cli_with_documents(monkeypatch, tmp_path)

    assert _search_ids(cli, capsys, "machine learning") == {"doc-multiword"}


def test_search_escapes_percent_wildcard(monkeypatch, tmp_path, capsys):
    cli = _cli_with_documents(monkeypatch, tmp_path)

    assert _search_ids(cli, capsys, "%") == {"doc-percent"}


def test_search_escapes_underscore_wildcard(monkeypatch, tmp_path, capsys):
    cli = _cli_with_documents(monkeypatch, tmp_path)

    # Unescaped, "_" matches any single char so every document matches.
    assert _search_ids(cli, capsys, "_") == {"doc-underscore"}


def test_search_escapes_backslash(monkeypatch, tmp_path, capsys):
    cli = _cli_with_documents(monkeypatch, tmp_path)

    assert _search_ids(cli, capsys, "\\") == {"doc-backslash"}


def test_search_whitespace_query_returns_all(monkeypatch, tmp_path, capsys):
    cli = _cli_with_documents(monkeypatch, tmp_path)

    assert _search_ids(cli, capsys, "   ") == {
        "doc-multiword",
        "doc-percent",
        "doc-underscore",
        "doc-backslash",
    }
