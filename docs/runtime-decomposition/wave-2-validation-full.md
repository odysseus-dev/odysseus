# Wave 2 final validation

Worktree: `odysseus-runtime-request-authority`; branch:
`feature/runtime-request-authority`.
Starting SHA: `d6c3c98c75e03f70c05ebe4058c6fa12e0395f62`.
The final SHA is the local commit containing this report, returned in the final
implementation report. No rebase, merge, push or PR was performed.

All results below apply to the final production code. The last production
changes addressed internal HTTP request/approval origin and transient untrusted
Chat context. Focused, Wave 1.1, broad runtime and full pytest were rerun after
those changes. Subsequent edits only recorded results and removed temporary
validation logs.

| Gate | Final result |
| --- | --- |
| New Wave 2 authority tests | 58 passed, 1 warning; 1.23s |
| Relevant contract/policy/approval/capability/Ajax/task/background tests | 1500 passed, 28 skipped, 1 warning; 30.55s |
| Wave 1.1 validation script | 2292 passed, 1 warning; 65.75s |
| Broad affected runtime suite | 3079 passed, 28 skipped, 1 warning; 92.83s |
| Full pytest | 11644 passed, 54 skipped, 2 xfailed, 182 warnings, 6 subtests passed; 444.40s |
| Python compileall | Passed |
| JS/MJS syntax | Passed for all 361 tracked files |
| Git whitespace gate | Passed |
| Conflict-marker scan | Passed |

The existing release smoke hook skipped because `APP_PORT` was unset; no live
instance was driven. Full pytest includes its existing skips and expected
failures. Warnings are retained in the local raw log. Missing development test
dependencies and Playwright Chromium were installed locally, without changing
project dependency declarations. No global dotenv-disable override was used.

## Commands

```sh
ODYSSEUS_TEST_STATIC_PORT=0 .venv/bin/python -m pytest -q tests/test_request_authority.py

ODYSSEUS_TEST_STATIC_PORT=0 .venv/bin/python -m pytest -q tests/test_request_authority.py tests/test_turn_contract*.py tests/test_tool_policy.py tests/test_tool_approval*.py tests/test_execution_capabilities.py tests/test_ajax*.py tests/test_task_*.py tests/test_bg_*.py

ODYSSEUS_TEST_PYTHON="$PWD/.venv/bin/python" bash scripts/validate_runtime_wave1.sh

ODYSSEUS_TEST_STATIC_PORT=0 .venv/bin/python -m pytest -q tests/test_request_authority.py tests/test_agent_*.py tests/test_turn_contract*.py tests/test_tool_policy.py tests/test_tool_approval*.py tests/test_task_*.py tests/test_bg_*.py tests/test_*completion*.py tests/test_foreground_model_routing.py tests/test_client_tool_routing.py tests/test_workspace_confine.py tests/test_product_turn_contract_route.py tests/test_execution_bridge.py tests/test_execution_capabilities.py tests/test_ajax*.py tests/test_external_context_tool_gate.py tests/test_tool_path_confinement.py tests/test_edit_file.py tests/test_runtime_evidence_contract.py tests/test_review_regressions.py tests/test_image_creation_routing.py tests/test_ask_user_tool.py tests/test_update_plan_tool.py tests/test_weather_search_recovery.py tests/test_clean_agent_preview.py tests/test_skill_audit*.py tests/test_preview_execution_evidence.py

ODYSSEUS_TEST_STATIC_PORT=0 .venv/bin/python -m pytest -q

.venv/bin/python -m compileall -q -x '(^|/)(\.venv|\.git|node_modules|data|logs|uploads)/' .
git ls-files -z '*.js' '*.mjs' | xargs -0 -n 1 node --check
git diff --check
# Staged whitespace check used --cached --check with all 37 changed paths explicit.
git grep --cached -l -E '^(<<<<<<< |=======$|>>>>>>> )' -- '*.py' '*.js' '*.mjs' '*.html' '*.css' '*.json' '*.md' '*.sh'
```

Conflict-marker grep returns exit 1 with no matches on success.
The context firewall rejected the unbounded staged whitespace command before
execution; the exact-path check passed. No admitted source inspection was
blocked by staging.
Local raw validation outputs are archived under the ignored
`.venv/wave2-validation/` directory; they are not committed.

## Regression scope and limits

The 58 authority tests cover schema/handler/model-selection non-authority,
narrow media/tasks/browser behavior, exact reads, deterministic grants, hard
denials, malformed/missing state, retry and nested inheritance, teacher
forwarding, exact approval scope/replay/digest, scheduled input sealing and
workspace restoration, detached followups and actual-launch-only sealing,
internal HTTP origin, untrusted Chat persistence, and denied-call journal
completion evidence. Existing fixture assertions remain intact; standalone
dispatch fixtures now provide explicit server authority.

Remaining limits: class admission uses deterministic request classifiers and
can reject unsupported phrasing; legacy task/job snapshots fail closed until
trusted resealing; snapshots assume trusted server database/job storage;
sidecar retention hardening is deferred. Existing approvals can admit one exact
root operation without granting subsequent or nested operations.

No Wave 3, 3-S, 4, 5 or 6 work was started. No containment, effect/egress,
provenance, authority-mode UI, journal or completion-foundation redesign is
included. File ownership and the discovery/implementation contract are recorded
in [wave-2-request-authority.md](wave-2-request-authority.md).
