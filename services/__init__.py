"""Service-layer exports with lazy loading.

Importing one service, such as ``services.hwfit``, must not initialize every
other service. The eager exports previously imported search, document,
research, memory, and shell stacks during any ``services.*`` import, making
Cookbook hardware/model discovery needlessly slow on a cold process.
"""

from importlib import import_module

_LAZY_EXPORTS = {
    "SearchService": ("search", "SearchService"),
    "SearchResult": ("search", "SearchResult"),
    "SearchResponse": ("search", "SearchResponse"),
    "DocsService": ("docs", "DocsService"),
    "DocChunk": ("docs", "DocChunk"),
    "IndexResult": ("docs", "IndexResult"),
    "ResearchService": ("research", "ResearchService"),
    "ResearchResult": ("research", "ResearchResult"),
    "ResearchSource": ("research", "ResearchSource"),
    "MemoryService": ("memory", "MemoryService"),
    "Memory": ("memory", "Memory"),
    "MemorySearchResult": ("memory", "MemorySearchResult"),
    "ShellService": ("shell", "ShellService"),
    "ShellResult": ("shell", "ShellResult"),
}


def __getattr__(name):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_EXPORTS))

__all__ = [
    # Search
    "SearchService",
    "SearchResult",
    "SearchResponse",
    # Docs
    "DocsService",
    "DocChunk",
    "IndexResult",
    # Research
    "ResearchService",
    "ResearchResult",
    "ResearchSource",
    # Memory
    "MemoryService",
    "Memory",
    "MemorySearchResult",
    # Shell
    "ShellService",
    "ShellResult",
]
