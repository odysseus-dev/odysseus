"""Tests for MCP server deduplication on add."""

import asyncio
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, McpServer
from routes.mcp_routes import find_duplicate_mcp_server


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _add_server(db, *, sid, name, transport, command=None, args=None, url=None):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    srv = McpServer(
        id=sid,
        name=name,
        transport=transport,
        command=command,
        args=json.dumps(args) if args is not None else None,
        url=url,
        is_enabled=True,
        created_at=now,
        updated_at=now,
    )
    db.add(srv)
    db.commit()
    return srv


def test_find_duplicate_http_matches_url_without_trailing_slash(db_session):
    _add_server(
        db_session,
        sid="abc12345",
        name="MCP Web Scraper",
        transport="http",
        url="http://192.168.40.95:9090/mcp",
    )

    dup = find_duplicate_mcp_server(
        db_session,
        transport="http",
        url="http://192.168.40.95:9090/mcp/",
    )

    assert dup is not None
    assert dup.id == "abc12345"


def test_find_duplicate_stdio_matches_command_and_args(db_session):
    _add_server(
        db_session,
        sid="stdio001",
        name="Filesystem",
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    )

    dup = find_duplicate_mcp_server(
        db_session,
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    )

    assert dup is not None
    assert dup.id == "stdio001"


def test_find_duplicate_allows_different_urls(db_session):
    _add_server(
        db_session,
        sid="http0001",
        name="Scraper A",
        transport="http",
        url="http://192.168.40.95:9090/mcp",
    )

    dup = find_duplicate_mcp_server(
        db_session,
        transport="http",
        url="http://192.168.40.95:9090",
    )

    assert dup is None


def test_manage_mcp_add_rejects_duplicate_stdio(monkeypatch, db_session):
    from src import tool_implementations

    _add_server(
        db_session,
        sid="existing",
        name="Filesystem",
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    )

    monkeypatch.setattr(tool_implementations, "get_mcp_manager", lambda: None)
    monkeypatch.setattr("core.database.SessionLocal", lambda: db_session)

    result = asyncio.run(
        tool_implementations.do_manage_mcp(
            json.dumps(
                {
                    "action": "add",
                    "name": "Filesystem copy",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                }
            )
        )
    )

    assert result["exit_code"] == 1
    assert result["existing_id"] == "existing"
    assert db_session.query(McpServer).count() == 1
