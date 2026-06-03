# Deferred Items — Phase 01

Out-of-scope discoveries logged during execution. NOT fixed here (scope boundary:
only auto-fix issues directly caused by the current task's changes).

## Pre-existing test failures (discovered Plan 01-01, Task 3)

**Status:** Pre-existing on branch `plan/phase-01-tooling-foundation`. NOT introduced by the
lockfile. Proven by running the full suite under both the unpinned `requirements.txt` baseline
and the new hash-pinned `requirements.lock` inside `python:3.12-slim`: both produce the
**identical** result — `20 failed, 1736 passed, 83 skipped`. The lock introduces zero new failures.

**Likely cause:** test-isolation / environment dependencies inside a clean container — e.g.
`FileNotFoundError: ./venv/bin/python` (tests assume a local venv), missing git working-tree
state, and multi-process / cross-process fixtures (RAG id stability). Several pass in isolation
(e.g. `test_agent_tools_truncate_nonstring.py` passes alone, fails in the full run) indicating
ordering/state leakage rather than a code or dependency defect.

**Not addressed here because:** this plan is dependency-locking only (behavior-preserving, no
application/test code changes). These belong to the coverage-gap / test-hardening work in later
phases (PROJECT.md: "fill coverage gaps before refactoring").

Failing tests (20):
- tests/test_agent_tools_truncate_nonstring.py (3)
- tests/test_archived_sessions_model_filter.py (3)
- tests/test_build_user_content_pdf_marker.py (1)
- tests/test_cookbook_helpers.py::test_pip_install_fallback_chain_propagates_failure_in_venv
- tests/test_email_polly_imap_leak.py::test_auto_summarize_pass_logs_out_imap_on_select_failure
- tests/test_hwfit_quant_formats.py::test_selected_gguf_quant_is_strict_not_lower_quant_fallback
- tests/test_null_owner_gates.py::test_gallery_owner_filter_blocks_anonymous
- tests/test_rag_vector_id_stability.py::test_rag_id_stability_across_processes
- tests/test_replace_messages_multimodal.py (2)
- tests/test_rewrite_persist_column.py::test_rewrite_query_selects_and_updates_latest_assistant_message
- tests/test_search_ranking_sports_substring.py (2)
- tests/test_search_service_nondict_rows.py::test_search_skips_non_dict_results
- tests/test_split_chunks_no_duplicate_tail.py::test_no_chunk_is_contained_in_another
- tests/test_webhook_ssrf_resilience.py::test_webhook_delivery_uses_naive_utc_timestamps
