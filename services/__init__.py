# services/__init__.py
"""
Service layer — plug-in capabilities for the chat core.

Agents: keep this package init import-light. Eager imports of search/docs/etc
pull httpx through core, which fails in the model-job worker when
``/app/calendar`` shadows the stdlib ``calendar`` module. Import submodules
directly (``from services.search import ...``).
"""

__all__: list[str] = []
