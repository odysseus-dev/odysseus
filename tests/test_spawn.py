"""Tests for src/agent/spawn.py"""
from __future__ import annotations
import pytest
from src.agent.spawn import (
    ReturnFormat, parse_return_header, ContextMode, SpawnConfig,
)


def test_return_format_parse_success():
    text = """**Status**: success
**Summary**: Found all relevant files

- src/main.py: entry point

**Files touched**: src/main.py
**Findings worth promoting**: Main entry point uses FastAPI"""
    result = parse_return_header(text)
    assert result.status == "success"
    assert result.summary == "Found all relevant files"


def test_return_format_parse_failed():
    text = """**Status**: failed
**Summary**: Could not connect to database"""
    result = parse_return_header(text)
    assert result.status == "failed"
    assert result.summary == "Could not connect to database"


def test_return_format_parse_partial():
    text = """**Status**: partial
**Summary**: Fixed 3 of 5 bugs"""
    result = parse_return_header(text)
    assert result.status == "partial"


def test_return_format_parse_blocked():
    text = """**Status**: blocked
**Summary**: Waiting for user input"""
    result = parse_return_header(text)
    assert result.status == "blocked"


def test_return_format_parse_no_header():
    text = "Just a normal response without headers."
    result = parse_return_header(text)
    assert result.status is None
    assert result.summary is None
    assert result.body == text


def test_return_format_parse_malformed():
    text = "**Status**: invalid_status\n**Summary**: test"
    result = parse_return_header(text)
    assert result.status is None


def test_context_mode_values():
    assert ContextMode.NONE.value == "none"
    assert ContextMode.STATE.value == "state"
    assert ContextMode.FULL.value == "full"


def test_spawn_config_defaults():
    config = SpawnConfig(agent_type="explore", task="Find all Python files", session_id="s1")
    assert config.agent_type == "explore"
    assert config.mode == "subagent"
    assert config.context_mode == "none"
    assert config.background is False
    assert config.timeout == 600.0


def test_spawn_config_with_allowlist():
    config = SpawnConfig(
        agent_type="explore", task="Search the codebase", session_id="s1",
        tool_allowlist={"read_file", "glob", "grep"},
    )
    assert config.tool_allowlist == {"read_file", "glob", "grep"}


def test_spawn_config_peer_mode():
    config = SpawnConfig(
        agent_type="general", task="Implement feature", session_id="s1",
        mode="peer", context_mode="full",
    )
    assert config.mode == "peer"
    assert config.context_mode == "full"
