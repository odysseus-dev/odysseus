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
    """Verify that _make_compact_section preserves code blocks and crops text to the first sentence."""
    verbose_text = """\
```test_tool
<argument>
```
This is a test description. It contains multiple sentences.
We want only the first sentence to survive this compaction.
Here is an example code snippet:
```
example usage
```"""
    compact = _make_compact_section("test_tool", verbose_text)
    assert "```test_tool" in compact
    assert "<argument>" in compact
    assert "This is a test description." in compact
    assert "It contains multiple" not in compact
    assert "example usage" not in compact


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
