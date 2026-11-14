# Native test merge gate

`Native test suites` runs on pull requests, dev/main pushes and manual dispatch.
`Authoritative native suites` fails unless both the native Node and complete
Chromium jobs succeed; it has no continue-on-error, retries or path bypass.
Repository administrators must require this check alongside the four pytest
checks in branch protection. The audited public dev branch had no protection or
ruleset, so merely adding workflow jobs cannot prevent a manual merge.

`tests/native-ci-manifest.json` exhaustively partitions all native Node files:
16 run in the native job,14 through existing pytest wrappers, one through the
existing frontend architecture CI step, and the formerly live email-reply file
through an explicitly identified Playwright driver. The manifest validator fails
for omitted/duplicate/unreachable entries. Node emits individual JUnit identities
and propagates its failure exit. No native file is newly run in each pytest shard.

The browser job uses the existing one-worker Chromium config and all 136 Photo
Editor cases (including cursor and brush regressions), plus one driver for the native
email-reply contract (137 cases total). Its ephemeral account,
SQLite DB and server are created by the config/setup; reuseExistingServer remains
false. NPX startup is offline/cache-only in a private cache; Notes/editor behavior
and Python MCP services remain real. No developer app or external account is used.

Default CI trace suppression remains intact. Only this job's explicit opt-in plus
a newly generated authenticated synthetic fixture marker permits failure traces.
The existing structured sanitizer runs before artifacts are uploaded; invalid
ownership, failed sanitization and missing tooling purge sensitive artifacts.
Unsupported or malformed sensitive records cause the affected archive to be removed
fail-closed. An independent fail-closed verification step (.github/scripts/verify_sanitized_artifacts.py)
runs after test suite execution or abnormal runner termination to prevent publishing
unsanitized diagnostics while preserving safe JUnit reporting. The verifier returns a nonzero
exit code upon any inspection or deletion failure, publishable XML is restricted to recognized
JUnit reports with sensitive tokens redacted, and workflow upload steps execute only when
independent verification succeeds (steps.<id>.outcome == 'success').
Never upload the fixture directory, database or browser storage state.

The stroke budget now excludes measured synthetic event-delivery pacing and also
bounds actual Chromium main-thread work at the original5s. All240 samples and
pixel/thumbnail/undo/redo assertions remain. Per-case timing attachments expose
both clocks rather than treating driver frame cadence as application work.

Three Bombadil properties remain opt-in: they require a Bombadil/browser scenario
and bounded fuzz campaign; no authoritative execution is claimed. The two model
smoke scripts require supplied model endpoints/models and remain explicit live
operations. No credentials or live-service authorization are implied by CI setup.
Firefox/WebKit alternatives remain opt-in and are not certified by Chromium CI.
