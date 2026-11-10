# Code and security review — 2026-09-16

Reviewed the current uncommitted project changes, fixed the initial six
findings, then broadened the review to changed backend/UI flows and security
boundaries. Existing unrelated edits were preserved. Nothing was committed,
pushed, deployed, or restarted.

## Findings fixed

| Area | Finding and correction |
| --- | --- |
| Endpoint credentials | Substring URL matches could attach saved credentials to an unrelated endpoint. Task, scheduler, and skill-audit lookups now require an exact normalized origin/path; task/audit lookups also filter by owner. |
| Tool authorization | Fixture capability restoration and admitted turn contracts could override explicit denials. Disabled-tool, owner, and guide-only restrictions now remain effective. |
| Calendar rendering | Non-link text surrounding a location URL was inserted as raw HTML. Both text and links are escaped. |
| Email deletion | Failed IMAP lookups were indistinguishable from confirmed absence, allowing premature index cleanup. Lookup failures now propagate. |
| Email invitations | Cancellations and revisions could create duplicates or resurrect stale events. Added scoped revision/tombstone state, detached-occurrence handling, stable event IDs, and serialized imports across workers. |
| DOCX editor | Late preview/conversion responses could overwrite another tab or newer edits. Responses are checked against document/request identity before applying. |
| Document ownership | Standalone Office imports were initially committed without an owner. Owner is assigned before the first commit. |
| Document conversion | Synchronous parsing/conversion blocked async request handling. Work runs off-loop; LibreOffice gets isolated profiles, bounded timeouts, and worker-owned cleanup. |
| Research extraction | Lexical rejection bypassed browser recovery and rejected cross-language input. The filter is scoped to small-model mode, permits recovery, and defers cross-language relevance to extraction. |
| Research planning | Generic fallback queries incorrectly included veterinary terms. Replaced with topic-neutral variants. |
| Agent routing | Explicit document routing swallowed email/compound requests; research job IDs were mistaken for task operations; document opening lost UI navigation. Corrected these paths. |
| Model queue | A foreground waiter was decremented twice, understating queued interactive work. Corrected release accounting. |
| Document library | Plain listings loaded every document body before limiting. Limit now applies in SQL. |
| Calendar UI | Source-email links disappeared when only one calendar existed. Email provenance no longer depends on calendar count/name. |

## Verification

- **2,723 tests passed**: all modified Python test files, review regressions,
  and selected ownership/authorization suites.
- **302 tests passed, plus 6 subtests**: new worktree tests and additional
  auth, upload isolation/limits, XSS, and document export checks.
- Batches overlap; these are not distinct-test totals.
- Behavioral tests include real owner-filtered SQLite queries, actual JS
  handlers with deferred responses, concurrent invitation revisions,
  cross-process exclusion, and execution-time permission denial.
- `git diff --check` and JavaScript syntax checks pass.

## Coverage and limitations

This was a risk-focused review of the working diff and its affected workflows,
not a claim that the entire repository is vulnerability-free. Authentication,
owner boundaries, credentials, external HTML, tool execution, and file handling
received targeted security review and regressions.

No live email/model endpoints were used for verification. Browser handlers were
tested in Node, not visually checked on a phone. LibreOffice is unavailable in
this environment: process behavior, direct-source input, timeouts, and cleanup
were tested with a substitute process, not real document-layout fidelity.

Invitation `RANGE=THISANDFUTURE` is explicitly rejected and remains retryable;
it is not silently applied as a single-occurrence update. The cross-process
lock test ran on POSIX; the Windows locking branch was not exercised.

Deployment must run normal database initialization to create the new
`email_calendar_invitations` table. File locks use a bounded directory beneath
the application's data directory. No production database migration was run
during this review.

All confirmed findings from this review are addressed. See
[REVIEW_FIX_PROGRESS.md](REVIEW_FIX_PROGRESS.md) for the implementation record.
