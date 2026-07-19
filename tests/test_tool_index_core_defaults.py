from src.tool_index import get_always_available_tools, CORE_DEFAULT_TOOLS
import src.settings as settings_mod

def test_core_tools_always_available(monkeypatch):
    # Simulate a user with no optionally enabled tools
    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: [])
    
    tools = get_always_available_tools()
    
    # Even with empty enabled_tools, core tools must be present
    for core_tool in CORE_DEFAULT_TOOLS:
        assert core_tool in tools

def test_optional_tools_mixed_with_core(monkeypatch):
    # Simulate a user enabling an optional but safe tool
    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: ["web_search"])
    
    tools = get_always_available_tools()
    
    assert "web_search" in tools
    for core_tool in CORE_DEFAULT_TOOLS:
        assert core_tool in tools

def test_unsafe_optional_tools_are_filtered(monkeypatch):
    # Simulate a user somehow having an unsafe tool in enabled_tools
    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: ["run_shell_command"])
    
    tools = get_always_available_tools()
    
    assert "run_shell_command" not in tools
    for core_tool in CORE_DEFAULT_TOOLS:
        assert core_tool in tools
