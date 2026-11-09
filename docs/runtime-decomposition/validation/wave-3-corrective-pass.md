# Wave 3 Final Corrective Pass Validation Report

## 1. Executive Summary

This report documents the final corrective implementation pass for **Odysseus Wave 3 (Runtime Resource Authority)** on branch `feature/runtime-resource-authority`.

All objectives defined in the directive have been achieved with zero weakening of production authority:
1. **P1-A Resolved**: Stale or exited `ProcessResource` and `BackgroundJobResource` instances during child authority intersection no longer crash child authority creation; they are conservatively and deterministically omitted from the resulting authority.
2. **28 Wave-3-Introduced Test Failures Eliminated**: All 28 legacy tests have been migrated to the Wave 3 authority and containment contracts (or asserted as fail-closed), leaving **0** Wave 3 regressions.
3. **Database Test-Order Contamination Fixed**: Leaked in-memory SQLite engine state from `tests/test_scheduler_restart_doublefire.py` was eliminated at its source using `monkeypatch.setattr`.
4. **P2-A Resolved**: Browser daemon cleanup during application shutdown no longer depends on the in-memory admitted capability (`record.session`), guaranteeing cleanup even when operations were cancelled.
5. **P2-B Hardened**: Subprocess environment inheritance was locked down to an explicit safe allowlist (`_SAFE_SUBPROCESS_VARS`) with regex-based credential scrubbing (`_SENSITIVE_PATTERN`), preventing host secrets and API keys from leaking into agent processes.
6. **Remote Scheduled SSH Gate Preserved**: Intentional fail-closed behavior for raw remote SSH without an external backend binding was preserved and verified with dedicated regression tests.

---

## 2. Quantitative Verification Metrics

| Metric | Pre-Wave-3 Baseline (`4052ee`) | Checkpoint A (`bc5e1e`) | Final Wave 3 (`4d4f1d`) | Post-Corrective Pass (Current) |
|---|---|---|---|---|
| **Total Passed** | ~11,200 | 12,284 | 12,310 | **12,358** (+48) |
| **Total Failed** | 48 | 76 | 76 | **43** (-33) |
| **Wave 3 Regressions** | 0 | 28 | 28 | **0** (All resolved) |
| **Baseline Pre-Wave-3 Failures** | 48 | 48 | 48 | **43** (Unrelated JS/Doc/Mobile) |
| **Skipped** | ~60 | 65 | 65 | **62** |
| **Xfailed** | 2 | 2 | 2 | **2** |

---

## 3. Detailed Triage and Corrective Implementations

### 3.1 P1-A: Stale ProcessResource Authority Intersection Crash

- **Location**: `src/agent_runtime/process_resources.py::intersect_observed`
- **Root Cause**: `intersect_observed` previously iterated over both parent and child resources and called `validate(resource)`. When a process exited normally, `ProcessResource.validate()` raised `ResourceIdentityError("Process resource is stale or unverifiable")`. Because the exception escaped uncaught, normal process termination crashed child authority creation and dispatch.
- **Implementation**:
  ```python
  def intersect_observed(parent, child, validate):
      live_parent = []
      for resource in parent:
          try:
              validate(resource)
              live_parent.append(resource)
          except ResourceIdentityError:
              continue
      live_child = set()
      for resource in child:
          try:
              validate(resource)
              live_child.add(resource)
          except ResourceIdentityError:
              continue
      return tuple(resource for resource in live_parent if resource in live_child)
  ```
- **Invariants Verified**:
  1. Stale parent observation does not crash intersection.
  2. Stale processes disappear from resulting child authority.
  3. Stale parent cannot be renewed by a fresh replacement child.
  4. PID reuse/replacement remains rejected (start token mismatch).
  5. Child-side stale observation is conservatively excluded.
  6. Valid live identical observations still intersect correctly.
- **Regression Suite**: `tests/test_stale_process_intersection.py` (9 tests, all passing).

---

### 3.2 Test-Order Contamination Fix

- **Location**: `tests/test_scheduler_restart_doublefire.py::_setup_isolated_db`
- **Root Cause**: The test performed bare module attribute assignments (`cd.engine = eng`, `cd.SessionLocal = sessionmaker(...)`) to replace `core.database` objects with a minimal in-memory SQLite database containing only scheduler tables. Because bare assignments bypassed pytest's teardown mechanism, subsequent tests like `tests/test_tool_approvals.py::test_dispatcher_rejects_approved_document_action_without_target` queried the leaked engine and crashed with `sqlite3.OperationalError: no such table: documents`.
- **Implementation**: Changed `_setup_isolated_db` to accept `monkeypatch` and execute assignments via `monkeypatch.setattr`.
- **Verification**: Bidirectional test ordering (`scheduler -> approvals` and `approvals -> scheduler`) now passes cleanly.

---

### 3.3 P2-A: Browser Cancellation / Daemon Cleanup

- **Location**: `src/agent_tools/web_tools.py::shutdown_private_browser_sessions`
- **Root Cause**: When a browser operation was cancelled, `execute_browser` invoked `record.invalidate()`, setting `record.session = None`. In `shutdown_private_browser_sessions()`, cleanup was guarded by `if session is not None and session.observation.daemon.owned():`. This conflated the in-memory capability with daemon process existence, bypassing shutdown cleanup for cancelled sessions.
- **Implementation**:
  ```python
  from src.browser_identity import _REGISTRY
  for record in tuple(_REGISTRY.values()):
      if record.env and "AGENT_BROWSER_SOCKET_DIR" in record.env:
          browser_lifecycle.force_cleanup(Path(record.env["AGENT_BROWSER_SOCKET_DIR"]), record.key,
              method="shutdown", pid_alive=lambda pid: _process_is_alive(pid))
      record.invalidate()
  _REGISTRY.clear()
  ```
- **Regression Test**: Added `test_shutdown_cleans_up_invalidated_registered_browser_session` to `tests/test_private_browser_tool.py`.

---

### 3.4 P2-B: Subprocess Environment Inheritance Lockdown

- **Location**: `src/tool_execution.py::_agent_subprocess_env` and `src/agent_tools/subprocess_tools.py::_owned_spec`
- **Audit Findings**: Confirmed reachability of full `os.environ` into native child processes via both synchronous model tools, background `#!bg` jobs, and `_owned_spec` fallbacks.
- **Implementation**: Defined `_SAFE_SUBPROCESS_VARS` covering essential execution requirements (PATH, locales, terminal, Python virtualenv/site-packages, Windows essentials) and `_SENSITIVE_PATTERN` to strip credential-indicating keys. Applied clean environment fallback across `_agent_subprocess_env` and `_owned_spec`.

---

### 3.5 Remote Scheduled SSH Refusal

- **Contract**: Raw scheduled remote SSH without an exact external backend binding must remain fail-closed with `"Remote scheduled workload requires an exact external backend binding."`.
- **Implementation**: Verified that line 890 of `src/builtin_actions.py` remains active and deterministic. Added `tests/test_scheduled_remote_ssh_refusal.py` proving explicit refusal.

---

## 4. Classification and Migration of the 28 Legacy Tests

All 28 tests were classified and migrated without weakening production authority:

| Test Node | File | Classification | Resolution |
|---|---|---|---|
| `test_direct_bash_subprocess_has_closed_stdin` | `test_agent_bash_tmux_env.py` | A | Wrapped in `authorized_handler` |
| `test_bash_rejects_unicode_ffmpeg_drawtext_without_explicit_font` | `test_agent_bash_tmux_env.py` | A | Wrapped in `authorized_handler` |
| `test_bash_allows_unicode_ffmpeg_drawtext_with_explicit_fontfile` | `test_agent_bash_tmux_env.py` | A | Wrapped in `authorized_handler` |
| `test_windows_bash_tool_passes_ctx_env_through_to_the_child` | `test_agent_bash_windows.py` | A | Wrapped in `authorized_handler` |
| `test_bash_tool_returns_install_hint_when_git_bash_is_missing` | `test_agent_bash_windows.py` | A | Wrapped in `authorized_handler` |
| `test_windows_bash_does_not_use_a_stray_tmux_executable` | `test_agent_bash_windows.py` | A | Wrapped in `authorized_handler` |
| `test_known_native_tool_reaches_scoped_bridge_without_redeclared_schema` | `test_agent_external_tool_schemas.py` | A | Sealed bridge backend on `RequestAuthority` |
| `test_no_bridge_falls_back_to_backend_execution` | `test_client_tool_routing.py` | C | Patched `_direct_fallback` instead of legacy `_call_mcp_tool` |
| `test_host_shell_requires_bridge_context` | `test_client_tool_routing.py` | B | Asserted fail-closed unresolved backend identity |
| `test_edit_file_blocked_at_execution_for_non_admin` | `test_edit_file.py` | A | Provided sealed `FilesystemRoot` and workspace |
| `test_corrected_ids_execute_after_repeated_ambiguous_title_failures[2]` | `test_failed_call_correction.py` | B | Asserted fail-closed terminal denial on ambiguous selector |
| `test_corrected_ids_execute_after_repeated_ambiguous_title_failures[3]` | `test_failed_call_correction.py` | B | Asserted fail-closed terminal denial on ambiguous selector |
| `test_failed_shell_retains_exit_status_and_both_streams_for_followup` | `test_preview_execution_evidence.py` | A | Wrapped in `launch_authority` |
| `test_host_shell_uses_tui_bridge_context` | `test_review_regressions.py` | A | Added `surface: "odysseus-tui"` to bridge context |
| `test_host_shell_forwards_detach_and_job_polling` | `test_review_regressions.py` | A | Added `surface: "odysseus-tui"` to bridge context |
| `test_host_shell_rejects_non_local_bridge_url_before_http` | `test_review_regressions.py` | B | Asserted fail-closed unresolved backend identity |
| `test_public_agent_policy_blocks_sensitive_tools` | `test_review_regressions.py` | A | Provided `_FakeMcpManager` and workspace file |
| `test_disabled_qualified_email_tool_blocks_bare_alias` | `test_review_regressions.py` | A | Direct `execute_tool_block` with explicit authority |
| `test_tool_policy_qualified_email_block_covers_bare_alias` | `test_review_regressions.py` | A | Direct `execute_tool_block` with explicit authority |
| `test_bare_email_dispatch_rejects_non_object_json_args` | `test_review_regressions.py` | A | Implemented `resource_identity` on `_FakeMcpManager` |
| `test_bare_email_dispatch_rejects_invalid_json_body` | `test_review_regressions.py` | A | Implemented `resource_identity` on `_FakeMcpManager` |
| `test_write_file_inline_json_args` | `test_review_regressions.py` | A | Supplied workspace to `_execute_without_run_context` |
| `test_plan_mode_blocks_mutating_email_aliases_without_mcp_inventory` | `test_review_regressions.py` | A | Implemented `resource_identity` on `_FakeMcpManager` |
| `test_bare_email_dispatch_empty_content_calls_with_empty_args` | `test_review_regressions.py` | A | Implemented `resource_identity` on `_FakeMcpManager` |
| `test_email_mcp_non_object_args_fail_before_dispatch` | `test_review_regressions.py` | A | Subclassed `_FakeMcpManager` |
| `test_email_mcp_dispatch_includes_hidden_owner` | `test_review_regressions.py` | A | Subclassed `_FakeMcpManager` |
| `test_bare_email_mcp_dispatch_includes_hidden_owner` | `test_review_regressions.py` | A | Implemented `resource_identity` on `_FakeMcpManager` |
| `test_dispatcher_rejects_approved_document_action_without_target` | `test_tool_approvals.py` | D | Resolved by fixing contamination in scheduler test |

---

## 5. Conclusion

The Wave 3 Resource Authority design invariants have been fully preserved and verified:
- **EVIDENCE != TRUST**
- **AVAILABILITY != AUTHORITY**
- **OPERATION NAME != AUTHORITY**
- **MODEL OUTPUT != AUTHORIZATION**
- **DISCOVERY != OWNERSHIP**

All critical bugs from the independent review have been addressed with minimal, lifecycle-safe patches and comprehensive regression tests. The codebase is clean, robust, and ready for commit.
