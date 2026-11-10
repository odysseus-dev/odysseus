# Wave 3 browser authority: observations with page execution disabled

Starting Checkpoint A: `bc5e1ee6922000a290371f8c2aa18802a03ffcad`, tree
`8e09cc2560f50a3472e06ec614d6ada028b7eb18`. Branch, cleanliness, both A
commits and canonical Wave 5B ancestry were verified before edits. Existing
145-file Checkpoint A baseline passed 3369 tests, with 3 platform skips
and 2 existing xfails.

## Producer decision and live evidence

The actual release Docker image was available locally:
`sha256:cc2d47e2327d573af01c6b027f23d2ab0f2ee9b85d658e9eb8065bd02b9c3515`
(Linux amd64). Its native binary reports exactly `agent-browser 0.35.0`.

The isolated local-launch probe performed:

1. Fresh local browser launch with the first `--pin-tab` request.
2. Create a sibling tab; capture and select an exact producer targetId.
3. `session info --no-pin-tab`, then `session info --pin-tab`.
4. Destroy the captured target using an external **test fixture**.
5. `snapshot --pin-tab`.

Both re-arm calls succeeded. The snapshot also succeeded, a replacement target
became active, and there was no `tab_gone`. Lifecycle metadata reported
`relaunchedBrowser=false`, `restartedBackground=false`, `launched=false`.
The CLI's special `session info` path does not attach the pin fields to its
daemon request. Successful flags therefore cannot establish `pin_armed_for`.
The producer audit's proposed re-arm sequence is not valid in this mode.

`tests/test_browser_producer_live_contract.py` reproduces this defect against
the actual binary, rather than treating the defect as a passing pin contract.
The four live tests also validate target/loader stability, reload/navigation,
same-document history change, distinct same-URL pages, and exact target switch
responses. Four passed in the actual release image. Raw GUIDs/CDP capability URLs
are neither printed nor saved by the tests or production adapter.

Page/document reads and effects are **unconditionally disabled before producer
dispatch**. Observations, matching preconditions, matching postconditions,
successful pin flags, exact approval and child scope never override this gate.

## Identity architecture

`src/browser_identity.py` owns producer validation, private configuration,
registration, observations, metadata execution, resource binding and CDP
observation. `src/agent_runtime/resources.py` supplies immutable types:

- `BrowserSessionObservation`: trusted namespace, version, platform, binary
  digest, configuration digest, selector-only session key, one nested Wave 5B
  `ProcessIdentity`, domain-separated browser GUID digest, and deterministic
  session-incarnation digest. No duplicated start-token abstraction.
- `BrowserSessionResource`: the observation plus mandatory owner/thread binding.
- `BrowserPageResource`: exact parent session, producer targetId, opaque loaderId,
  explicit page/document scope, and alias/URL audit metadata. Page authority is
  session + target; document authority additionally includes loader. Metadata
  does not participate in the authority key.

Registration is server-only, checks the installed producer and creates private
owned configuration. It does not spawn or adopt a daemon/browser. Model-facing
lookup never creates a session. Legacy lifecycle records are not authority.
There is currently no model-facing launch/enrolment operation; default/legacy
sessions without a registered observation fail closed.

An explicit trusted observation checks active producer state, captures the
daemon incarnation around exact executable observation, obtains the local CDP
capability, rejects lifecycle launch/replacement, validates tab schema and the
absence of labels, cross-checks CDP target type, captures main-frame loaderId,
detaches and rechecks daemon/browser identity. A changed session invalidates
every earlier page/document observation. A changed loader invalidates document
scope; a same-URL or same-alias replacement never inherits target scope.

The proposed pin re-arm is **not implemented as an authority-establishing
action**. `pin_armed_for` stays unset; even modifying this field cannot enable
page execution. No alternate pin workaround or producer fork is introduced.

## Trusted producer and observation transport

Only explicit glibc Linux release binaries are allowlisted:

| Platform | Version | Native binary SHA-256 |
| --- | --- | --- |
| linux-x64 | 0.35.0 | b7a28c3a43a7008dd02585e2e60c391c08983f7a099149caed63c9f13f57b752 |
| linux-arm64 | 0.35.0 | 92cd7d0897837ac648b9a6ab1965c69c5920e0f54df57e4295cdb1143b0541c8 |

These digests were observed from the release image's installed package. x64 was
executed live; arm64 execution remains a separate architecture gate. Selection
uses `/usr/local/lib/node_modules/agent-browser/bin/agent-browser-<platform>`.
Version, hash, ownership, permissions and schema are checked. No PATH search,
npx execution/download, cache glob, mtime selection or replacement download.
0.27.0, unknown versions, platforms and hashes fail closed.

The CDP sidecar accepts only loopback browser websocket capability URLs and
only `Target.getTargets`, `Target.getTargetInfo`, `Target.attachToTarget`,
`Page.getFrameTree`, `Target.detachFromTarget`. It does not enable domains,
evaluate, navigate, close targets or expose arbitrary CDP to tools. Frame identity
must equal the captured target and loaderId must be nonempty. Requests have
3-second bounds and bounded frame/message sizes. This is producer identity
observation, not semantic evidence or trust elevation.

The capability URL stays in a non-serializable, non-repr memory field. Metadata
revalidation connects to that captured browser endpoint, rather than calling
`get cdp-url` again: that getter can auto-launch a replacement. Failed or changed
daemon/CDP observations invalidate the registered session; no rediscovery/retry.

Configuration is exactly `{}` in an owned private cwd, with observed inode and
permissions checked. Client environment is constructed from an explicit fixed
allowlist: owned HOME/TMPDIR/socket directory, system PATH, Chromium path and
idle timeout. Ambient AGENT_BROWSER/CDP/provider/profile/state/config/proxy/XDG
settings and model subprocess environment are not inherited. Configuration is
part of the incarnation digest; credentials are not serialized.

## Operation and approval boundaries

| Operation | Binding | Current execution |
| --- | --- | --- |
| `session_info` | Exact registered session + caller/request | Supported metadata only; no URL/title/content, target selection or launch |
| New page, initial open, tab list, whole-session close | Session/creation producer guarantee | Disabled; no trustworthy atomic creation/control contract admitted |
| Select/close page, navigate/reload/back/forward, time wait, viewport scroll, page network/console | Exact session + target | Disabled before dispatch |
| Click/fill/press/evaluate, selector/ref interactions and waits | Exact session + target + loader | Disabled before dispatch |
| Snapshot/read/find/screenshot | Exact page, loader sandwich for any future read | Disabled before dispatch; no replacement-page read |

Failure is structured: `failure_kind=browser_page_authority_unavailable`,
`executed=false`, `retryable=false`, `producer_capability_unavailable=true`.
Missing session authority produces a separate session-unavailable failure.
No timeout or post-check can authorize execution against a replacement.

RequestAuthority version 5 carries explicit session/page ceilings. Old snapshots
restore empty browser scopes. Exact proposal capture binds normalized operation,
request/owner/thread and the exact session/page/document observation. Metadata
execution revalidates before one-use claim and at producer entry. Restoration
adds no general scope. Unsupported page approvals are never claimed/executed.

Child scopes validate parent observations before intersection. Session ceilings
require exact incarnation; page ceilings require exact parent + target; document
ceilings also require loader. A page child cannot acquire session control, and a
document child cannot renew a replaced document. Discovery adds no authority.

Model batches, raw tab/window/frame/connect commands, labels, raw targetIds,
configuration/session/CDP/provider/profile/state flags and flag-like positional
values are rejected. `page: tN` is strictly validated. The preview's automatic
open/snapshot batch rewrite and native read/post-click batches/recovery engine
are removed. Raw global Playwright browser control calls fail closed as well;
remote backend/stdio identity is not page authority. Other remote/MCP transport
mechanics remain unchanged and external.

Client invocations are bounded at 20 seconds, below the source-verified 30-second
read/resend floor, with held-handle kill/wait on timeout/cancellation and no
Odysseus retries. Immediate producer EOF/reset retries cannot be eliminated by
this wrapper. **No exactly-once claim is made; all effects remain disabled.**

## Control state and prior unsupported paths

Private browser runtime/configuration is protected by central control-plane
resolution and native launch workspace guards, including actual configured
directories. Direct, symlink and hardlink tests cover it. These are pathname/
inode observations, not race-freedom claims or a new containment policy.
Service-owned Wave 5B cleanup remains independent of model authority; shutdown
does not discover/download/run an untrusted producer binary.

Re-audit of Checkpoint A seams found:

| Path | Remaining enforcement |
| --- | --- |
| PTY/native manager routes | `routes/shell_routes.py:setup_shell_routes.shell_exec/shell_stream` call `_require_admin` before `_exec_shell/_generate_pty/_generate_tmux`; internal tool controls denied; auth-enabled human administration and explicit auth-disabled direct-local operator administration remain separate |
| Additional process producers | `resources.ProcessResource.__post_init__` admits only frozen native producer/role combinations; `process_resources.resolve_process_operation` requires sealed observations |
| Raw scheduled SSH | `TaskScheduler._execute_action` → `builtin_actions.action_ssh_command` → `_run_subprocess` refuses SSH without an external workload adapter |
| Local Cookbook scheduled auto-stop | `routes/cookbook_routes.py:setup_cookbook_routes.protect_native_control` applies shell admin boundary to local mutation; `tools/cookbook._cookbook_kill_session` refuses registry-less local control; legacy internal shell route cannot gain administration |
| Legacy/unscoped tasks | `authority.restore_task_authority` → `process_resources.resolve_process_operation` admits no missing creation scope |
| Anonymous administration / generic app_api | `owned_resources.needs_owned_binding` rejects shell/model/Cookbook namespaces; `_require_admin` rejects auth-enabled anonymous and auth-disabled untrusted/forwarded requests; direct-local operator administration is supported |

No model-reachable page producer entry remains in the native/research wrapper.
Trusted observation/setup methods are not tools or routes. Native arbitrary
program/network effects and remote workload effects retain their existing
explicit launch/backend boundaries; this checkpoint adds no general network
egress/provenance policy (Wave 4).

## Validation and remaining release gates

`wave-3-final-tests.txt` contains 149 files, retaining all 145 Checkpoint A files
and the exact prior 88-file selection. Legacy positive page/batch/recovery tests
are replaced by explicit unsupported-before-dispatch tests; formatting,
filesystem, YouTube, Wave 5B ownership/cleanup and research fallback tests remain.

Final resource/authority/approval focused run: **1,425 passed**. Final 149-file
integrated gate: **3,776 passed, 7 skipped, 2 xfailed**. The exact old 88-file
selection and all 145 Checkpoint A files were verified as subsets of this gate.
The 7 skips are `/tmp` not being a symlink, applicable RLIMIT_AS already
available, the Windows Ollama startup guard, and four explicit Docker-only
producer probes. Those four probes ran separately: **4 passed** on the actual
release x64 image. Index/schema/configuration checks separately passed 40 tests.

Full-suite failure classification was performed against an isolated archive of
the frozen Checkpoint A (no checkout/rewrite): replay of the initial 82 failing
cases reproduced 79. Two browser/schema regressions were corrected. The third
case, `test_dispatcher_rejects_approved_document_action_without_target`, passed
alone but failed identically on the frozen archive when preceded by
`test_scheduler_restart_doublefire.py`. That fixture permanently replaces
`core.database.SessionLocal/engine` with a task-only database. This is an
existing suite-order issue, not a browser authority regression. Missing Node
Playwright dependencies and legacy fixtures that expect unscoped execution
also remain explicit full-suite limitations; they are not skipped or counted
as passes. New browser test environment documentation also records the existing
memory backend owner settings required to regenerate the configuration page.

Final full repository run: **12,310 passed, 76 failed, 65 skipped, 2 xfailed,
6 subtests passed** (403.66 seconds). Every final failed node was reproduced on
frozen Checkpoint A, using the scheduler-order reproduction for the document
case. This is **not a green full-suite gate**. Exact failed node IDs and totals
are in `validation/wave-3-browser-final-results.json`.

Full-suite skips include smoke/live endpoints without an instance or opt-in,
the four separately executed release producer probes, the three platform cases,
missing caldav/chromadb/fitz/openpyxl/markitdown/libmagic/Node Playwright,
ffmpeg format limitations and missing rsvg-convert. Nothing was silently
converted into a pass. The two existing strict xfails in
`test_runtime_behavior_regressions.py` cover negative web-search wording that
does not yet suppress the offered web tools: "Do not search the web" and
"No web search please".

Compileall, whitespace, conflict-marker and unmerged-index checks pass.
The coherent fail-closed implementation is available for independent review;
full-suite cleanup remains outstanding and page enabling is not merge-ready.

## Exact production changes since Checkpoint A

```text
src/browser_identity.py
src/agent_runtime/resources.py
src/agent_runtime/authority.py
src/agent_runtime/process_resources.py
src/agent_tools/web_tools.py
src/tool_execution.py
src/tool_approvals.py
src/tool_schemas.py
src/tool_index.py
src/clean_agent_preview.py
src/agent_loop.py
src/constants.py
scripts/generate_env_reference.py
```

`website/configuration-reference.md` is regenerated documentation. Runtime
instructions/schema/index no longer advertise executable page interactions.
The agent loop change is only the browser prompt snippet; it is not decomposed.
Wave 5B lifecycle mechanics and MCP transport are not modified.

```sh
python3 -m pytest -q -rs $(cat docs/runtime-decomposition/wave-3-final-tests.txt)
python3 -m pytest -q -rs
python3 -m compileall -q app.py core routes services src tests scripts
git diff --check
git grep -n -E '^(<<<<<<< |=======$|>>>>>>> )' || true
git ls-files -u
```

Live release probe (source checkout mounted read-only, isolated container state):

```sh
docker run --rm --network none \
  -e ODYSSEUS_BROWSER_LIVE_CONTRACT=1 -e ODYSSEUS_DATA_DIR=/tmp/w3-data \
  -e DATABASE_URL=sqlite:///:memory: -v "$PWD:/app:ro" \
  --entrypoint python odysseus-maintainer-preview-odysseus:latest \
  -m pytest -q -rs -o cache_dir=/tmp/w3-pytest-cache \
  tests/test_browser_producer_live_contract.py
```

The x64 probes pass by proving observation contracts **and the known defect**.
They are not a positive merge gate for enabling page effects. Re-enabling needs
a separately audited/allowlisted producer that executes only while expected
browser incarnation, targetId and optional loaderId still match, rejects stale
state atomically before reading/effect, and does not resend an indeterminate
effect. No producer changes are implemented here.

The original positive 18-case Docker gate remains mandatory before re-enabling:
stable/repeated targets; reload; cross-/same-document navigation; identical URLs;
close/recreate; browser and daemon replacement; popup races; destroyed targets;
local-launch pin/atomic binding; exact target switch; A-F label collision;
lifecycle metadata; timeout/duplicate effects; bfcache; prerender/frame invariant;
strict schema. It must run per supported release architecture. Pin success and
pre/post checking alone can never substitute for atomic binding.

P1: producer page/document capability unavailable; unregistered sessions and
Checkpoint A compatibility paths intentionally denied. P2: private-runtime scan
cost/retention, filesystem observation races and architecture-specific live
coverage. Wave 4 remains responsible for effects/provenance/egress and truthful
completion evidence; no Wave 4 journal or lifecycle redesign is introduced.
