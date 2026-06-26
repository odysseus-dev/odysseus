"""Protection test: the tool_implementations compatibility shim must keep
re-exporting every symbol importers depend on.

Guards the slice-1 split (tool_implementations.py -> src/tools/*) from
accidentally dropping a symbol. The contract is enforced by two
self-verifying tests, not by the hand-maintained list below:

* ``test_shim_reexports_every_domain_do_function`` discovers every ``do_*``
  from the domain modules and asserts reachability through the shim.
* ``test_every_facade_import_in_repo_resolves`` discovers every
  ``from src.tool_implementations import X`` site across first-party Python
  dirs (src/, tests/, routes/, ...) and asserts ``X`` resolves through the
  shim.

Both fail automatically if a re-export is forgotten (the do_* discovery
covers the tool surface; the import-site scan covers underscore helpers a
reviewer's P3 finding showed could otherwise slip through the list). The
``_EXPECTED`` list below is the curated historical surface (the original
module's top-level names), kept as a belt-and-suspenders check and as the
async-shape contract for ``do_*``; it is not the ground truth.
"""

import inspect

import src.tool_implementations as ti

# 33 do_* tool functions
_EXPECTED = [
    "do_adopt_served_model", "do_api_call", "do_app_api", "do_cancel_download",
    "do_download_model", "do_edit_image", "do_list_cached_models",
    "do_list_cookbook_servers", "do_list_downloads", "do_list_served_models",
    "do_list_serve_presets", "do_manage_calendar", "do_manage_contact",
    "do_manage_endpoints", "do_manage_mcp", "do_manage_notes",
    "do_manage_research", "do_manage_settings", "do_manage_skills",
    "do_manage_tasks", "do_manage_tokens", "do_manage_webhooks",
    "do_resolve_contact", "do_search_chats", "do_search_hf_models",
    "do_serve_model", "do_serve_preset", "do_stop_served_model",
    "do_tail_serve_output", "do_trigger_research", "do_vault_get",
    "do_vault_search", "do_vault_unlock",
    # module-private helpers (importable by name too)
    "_cookbook_apply_retry_suggestion", "_cookbook_env_for_host",
    "_cookbook_kill_session", "_cookbook_register_task", "_cookbook_servers",
    "_ensure_served_endpoint", "_infer_serve_host", "_infer_serve_port",
    "_internal_headers", "_load_vault_config", "_mcp_allowed_commands",
    "_parse_tool_args", "_resolve_cookbook_host", "_run_bw",
    "_scan_running_model_processes", "_skill_dump", "_string_arg",
    "_validate_cookbook_ssh_target",
    # active-email facade helpers (no do_* prefix); consumed by
    # routes/chat_routes.py — listed here because get_active_email has no
    # in-repo importer, so the import-site scan below can't see it alone.
    "set_active_email", "get_active_email", "clear_active_email",
]


def test_shim_reexports_all_top_level_symbols():
    """Every original top-level function must remain importable via the module."""
    missing = [name for name in _EXPECTED if not hasattr(ti, name)]
    assert not missing, f"shim dropped symbols: {missing}"


def test_do_functions_remain_async_through_shim():
    """Every do_* must remain a coroutine function through the shim."""
    for name in _EXPECTED:
        if name.startswith("do_"):
            obj = getattr(ti, name)
            assert inspect.iscoroutinefunction(obj), (
                f"{name} is not async via shim (got {type(obj).__name__})"
            )


# Domain modules that own tool implementations after the slice-1 split.
# The shim must re-export every public do_* from each so existing
# `from src.tool_implementations import do_X` imports keep resolving.
_DOMAIN_MODULES = (
    "src.tools.system",
    "src.tools.cookbook",
    "src.tools.search",
    "src.tools.notes",
    "src.tools.calendar",
    "src.tools.image",
    "src.tools.research",
    "src.tools.contacts",
    "src.tools.vault",
    "src.agent_tools.admin_tools",  # admin manage_* tools migrated here (#3629)
)


def test_shim_reexports_every_domain_do_function():
    """Auto-discovered guard: every do_* defined in a domain module must be
    reachable through the shim.

    The hand-maintained ``_EXPECTED`` list above can drift silently when a
    new tool is added to a domain module but not re-exported by the facade
    (exactly the omission a reviewer found post-split). This test discovers
    the ground truth from the domain modules themselves, so a forgotten
    re-export fails the build automatically. ``hasattr`` is used (not
    ``dir(ti)``) because the admin symbols are re-exported lazily via
    module ``__getattr__`` and therefore do not appear in ``dir(ti)``.
    """
    import importlib

    dropped = []
    for mod_name in _DOMAIN_MODULES:
        mod = importlib.import_module(mod_name)
        for name in dir(mod):
            if not name.startswith("do_"):
                continue
            if not inspect.iscoroutinefunction(getattr(mod, name, None)):
                continue
            if not hasattr(ti, name):
                dropped.append(f"{mod_name}.{name}")
    assert not dropped, f"shim dropped domain do_* (re-export forgotten): {dropped}"


def test_every_facade_import_in_repo_resolves():
    """Every ``from src.tool_implementations import X`` in any first-party
    Python dir (src/, tests/, routes/, ...) must resolve through the shim.

    This makes the module-docstring contract ("existing ``from
    src.tool_implementations import X`` imports keep working") self-verifying
    instead of reliant on the hand-maintained ``_EXPECTED`` list, which
    omitted three underscore helpers in a reviewer's P3 finding and can drift
    again. The import sites are enumerated with ``ast`` rather than checked
    at runtime because the invariant is *which names the rest of the
    codebase asks the facade for* — no runtime hook enumerates that set,
    only the import statements do (the narrow source-scanning exception to
    the behavioral-first rule). The per-name assertion is runtime
    (``hasattr``), so any forgotten re-export — helper or ``do_*`` — fails
    here automatically.
    """
    import ast
    import os
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    # Walk every first-party Python dir so route-level (and any future)
    # facade consumers are covered, not just src/ and tests/. Prune
    # non-source trees (venvs, caches, data, build artifacts) in-place.
    _SKIP_DIRS = {
        "__pycache__", "venv", "node_modules", "data", "logs",
        "odysseus.egg-info", "static", "specs", "licenses", "docker",
    }
    names = set()
    for root, _dirs, files in os.walk(repo):
        _dirs[:] = [d for d in _dirs if not (d.startswith(".") or d in _SKIP_DIRS)]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = Path(root) / fn
            text = path.read_text(encoding="utf-8")
            if "src.tool_implementations" not in text:
                continue
            try:
                tree = ast.parse(text, filename=str(path))
            except SyntaxError:
                continue  # unrelated to the facade contract
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "src.tool_implementations":
                    for alias in node.names:
                        if alias.name != "*":
                            names.add(alias.name)
    unresolved = sorted(n for n in names if not hasattr(ti, n))
    assert not unresolved, (
        f"facade consumers import names the shim does not re-export: {unresolved}"
    )
