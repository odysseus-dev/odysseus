# Skills PR #6711 second-review corrections

The correction scope is route prompt compatibility and structural constructor
contracts. PR #6711 remains draft. Durable skill ownership stays with
`SkillsManager`. No publication policy, authority mechanism, provider runtime,
frontend behavior or protected manager/index implementation was changed.

The reviewed head was `a6a33674ed4facf11f53e7cdd41f882ad0fa8482`. The independent
original baseline is `86b886c611338282af6d4a717246ea4e256f58df`. Official `dev`
integration was tested against `c3819e3cbf56c4f899eacc66b21115a77d9c570d`.

## Compatibility corrections

Service-local imports captured prompt builders outside the route namespace.
Patching the public route symbols therefore stopped affecting execution after
extraction. `AuditDependencies` now binds the current test-task, test-message and
baseline-message builders. Manual execution, exact-approval continuation, initial
audits, baseline arms, self/teacher retries and scheduled batches use those
bindings. The route `_skill_baseline_task` wrapper again delegates to the current
route `_skill_test_task`. Both execution arms still use one shared task, matching
the original behavior. No service imports Skills routes.

The signature checker treated a factory's module/qualified name as its identity
and omitted Pydantic settings outside the generated Python signature. Closures
with different captured values and default-validation changes were false
negatives. New tests additionally demonstrated hidden validation-alias path and
model-configuration changes. The checker now records public field validation
settings, rebuilt annotations, defaults and inherited model configuration. See
the [Pydantic FieldInfo reference](https://docs.pydantic.dev/latest/api/fields/)
for the distinction between factories, validation aliases and `validate_default`.

Only known builtin factories are supported by identity. Custom factories,
including factories requiring validated field data, raise `TypeError` without
being invoked during comparison. Unsupported metadata/configuration values also
fail closed. No bytecode, arbitrary object representations or factory output is
serialized. The comparator does not promise equivalence of arbitrary Python
validators or factory implementations.

The expanded fixture was generated from the original baseline in a new detached
worktree, with only the revised test comparator copied in. Python 3.11 and 3.12
produced byte-identical files. Every previous parameter/return record remains
equal. All 28 interfaces and 22 endpoint declarations remain present. The
canonical OpenAPI fixture is unchanged, and its original digest is unchanged.
Independent native `inspect.signature` snapshots also match original versus
corrected implementations on each interpreter.

## Regression evidence and validation

The new tests were run before production/comparator edits: 12 failed and four
passed, demonstrating the six signature cases and six broken prompt cases. After
correction, all 16 added cases pass. The prompt probes capture actual
`stream_agent_loop` ingress rather than replacing the runners or coordination.
They cover patched and normal execution, guard escaping and taint metadata,
owner authority, exact-approval continuation, and worker retries after both
self and teacher rewrites. The same ten prompt cases pass on the original
implementation. The six signature cases reject custom factories without calling
them and detect default validation, alias paths and model validation settings.

Environments: Python 3.11.17 and 3.12.13, FastAPI 0.143.1, Pydantic 2.14.1,
pytest 9.1.1. Python test commands set `FASTEMBED_MODEL=review-disabled` and
`HF_HUB_OFFLINE=1` to exercise unavailable optional embeddings without model
downloads. They do not validate live providers or embedding quality.

| Executed scope | Result |
| --- | --- |
| 53-file Skills, approval, authority and lifecycle selection, Python 3.11 | 757 passed |
| Same selection, Python 3.12 | 757 passed |
| Both characterization/service files, included above | 70 passed |
| Original baseline, available focused selection plus revised characterization | 724 passed |
| Original baseline, revised characterization plus prompt probes, each interpreter | 47 passed |
| Current-dev combined tree, focused selection plus eight intervening dev test files, each interpreter | 900 passed |
| Repository-wide suite, Python 3.11 | 13,991 passed, 60 skipped, two inherited environment failures, six subtests passed |
| Frontend publisher and architecture checks | Passed, zero changed modules |
| Relevant Node tests | 13 passed |
| Compilation, both Python versions | Passed |
| Import graph and fresh service-first/route-first imports, both versions | Passed; acyclic; no reverse Skills route import |
| Original normalized moved-body comparison | 26 of 26 matched after explicit dependency substitutions |
| Pinned/checksum-verified Gitleaks 8.30.1, reachable HEAD and official dev histories | No leaks |
| Same Gitleaks, tracked working files | No leaks |
| Diff/whitespace and protected-source comparisons | Passed |

The two repository-wide failures are
`tests/test_containment_enforcement.py::test_namespace_handshake_closes_model_stdin`
and `test_execution_environment_cannot_replace_probed_bwrap[False]`. Isolated
reruns on both original and corrected implementations reproduce the same extra
`Failed to create stream fd: No such file or directory` stderr. The independent
review traced it to login-profile `im-config` calls to `systemd-cat` inside
Bubblewrap without the journal stream socket. These environment failures remain;
they were not hidden by excluding tests. A full Python 3.12 suite was not run.

The combined test tree used the official merge revision `5419ed37`, whose tree
exactly matches `git merge-tree --write-tree upstream/dev a6a33674`, with the
correction diff applied in a detached scratch worktree. Published history was
not rebased, amended, reset or rewritten.

Local commands, independent fixture captures, baseline reproductions and full
logs are retained under `/tmp/skills-6711-corrections/`. The independent review
and adversarial probe source remain under `/tmp/skills-6711-second/`. These
temporary evidence paths are not portable repository fixtures. CI should read
the committed golden fixtures, not regenerate them from the implementation.

## Separate inherited P2 hardening plan

All six defects demonstrably exist on the official original baseline. The six
safety assertions in `/tmp/skills-6711-second/test_adversarial.py` fail on both
the original and corrected trees with the same outcomes. Logs are
`inherited-original.log` and `inherited-head.log` in the correction evidence
directory. The original/source body comparison also matches these paths after
dependency substitutions. There is no demonstrated material worsening from the
extraction. That finding does not make their security impact acceptable.

Priorities below concern a separate hardening change. Items 1 and 2 are release
blockers requiring maintainer resolution before deploying automatic skill
rewrite/publication workflows. This recommendation is independent of whether
the architecture PR is integrated. No tool-authority bypass or cross-owner
credential disclosure was demonstrated, and no critical extraction regression
was found that requires expanding this compatibility correction.

1. **Model-controlled lifecycle/provenance fields.** Current source:
   [publication.py:328](../services/skills/publication.py#L328), especially line
   342; the bypassed retrieval condition is
   [skills.py:703](../services/memory/skills.py#L703). Original source:
   [routes/skills_routes.py:1039](https://github.com/odysseus-dev/odysseus/blob/86b886c611338282af6d4a717246ea4e256f58df/routes/skills_routes.py#L1039).
   `test_rewrite_cannot_promote_model_text_to_builtin` uses the real manager and
   saves model markdown with `source: builtin`, `status: published` and
   `confidence: 1`. Identity/owner remain pinned, but the lifecycle fields persist
   and the skill becomes retrieval-eligible without a passing audit. Corrective
   change: allowlist procedure and approved descriptive/retrieval fields during
   model rewrites; preserve existing owner, provenance, publication, confidence,
   audit and teacher lifecycle state. Only verified policy may update those
   fields. Metadata-only fixes must preserve the procedure or trigger a retest.
   Add real-manager tests for self, teacher and metadata-only rewrites.

2. **Preference loading fails open for automatic publication.** Current source:
   [publication.py:13](../services/skills/publication.py#L13) and
   [skills.py:59](../services/memory/skills.py#L59). Original source:
   [routes/skills_routes.py:710](https://github.com/odysseus-dev/odysseus/blob/86b886c611338282af6d4a717246ea4e256f58df/routes/skills_routes.py#L710).
   `test_preferences_read_failure_does_not_enable_auto_publication` makes the
   loader raise `PermissionError`; automatic publication is enabled rather than
   preserving the owner's possible opt-out. Corrective change: distinguish a
   successfully loaded empty preference store from an exception, log the failure
   and disable automatic publication on exceptions. Align the manager retrieval
   preference adapter in the separate fix. Test unreadable/malformed existing
   preference files and the ordinary empty-store default independently.

3. **Manual cancellation retains private credentials and stale running state.**
   Current source:
   [agent_runner.py:125](../services/skills/agent_runner.py#L125) and
   [job_coordinator.py:49](../services/skills/job_coordinator.py#L49). Original:
   [routes/skills_routes.py:635](https://github.com/odysseus-dev/odysseus/blob/86b886c611338282af6d4a717246ea4e256f58df/routes/skills_routes.py#L635).
   `test_manual_cancellation_cleans_sensitive_continuation` suspends and cancels
   the stream. The stream closes in its originating task, but job status remains
   running and `_run.headers.Authorization` remains resident. Polling still hides
   these fields; this is retention, not demonstrated disclosure. Corrective
   change: handle `CancelledError`, mark cancelled, clear private continuation
   data, retire only the attached exact approval and re-raise. Preserve state on
   a normal awaiting-approval return. Retain task handles for coordinated
   replacement/cancellation and test owner/approval isolation.

4. **Approval pauses during retries lead to demotion.** Current source:
   [audit_workflow.py:244](../services/skills/audit_workflow.py#L244), teacher
   retry at line 280 and demotion at line 303. Original:
   [routes/skills_routes.py:1448](https://github.com/odysseus-dev/odysseus/blob/86b886c611338282af6d4a717246ea4e256f58df/routes/skills_routes.py#L1448)
   and lines 1484/1507. `test_retry_approval_preserves_publication_and_confidence`
   begins published at .9, fails the first grade, then pauses the rewritten test
   for approval. The result is flagged/draft/.35. The lower-level approval is
   correctly denied; state handling afterwards is wrong. Corrective change:
   immediately recognize `approval_required` after every retry, stop escalation
   and demotion, and preserve the intended pre-pause lifecycle state. Coordinate
   with item 1 so model rewrites cannot replace that saved state. Test both
   self/teacher retries, repeated pauses and batch protection.

5. **Duplicate losers are binned after keeper publication fails.** Current:
   [publication.py:217](../services/skills/publication.py#L217), including loser
   writes at line 228. Original:
   [routes/skills_routes.py:914](https://github.com/odysseus-dev/odysseus/blob/86b886c611338282af6d4a717246ea4e256f58df/routes/skills_routes.py#L914).
   `test_duplicate_losers_retained_if_keeper_write_fails` raises disk-full during
   keeper publication and still observes a loser binned. Corrective change:
   require confirmed keeper persistence before binning losers when automatic
   publication is enabled. Treat both exceptions and False returns as failure,
   retain candidates and report recovery information. Preserve existing keeper
   selection and duplicate scoring rules; add failure/retry tests.

6. **Reported publication status can disagree with canonical storage.** Current:
   [publication.py:320](../services/skills/publication.py#L320). Original:
   [routes/skills_routes.py:1017](https://github.com/odysseus-dev/odysseus/blob/86b886c611338282af6d4a717246ea4e256f58df/routes/skills_routes.py#L1017).
   `test_publication_does_not_report_unpersisted_success` returns False from the
   manager update; the helper reports published while the stored skill remains
   draft. Exceptions are also swallowed by the inspected code. Corrective change:
   check persistence success, log failures and return reloaded canonical state or
   an explicit persistence error. Test False/exception outcomes and the job/UI
   projection. Coordinate this change with item 5 without changing publication
   thresholds or scoring.

Maestrum's read-only triage delegation produced no usable result within its
usefulness window and was cancelled. Cleanup completed and the session records
an interrupted result, not a successful review. The coordinator reclaimed the
remaining report work and verified the source references and reproduction logs
above. No worker claim was used as proof. No GitHub issue, new PR, maintainer
comment or PR-description edit was made.
