#!/usr/bin/env bash
# Focused runtime gate; no model inference or benchmark fixture access.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export ODYSSEUS_TEST_STATIC_PORT=0
export PYTHONDONTWRITEBYTECODE=1
export PYTHON_DOTENV_DISABLED=1
export ODYSSEUS_DATA_DIR="${ODYSSEUS_DATA_DIR:-/tmp/odysseus-runtime-decomposition-test-state}"
exec "${ODYSSEUS_TEST_PYTHON:-python3}" -m pytest -q -p no:cacheprovider \
  tests/test_runtime_evidence_contract.py tests/test_agent_evidence.py \
  tests/test_completion_boundary.py \
  tests/test_nested_invocation_ownership.py \
  tests/test_agent_evidence_loop.py tests/test_agent_render_ownership.py \
  tests/test_agent_runs_terminal_order.py tests/test_agent_loop.py \
  tests/test_tool_task_cancelled_on_disconnect.py tests/test_turn_contract.py \
  tests/test_agent_turn_contract_boundaries.py tests/test_loop_breaker_runaway.py \
  tests/test_chat_route_tool_policy.py tests/test_agent_runtime_context.py \
  tests/test_external_context_tool_gate.py tests/test_workspace_confine.py \
  tests/test_private_browser_tool.py tests/test_bg_jobs_store.py tests/test_bg_job_tools.py \
  tests/test_context_budget.py tests/test_context_compactor.py \
  tests/test_context_compactor_nonstring.py tests/test_generation_budget.py \
  tests/test_foreground_model_routing.py tests/test_tool_policy.py \
  tests/test_execution_bridge.py tests/test_tool_approvals.py \
  tests/test_tool_approval_single_action_scope.py tests/test_tool_approval_task_scope.py \
  tests/test_mcp_text_error_normalization.py tests/test_mcp_email_search_error_transport.py \
  tests/test_tool_path_confinement.py tests/test_workspace_artifact_tool_floor.py \
  tests/test_native_unattended_workspace_floor.py tests/test_misfenced_read_file_tool_call.py \
  tests/test_builtin_mcp_pythonpath.py tests/test_python_tool_import_paths.py "$@"
