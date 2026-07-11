"""Tool implementation package, split by domain (slice 1, #4082/#4071).

Public tool functions live in domain modules. ``src.tool_implementations``
re-exports from here for backward compatibility.
"""
from src.tools._common import _parse_tool_args  # noqa: F401
from src.tools.system import (  # noqa: F401
    do_manage_skills, _skill_dump, do_manage_tasks,
    do_api_call, do_app_api,
)
from src.tools.cookbook import (  # noqa: F401
    do_download_model, do_serve_model, do_list_served_models,
    do_stop_served_model, do_tail_serve_output, do_list_downloads,
    do_cancel_download, do_search_hf_models, do_adopt_served_model,
    do_list_cookbook_servers, do_list_serve_presets, do_serve_preset,
    do_list_cached_models,
    _cookbook_servers, _resolve_cookbook_host, _cookbook_env_for_host,
    _infer_serve_port, _infer_serve_host, _ensure_served_endpoint,
    _cookbook_register_task, _cookbook_apply_retry_suggestion,
    _scan_running_model_processes, _cookbook_kill_session,
    _MODEL_PROCESS_PATTERNS,
)
from src.tools.search import do_search_chats  # noqa: F401
from src.tools.notes import do_manage_notes  # noqa: F401
from src.tools.calendar import do_manage_calendar  # noqa: F401
from src.tools.image import do_edit_image  # noqa: F401
from src.tools.research import do_manage_research, do_trigger_research  # noqa: F401
from src.tools.contacts import do_resolve_contact, do_manage_contact  # noqa: F401
from src.tools.vault import (  # noqa: F401
    _load_vault_config, _run_bw,
    do_vault_search, do_vault_get, do_vault_unlock,
)
