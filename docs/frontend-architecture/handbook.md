# Odysseus frontend architecture handbook

Status: F1 merged in PR #6701. The complete Notes reference is implemented as an
unpublished review diff on `refactor/notes-reference-decomposition`. Read its
[case study](notes-case-study.md), [module inventory](notes-module-inventory.md)
and [validation receipts](notes-validation.md). Publication and the recipient's
post-integration reproduction remain pending.

Read the [ADR](ADR-001-source-and-deployment.md), [assessment](assessment.md),
[validation report](validation.md) and [Notes roadmap](notes-roadmap.md).
The frontend program ends with the Notes reference decomposition and handoff.
Calendar, Documents, Email, Chat, Skills, Settings and other later decompositions
belong to the designated frontend maintainer; do not start them automatically.

## Source, ownership and public APIs

`frontend/src/app/` composes public feature APIs and platform ports. Each
`features/<feature>/index.js` is an intentional API, with explicit named exports
and a compatibility default only when existing consumers require one. Internals
live under `domain/`, `application/`, `infrastructure/` or `ui/` as needed. Do not
create empty directories, boilerplate controllers or a framework by convention.
Feature names `app` and `shared` are reserved.

Keep pure business rules in domain; transitions and workflows in application;
HTTP/storage/browser services in infrastructure; DOM rendering and lifecycle in
UI. The index composes adapters, state and views. Application receives adapters
as explicit arguments or constructor ports, rather than importing concrete
infrastructure. Views call application operations; infrastructure does not own
business decisions. A public API returns data/operations, not mutable stores or
private DOM nodes. Importing an API should define capabilities; explicit lifecycle
methods activate them. Legacy side effects remain characterized until a reviewed
slice safely changes their placement.

Shared code has a named, proven cross-feature responsibility. `shared/domain/`
holds pure primitives, `shared/platform/` holds narrowly scoped platform adapters,
and `shared/ui/` holds common DOM capabilities. A named stateless root leaf such
as `shared/actionMenuOrder.js` is also allowed and receives domain restrictions.
Do not create a central `utils.js` collection or move feature business rules into
shared simply to satisfy an import rule.

## Mechanically checked dependency contract

| Importer | Permitted maintained-source targets |
| --- | --- |
| App composition | Other app code, shared code, feature `index.js` APIs |
| Feature index | Its own responsibility modules and shared code; other feature public indexes |
| Feature domain | Its own domain and shared domain/stateless leaves |
| Feature application | Its own application/domain, shared domain, other feature public indexes |
| Feature infrastructure | Its own infrastructure/domain, shared domain/platform |
| Feature UI | Its own UI/application/domain, shared domain/platform/UI |
| Shared domain leaf | Shared domain leaves only |
| Shared platform | Shared platform/domain only |
| Shared UI | Shared UI/platform/domain only |

No feature imports app composition. No shared code imports a feature. No external
feature imports private responsibility modules. UI and infrastructure communicate
with another feature through a supplied application operation/port. Maintained
source dependency cycles, including literal lazy imports, fail the architecture
gate. If features need bidirectional coordination, app composition injects a
callback or adapter instead of creating mutual imports. Prefer this even when a
one-way public index import would pass the gate.

Cross-feature events must document event name, payload, acknowledgement, owner,
subscription disposal and delivery timing. Keep them local and specific; F1 adds
no global event bus. Preserve `odysseus:notes-context-selected` and its synchronous
`accepted` acknowledgement in future Notes work. The server remains authoritative
for owner filtering and untrusted selected IDs. Frontend APIs cannot grant access.

The checker parses executable ESM, forbids computed imports in new source,
requires exact precache entries, rejects unresolved/remapped relative imports,
and permits only canonical static JS URLs with one unversioned identity for
maintained modules. Domain code cannot mention known browser/HTTP/timer globals
or an ambient clock; use `now`/clock inputs. This is a conservative vocabulary
check, including shadowed names, not a semantic purity proof. Randomness, hidden
side effects, authorization and lifecycle behavior require behavioral tests.

Legacy imports from new source default to forbidden. If an incremental bridge is
necessary, declare `legacyImports` on that source's manifest entry as a list of
`{ "specifier": "/static/js/example.js?v=existing", "reason": "contract, owner and removal slice" }`.
Only feature composition, infrastructure or UI boundaries may use it. Exact
specifier/reason and target existence are required; unused exceptions fail.
Keep the existing query identity. New domain/application modules receive no
legacy exception. F1 has zero exceptions. Do not use exceptions to invent new
shared platform dependencies on feature internals.

## State and lifecycle

Each feature has one authoritative mutable application-state owner. A transition
operation owns writes; UI receives a view/snapshot or subscription. Preserve
existing objects and initialization timing when extracting a stateful module.
Do not instantiate a new store in both the legacy facade and the feature API.
Compatibility facades only forward the same live implementation.

The Notes reference uses internal read-only live views to preserve captured note
identity across existing asynchronous completions. Its state owner supplies all
mutations; the public index exposes operations without the store. Notes deliberately
retains last-completion fetch ordering. Do not infer request-generation guards from
its decomposition. Its page-owned reminders survive panel closure, and panel/editor
listeners and observers have separate disposal scopes.

Document lifecycle scopes: page, authenticated user, session, mounted panel and
editor/draft. Name owners of subscriptions, DOM listeners, timers, observers,
pending requests and caches. Mounting must be idempotent; disposal releases the
resources it owns. Background reminders may outlive an open panel. Closing the
panel must not silently stop those reminders during extraction.

Keep request-generation and selected-session checks around asynchronous
completions. Serialize writes where the current contract requires it. Dispose
subscriptions explicitly and prevent late updates to a replaced view. Feature
state changes on user/session switch must follow current cleanup policy. Browser
storage is for preferences/drafts/cache state, never a substitute for backend
authorization, credential storage or owner filtering. Define every storage key's
owner, retention/reset behavior and compatibility format before moving it.

## Creating a module or introducing a feature

1. Identify responsibility, owner, public consumers, side effects and behavioral
   evidence before editing. Start with a pure leaf when introducing a feature.
2. Place only the required source file(s), for example
   `features/example/domain/calculate.js`. Add an index only when there is a
   real supported feature API to compose.
3. Register each source in `frontend/modules.json`. A typical new mapping is
   `features/example/domain/calculate.js` to
   `js/features/example/domain/calculate.js`. Preserve the source/deployment
   relative graph. The proof's legacy URL mapping is an explicit special case.
4. For an extracted leaf, the legacy feature coordinator may import its output
   as a temporary composition bridge. New maintained features use public indexes;
   the legacy tree is not globally forced into the new boundary rules in F1.
5. Add the exact output URL to the appropriate explicit SW array, retain lazy
   evaluation timing and bump the cache name when the graph changes. Declare
   `text eol=lf` for both mapped paths in repository-owned `.gitattributes`
   files; source-tree or scoped output patterns are allowed. The publisher checks
   effective Git rules, including nested overrides, and rejects a missing LF rule
   even if output bytes already match. Do not broaden unrelated legacy checkout
   rules or automatically rewrite all existing query identities.
6. Run the publisher, inspect the source/output diff, then run architecture,
   identity, graph, offline and focused behavior checks. Include both sides of
   the mapping in review. Retiring output requires a separate reviewed deletion;
   the publisher does not clean the static tree.

```sh
python scripts/build_frontend.py
python scripts/build_frontend.py --check
npm ci --ignore-scripts --no-audit --no-fund
node scripts/check_frontend.mjs
node scripts/check_frontend.mjs --inventory
node --test tests/frontendArchitecture.test.mjs
./venv/bin/python -m pytest -q -rs tests/test_frontend_build.py tests/test_frontend_foundation_offline_js.py tests/test_frontend_module_graph.py tests/test_frontend_module_version_parity.py tests/test_panel_loader_js.py
./venv/bin/python scripts/verify_frontend_runtime.py
```

Use your actual repository virtualenv path; the F1 environment is recorded in
validation. Node 20+ follows current CI. Existing Playwright/browser prerequisites
are documented in `tests/README.md` and `tests/KNOWN_FAILURES.md`. Missing Node can
skip Python browser wrappers, which is a coverage gap, not a pass. The standalone
Node gates require locked development dependencies and fail when missing.

The publisher validates all inputs before writing and only overwrites owned
outputs whose bytes differ. It is idempotent and working-directory independent.
Git must be available for publication and `--check`; neither runs at application
startup. Attribute evaluation uses a private temporary Git repository and ignores
global/local overrides, so checkout consistency is reproducible in CI. It adds
no runtime configuration or backend imports. CI checks LF attributes, parity and source
syntax in existing jobs; the Node job also runs architecture self-tests. Existing
runtime identity tests remain authoritative across legacy imports, HTML and SW.
The isolated runtime probe is included in the new pytest publication tests.

## Review, release and rollback

Review the source/output mapping and exact exported API, then the owner/dependency
contract and behavior tests. Check manifests, URL identities, MIME/CSP, load timing,
storage compatibility and disposal. Reject unexplained feature behavior changes.
Do not repair unrelated defects under an extraction title.

Release from a validated revision with committed output. Native startup and
packagers consume static files without a compiler. Run parity before producing a
Docker or desktop artifact. Test the actual packaged application on its platform;
Linux contract probes cannot certify macOS/Windows executables. Deploy matching
shell, facades, graph and manifests as a unit. Keep an authorized path to the last
complete artifact. Rollback restores the whole matching set, not just an entry
file or cache version. Do not delete a facade until its public consumers have
migrated and compatibility tests make that decision reviewable.

## Proposal and PR templates

For a decomposition proposal, record: baseline; responsibility map; current public
API and consumers; state owner; I/O and lifecycle boundaries; planned slice;
allowed dependencies/temporary exceptions; preserved behavior; deterministic
characterization tests; deployment/offline changes; overlapping PRs; rollback;
explicit non-goals and remaining uncertainties.

For each migration PR, lead with the responsibility moved and resulting boundary.
Record source/output paths, API compatibility, state/lifecycle ownership,
characterization results, exact validation commands, platform gaps, exception
removal plan, upstream dependencies and rollback. Attach a concrete before/after
example when needed to establish behavioral equivalence. Preserve the repository
PR template and never claim unexecuted checks.

F1 is an entirely separate foundation-only PR. Deliver the complete Notes
decomposition, regression tests, compatibility validation and final reference
case study through one draft PR targeting official `dev`, using one Notes branch
and worktree after F1 integration. N0 through N6 and the N5 substages are internal
engineering checkpoints, not independent PRs or merges. Each checkpoint records
focused tests, review evidence, its validated revision or reproducible snapshot,
and rollback to the prior matching source/output/facade/precache set. Keep the
draft open while those checkpoints progress; mark it ready only after the entire
decomposition passes the [roadmap's acceptance gates](notes-roadmap.md).

Structure the Notes PR description around the final integrated design and link
each checkpoint's evidence. The receiving maintainer may use smaller independent
PRs for future frontend features at their discretion; the one-PR Notes decision
does not impose that delivery size on later work.

## Program backlog and final handoff

Priority order: review/integrate F1; resolve root offline certification with the
owner of PR #6227; complete the Notes checkpoints and final reference case study
within one draft PR following the [roadmap](notes-roadmap.md); integrate the
complete Notes PR after its acceptance gates; finalize the recipient handoff.
Only then does the receiving maintainer prioritize Calendar, Documents, Email,
Chat, Skills and Settings against current behavior and risk. Existing partial
packages and high fan-in helpers need assessment, not automatic replacement.

Final H acceptance requires a designated maintainer, the integrated Notes PR
revision and its internal checkpoint receipts, the actual public API/state/lifecycle
map and final reference case study included in that PR, validated deployment/offline
commands, known exceptions/debt,
outstanding PR dependencies, rollback evidence and a reproducible independent
setup by the recipient. F1 provides the handbook structure and contracts; it does
not claim Notes or the completed handoff have occurred. The initiating architect
then returns to separately authorized non-frontend architecture work.
