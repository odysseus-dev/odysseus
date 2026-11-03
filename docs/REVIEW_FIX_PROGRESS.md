# Review and security fixes

Scope: fix the six findings from the initial review, broaden review of the
current worktree, then review security boundaries and fix confirmed findings.
Do not treat the initial six as the entire goal. Existing unrelated edits are
preserved. No deployment or commits performed.

## Implemented

- Task endpoint credential matching now requires identical normalized API
  origin and path; rejects embedded URLs, userinfo, query/fragment, changed
  ports, schemes and sibling paths. Regression tests use dummy credentials.
- Email deletion distinguishes failed IMAP probes/searches from confirmed
  absence; failures propagate to the error handler without deleting the index.
  Corrected swapped diagnostic fields for fixture and Message-ID presence.
- Original document conversion runs in a worker thread; its temporary files
  are cleaned up inside that worker, including after request cancellation.
  Each LibreOffice process gets an isolated profile. Timeout becomes HTTP 504.
- Research lexical rejection is limited to the intended small-model path;
  browser recovery precedes final rejection. Non-ASCII/cross-language inputs
  and empty term sets defer to model extraction instead of being hard-rejected.

## Verified so far

- Endpoint credential and email UID regression tests: 13 passed.
- Existing research full-loop navigation, extraction controls, browser
  fallback and synthesis resilience tests: 13 passed (the two original
  failures now pass).
- New research language and small-model browser recovery tests: 6 passed.
- `git diff --check`: passed.

## Second pass implementation

- Added email invitation revision tracking keyed by owner, normalized sender
  and ICS UID. Whole-event updates reuse the local event; cancellations retain
  tombstones (including cancellation-before-invite), remove reminders, and
  prevent older revisions from resurrecting the event. Attendee replies do not
  create events. Parser/write failures stay retryable. Single-part calendar
  messages are recognized. Four integration tests with isolated SQLite passed.
- Found and fixed three more substring credential matches in skills audits and
  scheduler paths. Centralized exact endpoint matching in endpoint_resolver;
  task override/audit lookups now also apply owner_filter.
- Found and fixed calendar location HTML injection: text surrounding a URL was
  inserted as raw HTML. Both links and non-link segments are now escaped.

## Third pass implementation and checks

- Detached recurrence reschedules/cancellations use independent revision state
  and exclude the original occurrence from the parent series. Out-of-order
  imports preserve exclusions; series cancellation also cancels detached rows.
  Eight calendar invitation tests pass. THISANDFUTURE is explicitly rejected
  and left retryable, rather than silently applying a single-instance change.
- Imported event IDs are derived from scoped invitation identities, bypassing
  title/time dedup so unrelated senders cannot become linked to the same event.
- Failed calendar attachment imports never fall through to AI interpretation.
- Original PDF form conversion now recognizes source markers with fields=.
  Three route-level conversion tests pass: event-loop concurrency, timeout and
  cleanup, and direct conversion of a form PDF's source.
- Fixed local-model foreground waiter double-decrement; behavioral test passes.
- Broader combined run: 276 passed, two broken test fixtures. Corrected a moved
  assertion using an undefined variable and refreshed the AST test's full-schema
  environment/expectations; rerun pending.
- Calendar HTML injection regression has passed in combined testing.

## Review checklist (completed in final pass)

- Credential regressions exercise real owner-filtered SQLite queries in task
  and skill resolvers. Both scheduler lookup sites use the same tested exact
  matcher and owner_filter; reviewed their call sites.
- Invitation updates are serialized across processes, with cancellation and
  cross-process lock tests. Startup create_all creates the new invitation
  table; no running-service migration/restart was performed.
- Broader review covered changed document/UI workflows, model/agent routing,
  research, task scheduling, and email/calendar ingestion.
- Security review covered auth/ownership, external-content rendering,
  credential routing, execution restrictions, and upload/file conversion.
- Final broad and security-focused runs are recorded below. See the final
  report for coverage boundaries and deployment limitations.

## Fourth pass

- Combined regressions now pass: 279 tests.
- Fixed a fixture-account policy exception that could restore explicitly
  disabled/owner-blocked personal tools. Capability restoration now excludes
  all denied names; AST-executed regression checks both denial sources.
- Fixed late DOCX preview responses reopening hidden previews/overwriting a
  different tab, and DOCX-to-rich conversion overwriting another tab or newer
  edits. Actual JavaScript handlers exercised with deferred responses in Node.
- New fixes plus personal routing/route policy suites: 70 passed.
- Ownership/auth/upload/audit suites: 79 passed, one stale mock signature;
  updated the mock to accept and verify the production override arguments.
- No service deployment/restart or real LibreOffice conversion performed.

## Final pass and completion evidence

- Execution-time disabled-tool and guide-only restrictions now win over an
  admitted turn contract, in both agent-loop checks and the dispatcher.
- Fixed email/document compound routing, research job-ID misrouting, and
  named-document opening losing UI navigation. Corrected the hardcoded
  veterinary fallback for arbitrary research queries.
- Invitation series imports use bounded, cross-process file-lock stripes;
  overlapping revisions, cancelled holders, and a separate-process probe pass.
- DOCX parsing/rendering are offloaded. Standalone imports now receive their
  owner before the first database commit, verified by a commit event hook.
- Plain document listings apply the SQL limit before loading document bodies.
- Source-email links render even with a single calendar; DOCX preview fails
  closed if its HTML sanitizer is unavailable.
- Updated stale tests only where verified current contracts changed: unknown
  intents may reach inference, DeepSeek reasoning is retained for protocol
  continuity, Qwen fallback uses native schemas, and email reads include the
  full-message reader.
- Final changed-test + review + ownership run: **2723 passed, 52 warnings**.
- New-worktree tests + authentication/upload/XSS/export batch: **302 passed,
  1 warning, 6 subtests passed**. These batches overlap; counts are not additive.
- `git diff --check` and `node --check` for calendar.js/document.js pass.
- No confirmed review finding remains unaddressed. This was a risk-focused
  code/security review, not a full production penetration test or live UI QA.
