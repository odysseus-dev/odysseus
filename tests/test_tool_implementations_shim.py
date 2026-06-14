"""Protection test: the tool_implementations compatibility shim must keep
re-exporting every top-level function that importers may depend on.

Guards the slice-1 split (tool_implementations.py -> src/tools/*) from
accidentally dropping a symbol. The list below is the ground-truth set of
top-level functions extracted from the original tool_implementations.py
before the split; the shim must re-export ALL of them so existing
`from src.tool_implementations import X` imports keep working.
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
    # 15 module-private helpers (importable by name too)
    "_cookbook_apply_retry_suggestion", "_cookbook_env_for_host",
    "_cookbook_kill_session", "_cookbook_register_task", "_cookbook_servers",
    "_ensure_served_endpoint", "_infer_serve_host", "_infer_serve_port",
    "_internal_headers", "_load_vault_config", "_parse_tool_args",
    "_resolve_cookbook_host", "_run_bw", "_scan_running_model_processes",
    "_skill_dump",
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
