# F1 implementation and review report

Date: 2026-10-13. Recommendation: ready for controlled incremental adoption after
maintainer review, with the platform/offline limits below. The foundation proof
is implemented and tested; Notes decomposition and the completed H handoff are
planned only. No commit, push, merge, PR or issue operation was performed.

## Notes delivery documentation correction

The maintainer's final delivery decision is one comprehensive Notes draft PR
targeting official `dev`, after the separate foundation-only F1 PR is integrated.
N0 through N6 are internal engineering checkpoints in one Notes worktree and
branch. The complete decomposition, regression tests, compatibility validation
and final reference case study belong to that single PR; it stays a draft until
the complete acceptance gates pass. Each stage retains its own test results,
review receipts and reproducible rollback/checkpoint strategy, without requiring
an independent GitHub PR. The receiving maintainer may choose smaller independent
PRs for future frontend features.

This correction updates only `notes-roadmap.md`, `handbook.md` and this report.
Implementation and test files retain their pre-correction bytes. All five
foundation documents passed link, fence, whitespace, no-em-dash and delivery
wording checks. `git diff --check` and the preserved-runtime diff check passed.
A before/after snapshot confirmed exactly these three changed documents and
15 unchanged existing F1 files. The implementation test results below remain
the previous executed evidence; implementation tests were not rerun in this
documentation-only pass.

## Independent review correction

The maintainer reports that the independent F1 review found no critical
architectural defects. This pass preserves the reviewed native ESM architecture
and scope. Its correction is to turn the documented LF checkout convention into
a manifest-driven publisher/CI requirement. The full independent review text
was not supplied; this report records the supplied finding and executed evidence.

The correction changes only `scripts/build_frontend.py`,
`tests/test_frontend_build.py`, the ADR, the handbook and this report. The
`.gitattributes` rules, architecture checker, CI workflow, manifest, dependencies
and stateless source remain as reviewed. The full F1 inventory below still has
18 files, including `.gitattributes`.

The publisher now evaluates both paths of every manifest entry through Git's
actual attribute engine before parity evaluation or any deployment write. It
requires `text: set` and `eol: lf` from applicable repository-owned attribute files,
including nested overrides. A private temporary repository and isolated Git
configuration prevent global attributes, local `.git/info/attributes`, templates
or alternate Git directory settings from masking an omitted declaration. Paths
are sent through NUL-delimited stdin, preserving spaces and using no shell/path
quoting assumptions. The working repository's attributes, index and output are
not changed by this check. Git is a publication/development prerequisite; the
application does not invoke it to build or check frontend assets at startup.

The production integration diff is the new `check_lf_attributes(root, paths)`
helper, four standard-library imports, this call, and updated CLI help:

```diff
@@ synchronize
     if unlisted:
         raise ValueError(f"source modules missing from manifest: {sorted(map(str, unlisted))}")
+    check_lf_attributes(root, sources | outputs)
     stale = []
```

The tests add fixture LF declarations and 14 cases. Ten parameterized cases
reject missing LF, CRLF, auto-text and binary-text rules for source/output paths
in both publish and check modes, even when bytes already match. The other four
cover a future manifest pair failing before any output is published and passing
after its LF declaration; nested rules and a spaced path; global/local settings
failing to mask an omitted repository rule; and clear failure without development
Git. Existing publication, ownership, confinement, parity and runtime tests remain.

## Exact attributes diff and checkout evidence

`git diff -- .gitattributes` is unchanged from the reviewed F1 diff:

```diff
diff --git a/.gitattributes b/.gitattributes
index 8681aee1..dd49c4a0 100644
--- a/.gitattributes
+++ b/.gitattributes
@@ -10,6 +10,10 @@
 entrypoint.sh text eol=lf
 docker/entrypoint.sh text eol=lf

+# Maintained frontend modules and this lane's byte-identical deployment output.
+frontend/src/** text eol=lf
+static/js/actionMenuOrder.js text eol=lf
+
 # Windows-native scripts stay CRLF.
 *.ps1 text eol=crlf
 *.cmd text eol=crlf
```

Exact command and results:

```text
$ git check-attr text eol -- frontend/src/shared/actionMenuOrder.js static/js/actionMenuOrder.js
frontend/src/shared/actionMenuOrder.js: text: set
frontend/src/shared/actionMenuOrder.js: eol: lf
static/js/actionMenuOrder.js: text: set
static/js/actionMenuOrder.js: eol: lf
```

The sampled legacy contracts were also inspected:

| Path | text | eol |
| --- | --- | --- |
| `static/js/notes.js` | auto | unspecified |
| `static/index.html` | auto | unspecified |
| `launch-windows.ps1` | set | crlf |
| `start-macos.sh` | set | lf |

Beyond samples, all 2,479 unrelated tracked paths were compared against HEAD for
both attributes and matched. The current Git 2.53.0 supports the diagnostic
`--source=HEAD` option; the publisher uses only the longstanding plain
`check-attr --stdin` interface, so it does not depend on that newer option.
The read-only whole-tree comparison command was:

```sh
python3 - <<'PY'
import subprocess
paths = subprocess.check_output(['git', 'ls-files', '-z'])
def attributes(source):
    result = subprocess.run(
        ['git', 'check-attr', *source, '-z', '--stdin', 'text', 'eol'],
        input=paths, capture_output=True, check=True,
    )
    fields = result.stdout.decode().split('\0')[:-1]
    return {(fields[i], fields[i + 1]): fields[i + 2]
            for i in range(0, len(fields), 3)}
before, after = attributes(['--source=HEAD']), attributes([])
owned = lambda p: p.startswith('frontend/src/') or p == 'static/js/actionMenuOrder.js'
changes = [(p, a, before[(p, a)], value) for (p, a), value in after.items()
           if not owned(p) and before[(p, a)] != value]
assert not changes, changes
print(len({p for p, a in after if not owned(p)}), 'unrelated tracked paths unchanged')
PY
```

## Ownership enforcement after correction

| Contract | Enforcement and CI receipt |
| --- | --- |
| Canonical, contained source/output paths and unique ownership | Publisher validates the whole manifest before writing; `python-syntax` runs its `--check` |
| Every maintained JS source is registered | Publisher rejects unlisted source; duplicate sources/outputs also fail |
| Reproducible LF checkout of both mapped paths | Publisher queries isolated repository-owned attributes, even for already matching bytes; the same CI `--check` now enforces it |
| Committed output equals maintained source | Publisher checks byte parity without rebuilding in CI; normal publication writes only stale owned outputs |
| Feature API/layer direction, no new cycles, stable relative resolution and maintained identities | Existing architecture checker and Node self-tests in `node-syntax`; no checker changes needed |
| Explicit exact-URL offline coverage | Architecture checker parses the existing SW arrays; real offline browser proof remains green |
| Legacy runtime graph/URL identity and behavior | Existing focused pytest guards and the full CI pytest jobs remain in place |

These gates enforce the documented maintained-JS contract together. They do not
retroactively impose source ownership on all legacy static files, clean retired
outputs or prove semantic state/lifecycle correctness. Those existing limits and
the independent packaging/offline limitations below remain unchanged.

## Final correction validation

The full focused pytest command printed below was rerun with the same interpreter
and all listed files: **193 passed, 1 skipped, 1 existing SQLAlchemy warning in
12.33 s**. It includes all 27 publisher tests and the isolated native startup,
static traversal and no-Node distribution probe. The new 14 tests account for
the increase from the initial F1 run's 179 passes. No test failed in this pass.

| Correction check | Actual result |
| --- | --- |
| Combined focused pytest command below | 193 passed, 1 unavailable-image skip |
| `node --test tests/frontendArchitecture.test.mjs` | 10 passed |
| Notes oracle/autosave/selected-context command below | 11 passed |
| `python3 scripts/build_frontend.py` | Passed, zero output changes, attributes enforced |
| `python3 scripts/build_frontend.py --check` | Passed, attributes and byte parity enforced |
| `node scripts/check_frontend.mjs` | Passed, one maintained module |
| Helper SHA-256 and `git check-attr` | Identical source/output hash; both text=set, eol=lf |
| All unrelated tracked attributes versus HEAD | 2,479 paths unchanged |
| `git diff --check` and preserved-runtime command below | Passed, no runtime/backend/packaging diff |
| `python -m compileall -q scripts/build_frontend.py tests/test_frontend_build.py` | Passed using the recorded virtualenv interpreter |
| Isolated Docker runtime command below | Passed, networking disabled; no source tree, node_modules or Node on PATH |
| Complete inventory, whitespace and documentation links | All 18 changed files matched the inventory; checks passed |

The one skip remains the existing test requiring `odysseus-odysseus:latest`.
No new skip or waiver was introduced. Windows/macOS executable builds, a fresh
full Dockerfile build, the full pytest suite, Firefox/WebKit and CI's exact
Python 3.11/Node 20 versions remain unexecuted locally. Native Git semantics,
nested overrides, paths with spaces and ignored `core.autocrlf`/global attribute
settings are covered by executed Linux tests; actual Windows execution remains a
platform-owner check. The existing `/static/` worker scope still prevents a claim
of full root offline reload readiness. No service-worker scope change is included.

The earlier local ChromaDB probe incident remains recorded under failures below.
All runtime checks in this correction pass used the isolated distribution probe
with reserved non-listening Chroma port and disabled warmups; Docker additionally
had networking disabled. This pass performed no new unisolated ChromaDB probe and
no attempt to restore or alter the existing local service's tool index.

## Reviewer-ready PR summary

Suggested title: `Establish frontend source ownership and native ESM deployment contracts`.

Maintain a stateless helper under `frontend/src/` while publishing identical bytes
at its existing static URL. Add manifest-owned publication, repository-owned LF
attribute and byte-parity checks, parsed dependency/API/identity/offline gates,
and a self-contained architecture handbook with the Notes decomposition roadmap.
Startup and existing feature behavior retain their current runtime contracts.

Validation: 193 focused Python tests passed with one existing Docker-image skip;
10 architecture and 11 Notes compatibility tests passed; source/output parity,
LF gates, preserved-runtime diff and isolated native/Docker runtime proofs passed.
Desktop executable builds and full root offline reload remain explicit limits.
All 18 files are inventoried below. The working diff is ready for review and is
uncommitted; commit, push and PR creation require separate authorization.

## Verified repository state

Repository: `odysseus-dev/odysseus`. Branch:
`refactor/frontend-architecture-foundation`. Worktree:
`/home/alteixeira20/Projects/Odysseus/worktrees/architecture/frontend-foundation`.
Initial worktree was clean. HEAD and local official `upstream/dev` are
`8feb50e89468235cd3e0986da80edbd3acaf2c40`. The live official GitHub `dev` SHA
matched at both initial and final verification. This is PR #6495's merge commit.
The `origin` remote is the maintainer fork, not the integration target.

Final changes are uncommitted F1 files only. Runtime JavaScript, Notes, HTML,
SW, backend, Dockerfile and desktop startup/packaging implementations have no
diff. The stateless source and existing deployed helper are both 1,688 bytes,
SHA-256 `e17baec5d6ac381ce6234ab6f028a8208487280dd07d6950a3469f8488809684`.
The final publisher run reported zero changed output modules.

## Decisions and scope

The [ADR](ADR-001-source-and-deployment.md) compares two executed approaches:
controlled native ESM copying and esbuild 0.25.12 ESM compilation/bundling.
Native byte copying wins on existing URL/module/cache compatibility and absence
of runtime tooling. Commit explicit output beside maintained source, publish
only manifest-owned modules and enforce parity in CI. Python startup never builds.

The [handbook](handbook.md) defines feature public indexes, responsibility layers,
state/lifecycle ownership, cross-feature ports/events, shared-code limits, legacy
coexistence, module creation, validation, rollback and handoff contracts.
The [assessment](assessment.md) records graph/bootstrap/state/storage/security,
packaging assumptions and current open-PR overlaps. The
[Notes roadmap](notes-roadmap.md) defines N0 through N6 as internal checkpoints
for one comprehensive Notes draft PR, with the final reference case study in
that PR and H recipient handoff after integration. None is implemented in F1.
There is one maintained stateless leaf and zero legacy-import
exceptions, with no empty feature scaffolding.

Acorn 8.19.0 is the sole new, exact-pinned development dependency, used to parse
architecture edges accurately. It is not loaded or distributed as runtime JS.
esbuild was a pinned one-off spike and is not a project dependency. Existing
frameworks, application entry points and backend contracts are unchanged.

## Exact changed files

| File | Change |
| --- | --- |
| `.gitattributes` | LF checkout for maintained source and the registered deployed proof |
| `.github/workflows/ci.yml` | Parity check; locked parser install; source syntax and architecture self-tests |
| `package.json` | Exact Acorn development dependency |
| `package-lock.json` | Acorn lock/integrity entry |
| `frontend/modules.json` | Explicit source/deployment ownership mapping |
| `frontend/src/package.json` | Native ESM interpretation for source tests, no dependencies |
| `frontend/src/shared/actionMenuOrder.js` | Authoritative stateless source, copied verbatim from the existing helper |
| `scripts/build_frontend.py` | Validated source publisher with manifest-driven, repository-owned LF enforcement and non-mutating parity check |
| `scripts/check_frontend.mjs` | Parsed import/layer/API/cycle/identity/precache guards and graph inventory |
| `scripts/verify_frontend_runtime.py` | Isolated current-code distribution/runtime compatibility probe |
| `tests/frontendArchitecture.test.mjs` | Behavioral API equivalence and positive/negative architecture gates |
| `tests/test_frontend_build.py` | Publication/ownership/drift/runtime coverage plus positive and negative Git attribute enforcement tests |
| `tests/test_frontend_foundation_offline_js.py` | Real SW precache, offline first evaluation, canonical namespace identity and lazy registry proof |
| `docs/frontend-architecture/ADR-001-source-and-deployment.md` | Decision, alternatives and distribution/rollback contracts |
| `docs/frontend-architecture/assessment.md` | Evidence-backed architecture and PR-overlap inventory |
| `docs/frontend-architecture/handbook.md` | Permanent foundation and maintainer continuation methodology |
| `docs/frontend-architecture/notes-roadmap.md` | Reference decomposition and final handoff plan |
| `docs/frontend-architecture/validation.md` | This implementation/review/validation report |

## Initial F1 validation before the review correction

Environment: Linux; native Python 3.14.4; pytest 9.1.1; FastAPI 0.143.0;
Starlette 1.8.0; Node 22.23.1; Playwright 1.62.1 with installed Chromium.
CI remains Python 3.11 and Node 20; that exact interpreter pair was not executed
locally. Use the repository virtualenv, not the system Python missing dependencies.
The interpreter used here was
`/home/alteixeira20/.venvs/odysseus-pr-6495/bin/python`.

The historical counts below are coordinator-executed checks in this worktree, excluding
delegated claims and earlier repeated subsets. Delegation was discontinued on
user instruction; no delegated implementation was integrated.

| Check | Actual result |
| --- | --- |
| Read-only baseline: graph, identity, helper, loader, MIME, nonce, runtime paths | 54 passed, one existing SQLAlchemy deprecation warning |
| Final combined focused pytest command below | 179 passed, 1 skipped, one existing SQLAlchemy warning, 12.62 s |
| Final `pytest -q -rs tests/test_frontend_build.py` review rerun | 13 passed, including native startup and encoded static traversal rejection |
| `node --test tests/frontendArchitecture.test.mjs` | 10 passed, no skips or failures; rerun after final gate review |
| Notes oracle/autosave/selected-context Node command below | 11 passed, no skips or failures |
| `python3 scripts/build_frontend.py` and `--check` | Zero output changes; byte parity passed |
| `node scripts/check_frontend.mjs` | Passed, one maintained module |
| `node scripts/check_frontend.mjs --inventory` | 230 files, 707 static edges, 108 lazy literal edges, no missing targets, three existing static cyclic components |
| `npm ci --ignore-scripts --no-audit --no-fund` | Locked development dependency installation succeeded |
| Native `scripts/verify_frontend_runtime.py` | Real FastAPI lifespan and shell/static assertions passed without maintained source, node_modules or Node on PATH |
| Docker runtime probe below | Same current-code assertions passed with networking disabled and source mount read-only |
| esbuild spike | Two identical 1,554-byte bundle outputs; executed exports/icon/ordering equivalence passed |
| JS syntax checks and Python compileall below | Passed |
| `git diff --check`, preserved-scope diff check, hash/LF checks | Passed; no runtime/backend/packaging source diff |
| Foundation documentation links, trailing whitespace and em-dash checks | All five files passed |

The skipped test is
`tests/test_docker_devops_hardening.py:187`, which requires the unavailable
`odysseus-odysseus:latest` image. It was not relabeled or bypassed. F1's separate
Docker runtime probe used the available `odysseus-maintainer-preview-odysseus:latest`,
image ID `sha256:cc2d47e2327d573af01c6b027f23d2ab0f2ee9b85d658e9eb8065bd02b9c3515`,
with Python 3.14.7, FastAPI 0.141.1 and Starlette 1.6.0. This proves compatibility
of current candidate code/assets with that image's runtime dependencies, not a
fresh build of the full repository Dockerfile or a deployment.

Reproduce the final focused command with the actual tested interpreter:

```sh
/home/alteixeira20/.venvs/odysseus-pr-6495/bin/python -m pytest -q -rs \
  tests/test_frontend_build.py tests/test_frontend_foundation_offline_js.py \
  tests/test_frontend_module_graph.py tests/test_frontend_module_version_parity.py \
  tests/test_action_menu_order.py tests/test_panel_loader_js.py \
  tests/test_app_static_mime.py tests/test_serve_html_with_nonce.py \
  tests/test_runtime_paths.py tests/test_ci_authoritative_validation.py \
  tests/test_selected_notes_context.py tests/test_email_library_module_graph_js.py \
  tests/test_settings_module_import_paths.py tests/test_app_config_shared_fetch_js.py \
  tests/test_markdown_lazy_lib_loading_js.py tests/test_startup_shell_js.py \
  tests/test_static_stylesheet_manifest.py tests/test_notes_dom_xss_helpers.py \
  tests/test_notes_reminder_temporal_redos.py tests/test_notes_sidebar_new_note.py \
  tests/test_notes_mobile_checklist_toggle_static.py \
  tests/test_notes_document_mutual_minimize.py tests/test_docker_devops_hardening.py

node --test tests/frontendArchitecture.test.mjs
ENDPOINT_URL=http://127.0.0.1:1 node --test \
  tests/noteTestOracle.test.mjs tests/notesDraftAutosave.test.mjs \
  tests/selectedNotesContext.test.mjs

python3 scripts/build_frontend.py --check
node scripts/check_frontend.mjs
/home/alteixeira20/.venvs/odysseus-pr-6495/bin/python scripts/verify_frontend_runtime.py

docker run --rm --network none \
  --mount type=bind,src=/home/alteixeira20/Projects/Odysseus/worktrees/architecture/frontend-foundation,dst=/candidate,readonly \
  --workdir /candidate --entrypoint python \
  odysseus-maintainer-preview-odysseus:latest scripts/verify_frontend_runtime.py
```

`ENDPOINT_URL` satisfies the existing oracle's import-time configuration requirement.
Its unit-test imports exercise pure exported functions; its network-driving main
is guarded by the executable-module check and does not run. The closed loopback
address is not a live model endpoint. No oracle implementation was changed.

Syntax commands executed:

```sh
bash -c 'shopt -s globstar nullglob; for f in static/app.js static/js/**/*.js frontend/src/**/*.js scripts/check_frontend.mjs tests/frontendArchitecture.test.mjs; do node --check "$f" || exit; done'
/home/alteixeira20/.venvs/odysseus-pr-6495/bin/python -m compileall -q app.py core routes src services scripts tests
git diff --check
git diff --exit-code -- static app.py Dockerfile .dockerignore Odysseus.spec build-macos-app.sh build-windows-portable.ps1 launcher.py core routes src services
git check-attr text eol -- frontend/src/shared/actionMenuOrder.js static/js/actionMenuOrder.js
sha256sum frontend/src/shared/actionMenuOrder.js static/js/actionMenuOrder.js
```

Spike compiler invocation, executed twice with equality and Node import assertions:

```sh
npx --yes esbuild@0.25.12 frontend/src/shared/actionMenuOrder.js \
  --bundle --format=esm --target=es2022 --platform=browser --log-level=error
```

The clean publication proof uses temporary roots in `test_frontend_build.py`,
including an initially absent output tree. Real-checkout publication is idempotent.
No emitted JS bytes, URLs, boot scripts or cache manifests changed in this lane.

## Failures encountered and their resolution

- An early architecture self-test expected an identity diagnostic but got an
  unresolved source path for a query-bearing relative import. The checker now
  rejects alternate identity before resolving that source path. All final tests
  pass; no test requirement was weakened.
- The initial Notes oracle command failed during import because `ENDPOINT_URL`
  was missing. Inspection established that the unit-test path never runs its
  endpoint-driving main. The explicit closed-address configuration above passed
  all 11 tests. This prerequisite is recorded for N0 rather than hidden.
- The first isolated distribution fixture omitted the existing `companion/`
  package, so both probes failed with `ModuleNotFoundError`. The fixture now
  includes that required runtime directory; neither application nor packaging
  implementation was modified to make the tests pass.
- The first exploratory native startup check connected to the existing local
  ChromaDB and refreshed the built-in tool index. That was unintended. Subsequent
  probes use a private distribution/data directory, a reserved non-listening
  Chroma port, disabled existing warmup options and no Node on PATH. Docker also
  disables networking. No attempt was made to overwrite or restore that service's
  data; the earlier unisolated probe is not the reproducible validation recipe.

## Remaining risks and limits

Root offline reload is not certified: existing SW scope is `/static/`, and open
[#6227](https://github.com/odysseus-dev/odysseus/pull/6227) proposes the independent
scope/header correction. F1 preserves and tests actual offline module behavior
at the current scope. No first-use offline guarantee is claimed for optional
CDN libraries, Mermaid or server APIs.

Windows/macOS packaged executables, a fresh full Dockerfile build, the full pytest
suite, Firefox/WebKit offline behavior and CI's exact Python 3.11/Node 20 versions
were not executed here. Existing packaging paths are inspected and unchanged;
platform owners should run release validation before broader certification.
The unavailable-image test remains a coverage gap. Legacy source still has
cross-feature coupling, global side effects and static cycles. New boundaries
are enforced on maintained source, not retrospectively on the entire legacy tree.

The publisher intentionally does not clean retired outputs or provide atomic
multi-file deployment. Review full source/output/facade/manifest changes and
ship a coherent artifact. Domain syntax checks cannot prove all semantic purity
or disposal; behavioral characterization remains required. Reconcile the package
lock with #6627 and refresh all PR overlaps before integration.

## Reviewer checklist

- Verify the base contains #6495 and the official integration target is dev.
- Confirm the deployed helper hash and runtime-scope diff are unchanged.
- Review the layer matrix, public API rules, single-state ownership and zero
  current legacy exceptions; verify negative tests actually reject violations.
- Review the manifest mapping, byte-parity/LF policy, exact offline precache and
  source/output relative-resolution guard; verify missing or overridden LF
  declarations fail publication and CI even when output bytes already match.
- Reproduce the focused tests and inspect the skipped-image prerequisite.
- Confirm no frontend compiler/runtime dependency is required at startup and
  inspect the isolated Python/Docker probe's limits.
- Review overlap with #6470/#6695/#6227/#6627 and the Notes backend follow-ups.
- Treat the ADR as awaiting adoption review, and the Notes/H documents as plans.
- Authorize any commit/push/PR action separately. Do not begin Notes on this branch.

## Required next reviews and continuation

1. Review this F1 working diff. On explicit authorization, commit and submit only
   the foundation-only F1 PR to official dev using the normal maintainer workflow.
2. Coordinate the existing #6227 offline fix and platform release validation as
   separate work. Do not bundle that behavioral change into F1.
3. After F1 integration and Notes implementation authorization, create one Notes
   worktree and branch from the latest official dev, preserving any subsequently
   merged Notes changes. Use one comprehensive draft PR targeting official dev.
4. Complete N0 through N6 from the [Notes roadmap](notes-roadmap.md) on that same
   branch, recording tests, review and rollback evidence at each checkpoint. Include
   the final reference case study in the same PR and keep it a draft until the full
   decomposition and regression/compatibility acceptance gates pass. Recurrence
   extraction follows characterization as an internal stage, not another PR.
5. Complete H only after Notes integration and recipient reproduction. Transfer
   future frontend work to the designated maintainer and stop this program.

Another maintainer can start at the handbook, run the documented source/parity,
architecture, runtime and behavior checks, inspect this report and select the
next authorized slice without relying on unwritten architecture decisions.
