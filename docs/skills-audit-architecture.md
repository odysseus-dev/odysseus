# Skills domain service boundaries

## Current integration state

This document covers draft [PR #6711](https://github.com/odysseus-dev/odysseus/pull/6711),
tracking [issue #6710](https://github.com/odysseus-dev/odysseus/issues/6710), on
`refactor/skills-evaluation-boundary` in the `architecture/skills-evaluation`
worktree. The domain naming pass began on 2026-10-14 from clean PR head
`5bf51207dc6d183b4b7357f77098f60614e32185`, verified against GitHub.
Official public `dev` was `c3819e3cbf56c4f899eacc66b21115a77d9c570d`.
Its six commits since the extraction baseline do not change the Skills service
files. They are not incorporated by this naming pass.

The initial extraction was committed as `a547c4d8`; the narrowly scoped
Gitleaks correction followed in `5bf51207`. The original S0 through S4 work
started from `86b886c611338282af6d4a717246ea4e256f58df`, which contained
#6683, #6572, #6643 and #6693. Historical extraction results below refer to
that work, not to fresh validation of the domain naming pass.

## Skills domain responsibilities

`services/skills/` did not exist before this pass. Repository-wide inspection
found no conflicting module, dynamic import, deployment assumption or external
compatibility requirement for the old service package. There is no compatibility
package under the old name. Public compatibility stays at the existing route
boundary.

| Module | Responsibility |
| --- | --- |
| `services/skills/__init__.py` | Identify the domain package without a second service facade |
| `evaluation.py` | Verdict parsing, model grading, necessity, retrieval precision, efficiency fields and improvement generation; no job scheduling or persistence |
| `agent_runner.py` | Manual execution, audit arms, agent loop integration, transcript capture, authority propagation, exact approval continuation and same-task stream closure |
| `audit_workflow.py` | Individual audit sequencing, retry, teacher escalation, sequential batches, scheduled execution and existing audit registry ownership |
| `job_coordinator.py` | Job creation and selection, continuation after HTTP approval validation, status projections, polling and cancellation requests |
| `publication.py` | Confidence thresholds, publishing eligibility, duplicate/redundancy policies, batch finalization, status decisions and improved markdown applied through SkillsManager |
| `model_selection.py` | Owner-scoped evaluator/teacher resolution, utility/default fallback, endpoint selection and existing credential lookup |
| `prompts.py` | Shared task construction and explicitly untrusted skill context |
| `dependencies.py` | Frozen `AuditDependencies` with explicit callable bindings and transient registry references |
| `routes/skills_routes.py` | FastAPI, request/response contracts, authentication and ownership checks, HTTP errors, sealed approval validation/consumption and Python compatibility wrappers |
| `services/memory/skills.py` | Unchanged canonical persistent storage, identity, ownership, indexing, retrieval and lifecycle state |

Publication calculations remain together. The per-skill duplicate guard and
batch connected-group finalizer retain their distinct selection rules; storage
retrieval retains its established eligibility policy. Consolidating those rules
would change behavior and is outside this pass.

## Public HTTP contract

All paths below have prefix `/api/skills`. Successful status defaults to 200.
Request models and endpoint declaration order remain fixed. A frozen
OpenAPI and declaration-order fixture records
the baseline contract independently of the extracted implementation.

| Method and suffix | Successful response | Authorization and errors |
| --- | --- | --- |
| GET empty | `skills,count` | Owner-filtered load |
| GET `/index` | `index,count` | Owner-scoped eligible index |
| GET `/slash-catalog` | `skills,count` | Owner-scoped index; sorted slash entries |
| GET `/builtin` | `builtin,count` and optional `error` | Existing shared capability text |
| GET `/builtin/{name}` | `name,text,default,is_overridden` | 404 missing tool; 500 import failure |
| PUT `/builtin/{name}` | `ok,name,is_overridden` | Admin; 404 unknown, 400 missing text |
| DELETE `/builtin/{name}` | `ok,name,is_overridden` | Admin |
| POST `/import-from-url` | `ok,skill,files` | Admin and current owner; 400 import, 502 HTTP, 500 other failure |
| POST `/add` | `ok,deduped,skill` | Current owner passed to manager; unchanged old/new request fields |
| POST `/{skill_id}/invoke` | `ok,type,name,command,message` | Eligible owner index only; 404 unavailable/source missing |
| GET `/{skill_id}` | Full skill dictionary | Owner load; 404 missing |
| GET `/{skill_id}/markdown` | `name,markdown` | Owner verification; 404 missing/source missing |
| POST `/{skill_id}/test` | `ok,status,skill,model` | Owner verification, HTTP request authority, registered fallback credentials; 400 missing/invalid model |
| POST `/{skill_id}/test-approval` | `ok,status,decision` | User approval request, owner verification, pending ID/owner/empty session match; 400 invalid body/decision, 409 stale/mismatch/consumed |
| GET `/{skill_id}/test-status` | `status` or `status,task,model,log,verdict,approval` | Owner-keyed lookup; server continuation/credentials omitted |
| POST `/audit-all` | `ok,status,total` plus running `done,model` or empty `results,log` | Owner-keyed singleton; 400 model resolution |
| GET `/audit-all/status` | `status` or job progress/result/log/timestamps | Owner-keyed lookup; task and credentials omitted |
| POST `/audit-all/cancel` | `ok,status` | Owner-keyed lookup and cancellation |
| POST `/{skill_id}/markdown` | `ok,name` | Owner verification; 400 missing/parse, 404 missing, 500 update |
| PUT `/{skill_id}` | `ok` | Owner verification and scoped manager update; 404 missing |
| DELETE `/{skill_id}` | `ok` | Owner verification and scoped manager deletion; 404 missing |
| POST `/search` | `skills,query,count` | Owner-filtered input; 400 empty query |

Strict owner verification allows an ownerless builtin but denies an ownerless
ordinary skill for an authenticated user. The existing ownerless deployment
behavior remains. Global auth middleware is unchanged.

## Python compatibility and test seams

`app.py` imports `setup_skills_routes`. `src/builtin_actions.py` imports
`_run_skill_test_once`, `_skill_test_task`, `_resolve_audit_models`,
`_run_audit_all_job` and `_skill_audit_jobs`. Tests additionally import or patch
`_VERDICT_PROSE_RE`, `_skill_baseline_task`, `_run_skill_audit_arm`,
`_run_skill_test_job`, `_skill_test_jobs`, `_audit_one_skill`,
`_audit_auto_publish_policy`, `_audit_finalize_status`, `_audit_utility_blocker`,
`_finalize_audit_batch`, the evaluation helpers and `SkillAuditUnavailable`.
Retain all original top-level functions, classes, signatures and registries.
Bindings must pass patched collaborators explicitly; copying imports alone
would silently bypass these fixtures.

External dependency patches target `src.agent_loop.stream_agent_loop`,
`src.llm_core.llm_call_async/list_model_ids`, endpoint resolver functions,
`src.ai_interaction._resolve_model`, preferences and settings. Keep lazy
resolution and route-level effort injection so offline patches remain effective.

## Characterized lifecycle and security contracts

Manual tests replace owner/name jobs, deny replaced approvals, run detached
tasks, retain server-only continuation state on approval and poll condensed
logs. Manual results adjust confidence (pass .95, needs_work .6, fail .4)
without publishing. Audit arms suppress saved skill injection, use 4096 tokens,
and inherit active request authority or use empty owner authority. Manual
tests use the explicit originating authority or the sealed approval authority.
Both close async generators inside the originating task with `aclosing`.

Approval requests consume the exact ID destructively with
`allow_continuation=False`. Transcripts and skill markdown remain untrusted
context messages. Unattended audits deny encountered approvals and preserve
publication/confidence for approval-required results. No approval grants are
invented by a service boundary. Issue #6635 remains open: a manual test without
an appropriate workspace can still be unable to execute its proposed action.

Batches run sequentially per owner, record current/done/results, cap logs at
1,000, and finalize only on normal completion. Cancellation clears current/task
and skips finalization; infrastructure unavailability stops without a skill
verdict. Ordinary exceptions record an inconclusive result. Scheduled runs
select existing lifecycle candidates by oldest audit, cap the batch, skip
an active owner job and mark every model phase as background work.

Run review makes two attempts (32768 tokens, 180 seconds); necessity uses
8192/120, retrieval uses 4096/90, rewrites use 16384/180. Owner effort is
resolved for each model call. Preserve prompts, think stripping, trailing-comma
parsing, last verdict selection, bounded schema fields and unknown fallbacks.

Per-skill pass confidence is .95, self-edit pass .85, teacher rewrite pass .8;
continued failure becomes draft/.35. Inconclusive preserves publication.
Thresholds, automatic approval, redundancy and worse-baseline blockers remain.
Batch finalization touches only processed skills, protects approval-required
results, excludes builtins/binned records, and bins duplicate losers.
Published eligibility and remote-only exclusion remain in SkillsManager.

## Historical overlap inspection

Read-only GitHub inspection found all of #6568, #6265, #6191, #6275 and #6695
open and unmerged during the initial extraction. They concern model effort
capability evidence, frontmatter escaping, tool registry extraction, tool
availability across turns and OXS. Their changes were excluded from the
extraction. Recheck upstream state before merge.

## Final architecture and inventory

The transport module delegates evaluation, execution, job transitions and audit
publication. Its original request classes, endpoint functions and declaration
order stay in place. Compatibility functions have their original signatures;
stateless helpers are aliases. At each service entry, `_audit_dependencies()`
binds current route symbols into a frozen, explicit collaborator record.
This preserves existing monkeypatches without changing service module globals
or importing `routes.skills_routes` from a service.

The test registry is owned by `agent_runner.py`, and the audit registry by
`audit_workflow.py`. Routes expose aliases to those same dictionaries. Neither
registry is a second skill store: all durable updates use the supplied
SkillsManager. Polling deliberately projects only existing public fields.
HTTP handlers retain registered endpoint authorization and exact approval
validation; the service receives the consumed server-side grant.

```mermaid
flowchart TD
    HTTP[Authenticated HTTP requests] --> Routes[skills_routes transport and compatibility bindings]
    Builtins[Existing builtin_actions callers] --> Routes
    Routes --> Jobs[job_coordinator]
    Routes --> Models[model_selection]
    Routes --> Evaluation[evaluation]
    Routes --> Runner[agent_runner]
    Routes --> Workflow[audit_workflow]
    Jobs -->|injected runner| Runner
    Jobs -->|injected workflow| Workflow
    Workflow -->|injected callbacks| Runner
    Workflow -->|injected callbacks| Evaluation
    Workflow -->|injected callbacks| Publication[publication]
    Runner -->|injected evaluation| Evaluation
    Runner --> Agent[Existing agent runtime and authority enforcement]
    Runner --> Prompts[prompts and untrusted context]
    Jobs --> Prompts
    Workflow --> Prompts
    Routes --> Manager[Canonical SkillsManager]
    Runner --> Manager
    Workflow --> Manager
    Publication --> Manager
    Publication --> Preferences[Existing routes.prefs_routes adapter]
    Models --> Resolver[Existing endpoint resolver and owner-filtered credentials]
```

Arrows describe domain responsibilities, including injected calls. The only
concrete import between the runner and workflow is the workflow's import of
`SkillAuditUnavailable`. The frozen dependency record provides existing
callbacks without service imports of `routes.skills_routes`. Route wrappers
bind the current patchable functions on every entry, preserving builtin callers
and monkeypatch behavior. Logger names remain unchanged for observability.

`SkillsManager` is the sole persistent state owner. Runner and workflow calls
that record audit metadata still go through that manager; the service package
has no storage, identity or indexing implementation. Published-only and
remote-only eligibility remain in their existing manager/index boundaries.
`services/memory/skills.py` and `src/skill_index.py` are unchanged.

### Runtime execution flow

1. HTTP handlers authenticate, verify ownership and validate requests. Manual
   model choices retain registered-endpoint validation and owner-scoped credentials.
2. Job coordination records the existing transient state and launches the
   existing background coroutine. Polling projects only the established fields.
3. The runner passes originating or sealed approval authority into the existing
   agent runtime, captures tainted transcripts and closes streams with `aclosing`
   inside the originating task. Approval pauses return to HTTP validation.
4. The approval handler matches the sealed identity, owner and empty session,
   consumes it once with `allow_continuation=False`, then delegates continuation
   using the consumed grant. The coordinator rebuilds untrusted context.
5. Audit workflows sequence necessity/retrieval checks, execution, grading,
   self-edit retries and optional teacher rewrites. Scheduled runs retain their
   lifecycle candidate selection and background workload flags.
6. Publication decisions and markdown changes use the supplied canonical manager.
   Sequential batches finalize only after normal completion; cancellation and
   infrastructure unavailability retain their existing cleanup and verdict rules.

### Final directory layout

```text
services/
├── skills/
│   ├── __init__.py
│   ├── evaluation.py
│   ├── agent_runner.py
│   ├── audit_workflow.py
│   ├── job_coordinator.py
│   ├── publication.py
│   ├── model_selection.py
│   ├── prompts.py
│   └── dependencies.py
└── memory/
    ├── skills.py
    ├── skill_format.py
    ├── skill_lifecycle.py
    └── ...
routes/
└── skills_routes.py
```

### Exact file moves in the domain naming pass

| Old path | New path |
| --- | --- |
| `services/skill_audit/__init__.py` | `services/skills/__init__.py` |
| `services/skill_audit/evaluation.py` | `services/skills/evaluation.py` |
| `services/skill_audit/execution.py` | `services/skills/agent_runner.py` |
| `services/skill_audit/orchestration.py` | `services/skills/audit_workflow.py` |
| `services/skill_audit/jobs.py` | `services/skills/job_coordinator.py` |
| `services/skill_audit/policy.py` | `services/skills/publication.py` |
| `services/skill_audit/models.py` | `services/skills/model_selection.py` |
| `services/skill_audit/prompts.py` | `services/skills/prompts.py` |
| `services/skill_audit/dependencies.py` | `services/skills/dependencies.py` |

The other changed files in this pass are `routes/skills_routes.py`,
`tests/test_skills_audit_services.py` and this document. The source changes are
module names, internal import aliases and the package docstring. Function
bodies, constants, prompts, registry ownership and the frozen HTTP/Python
fixture remain intact. References to old paths in the table above are migration
history, not active imports.

The initial extraction reduced the route from 2,462 to 865 lines. Its integration
inventory was the route, nine service modules, the frozen fixture, two test files,
this document and `.gitleaks.toml`. This naming pass moves those nine modules
and updates the three files listed above. Ignored development environment,
bytecode, test logs and caches are not integration deliverables.

## Compatibility and security verification

The baseline fixture freezes all 22 endpoint declarations, all 28 inspectable
original Python signatures, and the whole generated OpenAPI digest:
`7abc5b334a6c3c52dd3eba15d8bc516da728a67f14fb7f56d0bb5992d89a16ca`.
The exception class remains available at its original import path as an alias.
The fixture was captured before structural extraction. No request models,
endpoint docstrings, operation IDs or response declarations changed.

During the initial extraction, a read-only AST comparison against
`git show 86b886c6:routes/skills_routes.py`
verified 20 moved function bodies, the extracted verdict parser, and four
model system prompts. Comparison normalized only injected collaborator names
and excluded docstrings; it did not normalize branching, thresholds, request
arguments, stream cleanup or exception handling. The evaluation parser's
imports and verdict tuple moved with it. Full runtime checks supplement this
structural evidence.

A second AST comparison verified all 19 unchanged route handlers/helpers and
the approval handler's validation and sealed-consumption prefix. The only
removed import in that prefix was `asyncio`, now used by the job service.

| Required contract | Evidence |
| --- | --- |
| Route and Python compatibility | Frozen declaration order, OpenAPI digest, signatures and existing route tests |
| Owner isolation | Existing manager/update/delete, request-authority, registered-endpoint and teacher-owner tests; live second-owner 404 |
| Utility effort and fallback | Existing utility reasoning tests; advisory owner tests; first owner-scoped audit fallback and served-model normalization |
| Verdict parsing and failure schemas | Characterization cases for think blocks, trailing commas, last verdict, coercion, prose, retry and transient exceptions |
| Scheduling and progress | Existing scheduled/background phase tests; batch done/results/current/timestamps and ordinary exception case |
| Finalization and publication | Threshold/auto-approval matrix, inconclusive preservation, duplicate winners/losers, unprocessed and approval-required protection |
| Cancellation and cleanup | Cancellation in the originating context, skipped batch finalization, cleared task/current and stream exception cleanup |
| Approved and rejected execution | Existing exact ungranted-process and authority tests; route grant/deny integration tests |
| Exact approval reuse prevention | Destructive store consumption, single-action grant flags and second submission returning 409 |
| Published and remote-only eligibility | Existing index/retrieval and lifecycle suites; SkillsManager implementation unchanged |
| Skills UI/API integration | Existing Skills UI/static tests, Node approval metrics tests, live authenticated API and asset checks |

The security review found no widened authority source. The manual runner uses
the original request or sealed pending authority and otherwise empty owner
authority. Audit arms retain active task-local authority, saved-skill
suppression and same-task `aclosing`. Both approval ID and normalized owner
must match, and the session must be empty. Internal tool approval requests are
still rejected before consumption. Continuations keep skill/transcript bytes
tainted. Unattended approvals are denied rather than retained as reusable
grants. Services do not reconstruct sealed operations or choose a workspace.

## Domain naming pass validation, 2026-10-14

The worktree-local CPython 3.11.17 environment and Node 22.23.1 were reused.
The focused selection is exactly the 53-file algorithm documented below.
Pre-change hashes and outputs are retained in ignored `.cache/skills-domain-*`
artifacts, separately from the historical extraction logs.

| Check actually executed | Result |
| --- | --- |
| Pre-change focused selection at `5bf51207` | 729 passed, 2 warnings, 26.16 seconds |
| Characterization and service suites | 42 passed, 1 warning, .73 seconds |
| Post-change full focused Skills/authority/approval selection | 729 passed, 2 warnings, 26.17 seconds |
| `.venv/bin/python -m compileall -q app.py core routes src services scripts tests` | Passed |
| `.venv/bin/python scripts/build_frontend.py --check` | Passed, zero changed deployment modules |
| `node scripts/check_frontend.mjs` | Passed |
| `node --test tests/skillsApproval.test.mjs tests/frontendArchitecture.test.mjs` | 13 passed |
| `node --check` over the same documented JavaScript inventory | 235 files passed |
| Fresh interpreter import for each of the nine new modules and builtin-actions import | Passed |
| Import graph inspection | Acyclic service imports; no import of Skills transport |
| Frozen API fixture comparison | All 22 declarations in order, all 28 signatures and exact OpenAPI digest match |
| Isolated uvicorn HTTP smoke using `.cache/skills-runtime-smoke.py` | 17 checks passed, including authenticated owner isolation |
| Full Python suite with the documented environment and `-q -rs --durations=25` | 13,963 passed, 2 failed, 60 skipped, 6 warnings, 6 subtests passed, 570.22 seconds |
| `git diff --check` and staged whitespace validation | Passed |

All eight service implementation files are byte-identical after normalizing the
workflow's single renamed relative import. The package docstring identifies the
Skills domain. The route is byte-identical after normalizing module imports and
private aliases. Service tests have identical ASTs after normalizing only module
imports and collaborator names, including unchanged assertion values and fixture
strings. No expectation or frozen compatibility baseline was updated.

Hash checks confirm that `services/memory/skills.py`, `src/skill_index.py`,
`src/builtin_actions.py`, `tests/fixtures/skills_api_baseline.json` and
`.gitleaks.toml` match pre-change head exactly. Runtime identity checks confirm
that route aliases still expose the runner's test registry, the workflow's audit
registry and the same `SkillAuditUnavailable` class. `AuditDependencies` remains
frozen. Repository-wide source and configuration search found no old-package
imports, dynamic targets or deployment references; the only old paths are the
nine deliberate entries in the migration table above.

The full-suite failures are exactly:

```text
tests/test_containment_enforcement.py::test_namespace_handshake_closes_model_stdin
tests/test_containment_enforcement.py::test_execution_environment_cannot_replace_probed_bwrap[False]
```

The previously intermittent tmux cleanup test passed in this run. The 60 skips
retain their existing opt-in, optional dependency and platform reasons. The
full suite is not green on this host; the failures reproduce on clean baselines
and do not establish a regression in the domain naming pass. Official CI is
required to validate the supported runner. Full output is retained in
`.cache/skills-domain-full-pytest.log`.

The exact full-suite command was:

```sh
env -u APP_PORT PATH="$PWD/.venv/bin:$PATH" \
  PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/ms-playwright" \
  .venv/bin/python -m pytest -q -rs --durations=25
```

The two documented containment cases were also rerun individually as a pair
in the current worktree and fresh `git archive` snapshots of pre-change head
`5bf51207` and official extraction baseline `86b886c6`. All three runs had
exactly the same two failures: expected `closed`/`contained` output followed by
three host journal `Failed to create stream fd: No such file or directory`
stderr lines. Current: 2 failed in .67 seconds; pre-change: 2 failed in .84
seconds; official extraction baseline: 2 failed in .81 seconds. No runtime code
or test assertion was changed. Temporary baseline snapshots were removed.
Logs are `.cache/skills-domain-{current,prechange,official-extraction-baseline}-containment.log`.

## Historical initial extraction validation

All Python runs used the worktree-local CPython 3.11 environment and the
declared `requirements-dev.txt`. The shell's original `python` belonged to an
unrelated project and lacked pytest; no changes were made to that environment.
The first fixture-generation attempt tried to inspect the exception class,
which has no Python signature. After limiting the signature inventory to
inspectable symbols, the complete baseline passed. No assertion was weakened.

The checkpoint command groups below are literal pytest arguments:

```text
S0:
tests/test_skills_audit_characterization.py tests/test_skill_audit_utility.py
tests/test_skill_audit_unavailable.py tests/test_skill_test_stream_closed.py
tests/test_skills_routes_owner_update.py tests/test_skills_routes_nondict.py
tests/test_skills_delete_owner.py tests/test_skill_save_no_rename.py
tests/test_skills_manager_owner_isolation.py tests/test_skill_lifecycle.py
tests/test_skill_index_local_model_gating.py tests/test_utility_reasoning_effort.py
tests/test_teacher_audit_owner_scope.py tests/test_review_endpoint_credentials.py
tests/test_request_authority.py tests/test_endpoint_registered_authority.py
tests/test_exact_approval_ungranted_process.py tests/test_tool_approvals.py
tests/test_tool_approval_single_action_scope.py tests/test_tool_approval_task_scope.py

S1:
tests/test_skills_audit_characterization.py tests/test_skill_audit_utility.py
tests/test_utility_reasoning_effort.py tests/test_redos_verdict_continuation.py
tests/test_redos_residual_python.py

S2:
tests/test_skills_audit_characterization.py tests/test_skill_test_stream_closed.py
tests/test_skills_routes_nondict.py tests/test_skills_routes_owner_update.py
tests/test_skill_audit_unavailable.py tests/test_skill_audit_utility.py
tests/test_teacher_audit_owner_scope.py tests/test_review_endpoint_credentials.py
tests/test_request_authority.py tests/test_endpoint_registered_authority.py
tests/test_tool_approval_single_action_scope.py

S3:
tests/test_skills_audit_characterization.py tests/test_skill_audit_utility.py
tests/test_skills_manager_owner_isolation.py tests/test_skill_lifecycle.py
tests/test_skill_save_no_rename.py tests/test_skill_index_local_model_gating.py

S4:
tests/test_skills_audit_characterization.py tests/test_skills_routes_owner_update.py
tests/test_skills_routes_nondict.py tests/test_skill_audit_utility.py
tests/test_skill_audit_unavailable.py tests/test_skill_test_stream_closed.py
tests/test_request_authority.py tests/test_endpoint_registered_authority.py
tests/test_tool_approval_single_action_scope.py
```

| Run | Command | Result |
| --- | --- | --- |
| S0 | `.venv/bin/python -m pytest -q` with S0 arguments | 447 passed, 2 warnings, 18.63 seconds |
| S1 after parser extraction | Same command with S1 arguments | 133 passed, 1 warning, 1.47 seconds |
| S2 | Same command with S2 arguments | 374 passed, 2 warnings, 17.31 seconds |
| S3 | Same command with S3 arguments | 48 passed, 1 warning, .94 seconds |
| S4 | Same command with S4 arguments | 356 passed, 2 warnings, 16.21 seconds |
| Added service/HTTP contracts | `.venv/bin/python -m pytest -q tests/test_skills_audit_services.py tests/test_skills_audit_characterization.py` | 39 passed, 1 warning, .70 seconds |
| Added duplicate finalization cases | `.venv/bin/python -m pytest -q tests/test_skills_audit_services.py` | 22 passed, 1 warning, .65 seconds |
| Initial full relevant selection | `.venv/bin/python -m pytest -q -rs --durations=15` with the 53-file selection below | 726 passed, 2 warnings, 21.65 seconds |
| Final full relevant selection, including fallback and duplicate cases | Same command with the 53-file selection below | 729 passed, 2 warnings, 22.88 seconds |
| Repository-wide CI-equivalent Python scope | `env -u APP_PORT PATH="$PWD/.venv/bin:$PATH" PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/ms-playwright" .venv/bin/python -m pytest -q -rs --durations=25` | 13959 passed, 3 failed, 60 skipped, 6 warnings, 6 subtests passed, 605.00 seconds |

The reproducible relevant-file selection used for the final run is:

```python
from pathlib import Path
import subprocess
import sys

files = set()
for path in Path("tests").rglob("test_*.py"):
    text = path.read_text()
    if ("skill" in path.name or "approval" in path.name
        or "utility_reasoning" in path.name or "skills_routes" in text
        or "review_endpoint_credentials" in path.name):
        files.add(str(path))
files.update([
    "tests/test_request_authority.py",
    "tests/test_endpoint_registered_authority.py",
    "tests/test_redos_verdict_continuation.py",
    "tests/test_redos_residual_python.py",
])
subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs",
                "--durations=15", *sorted(files)], check=True)
```

Other executed validation:

- `.venv/bin/python -m compileall -q app.py core routes src services scripts tests`: passed.
- `.venv/bin/python scripts/build_frontend.py --check`: passed, zero changed modules.
- `node scripts/check_frontend.mjs`: passed.
- `node --test tests/skillsApproval.test.mjs tests/frontendArchitecture.test.mjs`: 13 passed.
- `node --check` for `static/app.js`, `scripts/check_frontend.mjs`, all `static/js/**/*.js` and `frontend/src/**/*.js`: 235 files passed.
- `git diff --check`: passed.
- `bwrap --die-with-parent --unshare-pid --ro-bind / / --proc /proc --dev /dev /bin/true`: passed.
- `.venv/bin/python .cache/skills-runtime-smoke.py`: 17 live HTTP checks passed against an isolated uvicorn process.

Python was 3.11.17 and Node was 22.23.1. CI's Python version matches; its Node
version is 20, so the local Node checks are equivalent commands with a newer
runtime. Existing SQLAlchemy and Pydantic deprecation warnings remain.

The repository-wide run completed with failures in these unchanged tests:

```text
tests/test_agent_tmux_retirement.py::test_legacy_cleanup_against_a_private_real_tmux_server
tests/test_containment_enforcement.py::test_namespace_handshake_closes_model_stdin
tests/test_containment_enforcement.py::test_execution_environment_cannot_replace_probed_bwrap[False]
```

The tmux test reported `{seen: 1, torn_down: 0, unverifiable: 1, failed: 0}`.
Both containment tests completed their expected command but included three
extra stderr lines: `Failed to create stream fd: No such file or directory`.
Their exact-output assertions failed. No containment implementation or
assertion was modified to suppress this output.

The exact targeted rerun command was:

```sh
env -u APP_PORT PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q -rs \
  tests/test_agent_tmux_retirement.py::test_legacy_cleanup_against_a_private_real_tmux_server \
  tests/test_containment_enforcement.py::test_namespace_handshake_closes_model_stdin \
  'tests/test_containment_enforcement.py::test_execution_environment_cannot_replace_probed_bwrap[False]'
```

Current-worktree rerun: 2 failed, 1 passed, 1 warning, 2.69 seconds.
A fresh `git archive HEAD` snapshot, unpacked into an owned temporary directory
under this worktree's `.cache/`, ran the same three nodes with the same Python
environment: 2 failed, 1 passed, 1 warning, 2.82 seconds. Both containment
failures reproduced against the unchanged required baseline with the same
stderr output. The tmux case passed in both isolated reruns; its full-suite
failure remains intermittent or dependent on test order/environment and is
not fully diagnosed. The snapshot was removed after collecting evidence.
The full-suite check is therefore not green. Official CI should verify these
host-dependent process cases before integration.

Sixty existing skips cover unconfigured live application/Ajax smoke tests,
an allowlisted browser release image, absent optional tools/dependencies and
platform-specific cases. The final relevant run includes the three new
duplicate/fallback cases added after repository-wide test collection.
Full output is retained locally in `.cache/skills-full-pytest.log`,
`.cache/skills-failure-recheck.log` and
`.cache/skills-baseline-failure-recheck.log`.

The application smoke script started real uvicorn on an owned ephemeral
loopback socket with authentication enabled. Its temporary data directory,
database, caches and sockets were inside this worktree. Chroma pointed at
unavailable port 0; pollers, scheduler and embedding prewarm were disabled
through existing deployment settings. Seventeen HTTP checks passed: health,
setup/login, Skills list/index/slash catalog, draft creation, markdown, manual
test and audit missing-model 400 responses, test polling, cancellation, memory
page, Skills JavaScript, second account/login, and cross-owner markdown 404.
The server completed shutdown and its private runtime directory was removed.
No live model execution or production skill state was used.

## Future architecture opportunities and remaining limitations

The existing publication preferences adapter imports `routes.prefs_routes`.
That layer inversion remains intentionally intact. Future boundary hardening
could move the preferences adapter below transport while preserving owner
resolution and fallback semantics.

Long-term consolidation of Skills storage under the domain package would require
an explicit lifecycle migration proposal. This PR leaves `SkillsManager`, skill
format/lifecycle modules and indexing in their canonical locations. A future
job-lifetime proposal could address transient state across restarts; this pass
preserves the current dictionaries, task lifetime and scheduling behavior.
The frozen dependency record remains the compatibility mechanism, without a
new dependency injection framework, abstract hierarchy or second service facade.

Issue #6635 remains unresolved: manual tests without an appropriate workspace
can still fail to execute. Advisory model outputs retain their existing
permissive coercions and unknown fallbacks. Historical isolated HTTP smoke
checks did not exercise a configured live model or a real external tool action.
Offline stream/approval tests and existing sealed-process tests cover those
boundaries deterministically. The earlier independent audit was CONDITIONAL
PASS; it does not constitute a new independent review of this naming pass.

Review and successful CI remain necessary before merge. The PR remains a draft;
this task does not authorize merging or marking it ready. No Notes files,
agent runtime, authorization implementation, provider configuration, persistent
schema or frontend source are changed.

## Gitleaks baseline checksum review

The initial extraction included 14 files. The PR also
updated `.gitleaks.toml` to handle two reviewed
`generic-api-key` false positives.

Both findings refer to the same public OpenAPI SHA-256 compatibility
digest, appearing in the characterization fixture and this report.

The exception requires an exact digest match, the specific rule,
and one of the two documented file paths. It does not suppress
unrelated secret findings or disable scanning.

The exception in `.gitleaks.toml` is byte-identical to pre-change PR head
`5bf51207`. The digest and frozen fixture are unchanged by the naming pass.
