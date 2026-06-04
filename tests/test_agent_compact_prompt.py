import os
import sys
import logging
from unittest.mock import MagicMock

import pytest

from src.agent_loop import (
    _make_compact_section,
    _detect_system_ram_gb,
    _build_system_prompt,
)
from src.settings import save_settings, get_setting


def test_make_compact_section_pruning():
    """Verify that _make_compact_section preserves description text and guidelines while stripping code blocks and lists."""
    verbose_text = """\
```test_tool
<argument>
```
This is a test description. It contains multiple sentences of guidelines.

Here is an example code snippet:
```
example usage
```
- Endpoint: /api/test/list
- Endpoint: /api/test/create
- Endpoint: /api/test/delete
"""
    compact = _make_compact_section("test_tool", verbose_text)
    assert "```test_tool" in compact
    assert "<argument>" in compact
    assert "This is a test description." in compact
    assert "It contains multiple sentences" in compact
    assert "example usage" not in compact
    assert "/api/test" not in compact
    assert "Here is an example" not in compact

    # Verify one-liner bullet point preservation
    one_liner = "- ```some_tool``` — A one line tool description."
    assert _make_compact_section("some_tool", one_liner) == one_liner


def test_detect_system_ram_gb():
    """Verify that _detect_system_ram_gb returns a valid positive float."""
    ram = _detect_system_ram_gb()
    assert isinstance(ram, float)
    assert ram >= 0.0


def test_build_system_prompt_compact_descriptions_reduction(monkeypatch):
    """Assert that enabling compact_descriptions reduces prompt character footprint."""
    monkeypatch.setattr("src.agent_loop.get_setting", lambda key, default=None: "auto")

    messages = [{"role": "user", "content": "run a bash check and search the web"}]
    
    # 1. Build prompt with compact_descriptions=False
    full_msgs, _ = _build_system_prompt(
        messages=messages,
        model="test-model",
        active_document=None,
        mcp_mgr=None,
        compact=False,
        compact_descriptions=False,
    )
    full_prompt_len = sum(len(m.get("content", "")) for m in full_msgs if m.get("role") == "system")

    # 2. Build prompt with compact_descriptions=True
    compact_msgs, _ = _build_system_prompt(
        messages=messages,
        model="test-model",
        active_document=None,
        mcp_mgr=None,
        compact=False,
        compact_descriptions=True,
    )
    compact_prompt_len = sum(len(m.get("content", "")) for m in compact_msgs if m.get("role") == "system")

    assert compact_prompt_len < full_prompt_len, f"Expected compact prompt ({compact_prompt_len}) to be smaller than full prompt ({full_prompt_len})"


def test_save_settings_warns_low_ram_full_mode(tmp_path, monkeypatch, caplog):
    """Assert that save_settings logs a warning when full mode is saved on a low-RAM system."""
    # Mock settings files paths
    monkeypatch.setattr("src.settings.SETTINGS_FILE", str(tmp_path / "settings.json"))
    
    # Mock hardware RAM query to return 8.0 GB (low RAM)
    fake_hardware = MagicMock()
    fake_hardware._get_ram_gb.return_value = 8.0
    sys.modules["services.hwfit.hardware"] = fake_hardware

    # Execute save_settings with profile='full'
    test_settings = {
        "agent_prompt_profile": "full",
        "default_model": "test",
    }
    
    with caplog.at_level(logging.WARNING):
        save_settings(test_settings)
        
    # Assert warn logs contain our hardware warning message
    warnings = [rec.message for rec in caplog.records if rec.levelno == logging.WARNING]
    assert any("Hardware Warning" in w for w in warnings), "Expected hardware warning about low RAM in logs"
    
    # Clean up mocked modules to avoid side-effects in other tests
    sys.modules.pop("services.hwfit.hardware", None)


class InterceptedArgs(Exception):
    def __init__(self, compact_descriptions):
        self.compact_descriptions = compact_descriptions


@pytest.mark.asyncio
async def test_stream_agent_loop_auto_profile_resolution(monkeypatch):
    from src.agent_loop import stream_agent_loop
    
    # Mock _build_system_prompt to intercept the compact_descriptions flag
    def mock_build_system_prompt(*args, **kwargs):
        raise InterceptedArgs(kwargs.get("compact_descriptions", False))
        
    monkeypatch.setattr("src.agent_loop._build_system_prompt", mock_build_system_prompt)
    monkeypatch.setattr("src.agent_loop.get_setting", lambda key, default=None: "auto")

    # Helper function to run the test case
    async def run_case(endpoint_url, model, ram, context_len):
        monkeypatch.setattr("src.agent_loop._detect_system_ram_gb", lambda: ram)
        # Mock get_context_length
        monkeypatch.setattr("src.model_context.get_context_length", lambda url, mdl: context_len)
        
        gen = stream_agent_loop(
            endpoint_url=endpoint_url,
            model=model,
            messages=[{"role": "user", "content": "hello"}],
        )
        try:
            await gen.__anext__()
        except InterceptedArgs as e:
            return e.compact_descriptions
        except Exception as e:
            pytest.fail(f"Unexpected exception: {e}")
        finally:
            await gen.aclose()

    # Case 1: Cloud endpoint (api.anthropic.com) with low RAM (8GB) and large context (200k)
    # Result should be False (no compaction)
    compact_1 = await run_case("https://api.anthropic.com/v1", "claude-3-5-sonnet", 8.0, 200000)
    assert compact_1 is False

    # Case 2: Local endpoint (localhost) with low RAM (8GB) and large context (128k)
    # Result should be False (no compaction based on local or system RAM)
    compact_2 = await run_case("http://localhost:11434/v1", "llama3", 8.0, 128000)
    assert compact_2 is False

    # Case 3: Local endpoint (localhost) with high RAM (32GB) and large context (128k)
    # Result should be False (no compaction because context is large)
    compact_3 = await run_case("http://localhost:11434/v1", "llama3", 32.0, 128000)
    assert compact_3 is False

    # Case 4: Cloud endpoint (api.anthropic.com) with high RAM (32GB) but small context (8k)
    # Result should be True (compaction due to small context window)
    compact_4 = await run_case("https://api.openai.com/v1", "gpt-3.5-turbo", 32.0, 8192)
    assert compact_4 is True

