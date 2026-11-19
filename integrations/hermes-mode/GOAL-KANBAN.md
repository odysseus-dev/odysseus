# Goal–Kanban autonomous orchestration + Windows worker git-hang fix

This directory's reviewed-patch sequence now carries three additional patches (positions 9, 10
and 11 in `webapp_patches()`):

- `goal-kanban.patch` — binds Desktop `/goal` sessions to durable Kanban worker tasks and wake
  events (Hermes-side change, 6 files).
- `webapp-worker-git-hang.patch` — hardens every git probe in `hermes_cli/version_info.py`
  against Windows credential-pipe hangs in unattended worker processes (1 file).
- `webapp-goal-drain-yield.patch` — makes the goal continuation loop yield to gateway drain:
  goal-bound wakes defer (paused-goal, cursor held, event identity preserved) while the runner
  drains, so `restart --drain` converges without `--force` on active-goal hosts (2 files:
  `gateway/session_automation.py` + the bridge test as a new file). Registration-adapted form of
  reviewed commit `59ec7cd715`; the literal format-patch cannot apply because `goal-kanban.patch`
  omits the bridge test file this patch modifies — never register the literal alone.

All three are applied automatically by the normal managed-install path (`ensure`/`repair`) on top of
the pinned Hermes revision in `config/hermes-component.lock.json`. No manual edits to the
managed release are required.

## What the integration provides

- Persistent `/goal` → Kanban task ownership (`goal_task_key`), bound per goal instance.
- Automatic lead continuation through Kanban wake events (stable admission IDs; the board
  cursor advances only after admission).
- Idempotent task creation: replayed keys return the original card; no duplicate tasks.
- Worker admission and eligibility handling (`kanban.dispatch_profiles` allowlist; profiles
  outside the allowlist never dispatch, so historical cards stay inert).
- Review handoff (`kanban_request_review`) with review-column dispatch to a different profile,
  and second-wave replanning via parent dependencies and captured handoffs.
- Goal completion and blocked-state handling: paused goals keep wake events pending;
  cleared/done goals consume them silently; blocked owned work pauses the goal judge instead
  of spinning.
- Windows git subprocess hardening: non-interactive environment
  (`GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=Never`, `GIT_ASKPASS=echo`, `GCM_PROVIDER=""`,
  `GIT_NO_LAZY_FETCH=1`) and full process-tree termination on timeout, so a surviving
  credential-helper grandchild cannot strand pipe handles and wedge `communicate()`.

## Clean installation

1. The component clones the pinned Hermes revision (partial clone) and checks it out.
2. `apply_patch` applies the full reviewed sequence in order, including the two new patches.
3. `verify_checkout` / `patch_state` accept the tree as exactly `pin + reviewed sequence`.
4. Security and workspace restrictions are unchanged: the new patches touch only the seven
   source files listed in their headers; no guard, grant, auth, or approval code is modified.
5. Configure eligible workers with `kanban.dispatch_profiles` (comma-separated profile names).
   Without an allowlist entry, existing cards are never auto-dispatched.
6. Reviewer profiles must carry the `sdlc-review` skill: review claims force-load it, and a
   profile without it fails closed (`kanban_skills_unavailable`).

Patch files MUST be stored with LF line endings. CRLF patch content makes `git apply --cached`
produce CR-laden blobs, which fails the `_sequence_matches` tree comparison (`foreign`).

## Upgrades and retirement

- Adding these patches to an existing installation follows the normal release-staging path:
  `repair` stages a NEW release with the full sequence; the previous release is retained for
  rollback. An in-place `apply_patch` on an older patched tree refuses (fail-closed) — the old
  tree keeps verifying against its own older sequence and is never corrupted.
- When a future Hermes pin already includes either upstream change, retire the corresponding
  reviewed patch: remove its method from `webapp_patches()` and delete its patch file. Leaving
  it registered makes `apply_patch` refuse on the new pin (fail-closed) until retired.
- Older supported sequences (e.g. the seven-patch `usage_patches()` set used for rollback)
  remain recognized unchanged.

## Verification summary

- Clean disposable clone of the pin: full 10-patch sequence applies; `verify_checkout` and
  `patch_state('applied')` pass; the bridge's 8-test unittest suite passes against the freshly
  patched tree; `get_version_info()` returns in <0.2s in a fresh process on a partial clone;
  mock checks confirm the protective environment and Windows tree-kill on timeout.
- Upgrade simulation: an 8-patch release verifies; the 10-patch list correctly reports it as
  `foreign` (triggering release staging); in-place application refuses without corruption; the
  old release still verifies afterwards.
- Live pilot (Windows): dispatch on three providers (Claude subscription, OpenAI Codex,
  Nous qwen4-max via per-task model/provider override), 35/35 worker tests independently
  re-verified, cross-review approval, second-wave dependency gating, idempotency replay dedup,
  restart recovery with no duplicate cards, and an autonomous goal-closure loop with zero user
  messages.

## Known limitations / follow-ups

- **Goal loop vs controlled shutdown:** an active goal's queued wakes/continuations keep
  session admissions `started`, so `restart --drain` may never observe an idle gateway. The
  operator path is `restart --force` from a separate terminal (interactive confirmation).
  Making the goal loop yield to drain is an explicit follow-up; restart handling is NOT
  claimed complete here.
- `goal_mode` per-card judge loops and compaction-rotation re-wake were not exercised live.
- The reviewer rejection/rework path (`kanban_request_changes`) was not exercised in the pilot.
- Claude subscription transport: the SDK-based provider plugin may carry Anthropic terms
  considerations; CLI transport is the conservative choice for subscription accounts.