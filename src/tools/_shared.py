"""
src/tools/_shared.py

Shared lazy seam for the split tool modules (management_tools, model_tools,
calendar_tools, vault_tools). Active-document / active-model state lives in
src/agent_tools/document_tools.py alongside the document tool handlers — the
split modules must not keep a second copy of that mutable state.
"""


class _LazyToolImpl:
    """Lazy proxy for the src.tool_implementations aggregator.

    The split tool modules need to reach a few shared / monkeypatch-sensitive
    names that live on tool_implementations (get_mcp_manager, _parse_tool_args,
    _internal_headers / _INTERNAL_BASE). Importing
    tool_implementations eagerly at submodule load creates a circular import
    when a submodule is imported *before* tool_implementations (the re-export
    block in tool_implementations runs while the submodule is still partially
    initialised). Resolving the module on first attribute access — at call time,
    never at import time — breaks the cycle while preserving the live-attribute
    binding that test monkeypatches rely on.
    """

    __slots__ = ("_mod",)

    def __init__(self):
        self._mod = None

    def __getattr__(self, name):
        mod = self._mod
        if mod is None:
            import src.tool_implementations as mod  # resolved once, at call time
            object.__setattr__(self, "_mod", mod)
        return getattr(mod, name)


# Shared lazy handle the split tool modules import as `_ti`.
ti = _LazyToolImpl()
