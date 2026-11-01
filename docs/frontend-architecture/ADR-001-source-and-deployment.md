# ADR 001: Feature-first native ESM with controlled deployment output

Date: 2026-10-13. Status: implemented F1 proof, proposed for maintainer adoption.
Baseline: official `upstream/dev` at `8feb50e89468235cd3e0986da80edbd3acaf2c40`.
Scope: source ownership and distribution foundation. Notes implementation is deferred.

## Decision

Maintain migrated JavaScript in `frontend/src/`. Publish native ES modules to
explicitly registered paths in `static/js/`, and commit source and output together.
Use `python scripts/build_frontend.py` to copy bytes without rewriting imports,
bundling, minification, source maps, timestamps or generated headers. A checked-in
`frontend/modules.json` maps source paths to deployment paths. All other static
files remain authored legacy modules or deployment assets until a reviewed slice
transfers their ownership. Do not claim that the whole static tree is generated.

The proof transfers maintenance of the stateless action-menu ordering helper to
`frontend/src/shared/actionMenuOrder.js`. Its output remains
`/static/js/actionMenuOrder.js`, with the same three exports and identical bytes.
All seven current consumers, HTML, service worker and runtime entry points retain
their existing URLs. There is no wrapper, extra request or second browser module
instance. The source copy is a development input, not a second runtime entry point.

Use `frontend/src/app/` for composition, `features/<feature>/index.js` for feature
public APIs, and responsibility directories only when implementation requires
them. The [handbook](handbook.md) defines dependency, ownership and lifecycle rules.
F1 creates only the shared proof, without empty application or feature scaffolding.

## Evidence and alternatives

Both options were executed against this helper, rather than assumed to work.
Measurements are local compatibility observations, not general performance claims.

| Property | Controlled native ESM copy, selected | esbuild 0.25.12 controlled ESM build |
| --- | --- | --- |
| Proof output | 1,688 bytes, identical SHA-256 to existing runtime | 1,554 bytes with `--bundle --format=esm --target=es2022 --platform=browser` |
| Repeatability | Clean temporary publication and idempotent rerun tested | Two independent compiler invocations emitted identical bytes |
| Runtime contract | Existing exports, imports, cache entry and module identity unchanged | Export names, icon and ordering matched in an executed Node import |
| Tooling | Python standard library; no production dependency | Compiler and platform binary needed for artifact production |
| Local cost | Copy check: 0.03 s, 15,596 KB maximum RSS | Cached `npx` ESM transform: 0.58 s, 114,196 KB maximum RSS |
| Incremental migration | Explicit file ownership, preserved module boundaries | Feasible for the stateless leaf; stateful facade/chunk identity still needs a separate graph spike |
| Release implications | Commit output; install/startup paths consume existing static assets | Would also need committed or release-produced output plus compiler locking and facade/chunk manifest policy |

The timing rows measure different tool paths, including `npx` overhead. The
compiler's single-leaf success does not establish stateful graph or lazy chunk
compatibility. The 134-byte bundle reduction does not justify that additional
contract in F1. A future compiler adoption requires a new ADR and graph-level
evidence. React, Vue, TypeScript, a global state library, hashed chunks and a
development server have no demonstrated necessity here.

Acorn 8.19.0 is added only as an exact, lockfile-pinned development dependency for
architecture checks. A real JavaScript parser is needed to enforce boundaries
across multiline imports, re-exports and lazy thunks without treating comments
and string literals as executable imports. It is MIT licensed, about 568 KB
unpacked, and contributes no deployed JavaScript or runtime dependency.

## Distribution contract

1. Source and generated output are reviewed and committed together. CI checks
   byte parity; it does not silently rebuild and accept drift.
2. Native Python startup continues to use `STATIC_DIR` and `/static`, without
   invoking the publisher. Native installers already obtain tracked static files.
3. Docker's current `COPY . .` includes the output. `.dockerignore` excludes
   `node_modules/`. The Docker runtime and entrypoint require no frontend build.
4. `Odysseus.spec` and `build-windows-portable.ps1` already include `static/`.
   The macOS wrapper uses the Python checkout and its static assets. Neither
   needs the source tree or frontend tooling to serve the proof.
5. A release producer runs the checks before packaging and distributes the
   entire matching static graph. The publisher is not a transactional deployment
   mechanism. Partial copying of a new graph into a live deployment is unsupported.
6. New output paths must declare LF checkout behavior in `.gitattributes`, as
   this proof does, so Windows checkout conversion cannot create false byte drift.
   Before checking parity or writing output, the publisher uses the manifest to
   require effective `text: set` and `eol: lf` on every source/output path. It asks
   Git in a temporary repository containing only the applicable repository-owned
   `.gitattributes` files, including nested rules. Global/local Git overrides
   cannot supply missing declarations. No blanket legacy-JS rule is introduced.
   Git is a development/publication prerequisite, not a startup dependency.

An isolated distribution probe executes the real FastAPI lifespan, shell and
static requests after omitting `frontend/` and `node_modules/` and removing Node
from the child process search path. It verifies bytes, MIME, CSP, revalidation,
source-tree 404 behavior and static path confinement. The same probe runs in an existing Docker image
with networking disabled. This is not a claim that a fresh full Docker image or
Windows/macOS executable was built; see [validation](validation.md).

## Imports, identity and offline operation

New maintained modules use one unversioned deployment URL. Existing legacy URLs,
including their version queries, stay unchanged. A copy mapping must preserve
the resolution of every literal relative import; the architecture checker rejects
a mapping that changes that resolution. Canonical absolute `/static/js/...`
imports are also supported. No bare packages or browser imports of `frontend/src`
are supported. Existing graph and identity guards continue to cover the runtime.

Each registered output joins the exact `PRECACHE` or `PANEL_PRECACHE` array. The
new checker parses those arrays, rather than accepting a URL mentioned in a
comment. Installation fetches do not evaluate lazy modules. Import-time DOM work,
timers, state creation and network I/O must be characterized before changing an
entry point's load timing. New source dependency cycles are rejected; existing
legacy cycles are inventoried and remain outside the new rules.

The proof already has its exact precache entry, so no SW cache-name or HTML edit
is needed. Its first evaluation in a fresh offline browser page passes, and
importing the panel registry does not evaluate the editor. The current worker's
`/static/` scope cannot control the SPA root. Open PR #6227 addresses that existing
limitation; F1 does not fix or mask it. Full root offline reload is not certified.

## Security and compatibility

Keep backend authorization and owner scope authoritative. No agent, database,
auth, CSP, static mount or API implementation changes belong to this decision.
The helper remains same-origin ESM without eval, compiler runtime or external
resources. No new source maps or vendored assets are emitted; existing SRI/CSP
policy and distribution notices remain unchanged. The publisher validates path
containment, symlinks, duplicate ownership and unmapped source before writing.
It never deletes unowned static assets. Deleting or retiring outputs is an
explicit reviewed migration operation, not automatic cleanup.

## Rollout, rollback and limits

Adopt F1 for controlled, reviewed slices after maintainer review. Leave Notes,
bootstrap, HTML and service worker unchanged here. Register new files explicitly;
update offline manifests only when new runtime dependencies are introduced.
F1 rollback can remove the source manifest, proof tooling, gates and documentation
without changing runtime bytes. Later rollback restores the complete prior
source/output/facade/precache set through an authorized follow-up change.

Boundary gates cannot prove semantic purity, owner isolation or correct teardown.
Domain platform/clock checks are intentionally conservative syntax guards;
behavior tests still establish those contracts. Worker URLs, styles and vendored
libraries are outside the maintained-JS import gate and need explicit review if
a future lane changes them. Legacy exceptions require an exact specifier and a
module-scoped reason; there are none in F1. New source is not permission to
grandfather new dependency cycles or to replicate broadly shared mutable state.
