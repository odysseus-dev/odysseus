"""Single source of truth for tools supplied by the Odysseus TUI host bridge."""

TUI_ROUTED_BRIDGE_TOOL_NAMES = frozenset({
    "bash", "python", "grep", "ls", "glob", "list_dir", "find_files",
    "read_file", "write_file", "edit_file",
})

TUI_CLIENT_TOOL_NAMES = frozenset({
    *TUI_ROUTED_BRIDGE_TOOL_NAMES,
    "apply_patch",
    "host_shell",
})
