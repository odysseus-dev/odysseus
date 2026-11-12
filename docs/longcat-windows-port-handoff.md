# LongCat: Windows port of Odysseus core (handoff)

Your worktree is ready and clean: `F:\Odysseus\integration-backups\longcat-winport`, branch `feature/windows-port-longcat`, based on the production release commit `ed0bea07`. Nothing of yours has been written there, and nothing here touches production.

## Rules of the road
- Work only in that worktree and branch. Do not edit `F:\Odysseus` (production), do not push, do not force-push, no restarts.
- One feature branch, small commits, rebase onto the release commit when it moves. Sonnet merges into the release line one branch at a time, after a focused test run.
- File ownership (exclusive): you own `src/containment*.py`, `src/process_*.py`, `src/bg_jobs.py`, `core/platform_compat.py`, non-Hermes `routes/**` and `scripts/**`, and every test outside `tests/application_modes/`, `tests/wiki_steward/` and `tests/desktop-lab/`. Sonnet owns `src/hermes_*.py`, `src/application_modes.py`, `static/js/application-modes.js`, `integrations/**`, `scripts/hermes-*.py`, `scripts/wiki*.py`, `config/hermes-*.json`, `config/release-baseline.json`, and the Hermes docs.
- Shared, change only through a note to Sonnet first: `app.py`, `core/auth.py`, `requirements*.txt`, `launch-windows.ps1`.
- `config/release-baseline.json` has exactly one writer (Sonnet). When tests start passing, send a note listing the test ids; Sonnet re-baselines once.

## Interface contracts to keep stable
1. `src/process_ownership.py`: `start_token` / `verify` and the process-lifecycle helpers the managed Hermes component uses to start and stop only its own gateway. Signatures and return shapes must not change; adding functions is fine.
2. Windows-only skip markers: use a skip with a reason string, never delete a test to make a list shorter.
3. No new top-level dependencies without telling Sonnet (installs need the owner's approval).

## What you are shrinking
The release baseline records tests that already fail on the unmodified Windows checkout: **520 failing ids across 126 modules** (plus 9 environment-dependent and 4 order-dependent). The largest groups:

| Tests | Module |
|---|---|
| 72 | `tests/test_audit_c5_benchmark_evidence.py` |
| 58 | `tests/test_editor_layer_scoped_actions_browser.py` |
| 48 | `tests/test_browser_resource_identity.py` |
| 20 | `tests/test_containment_contract.py` |
| 17 | `tests/test_code_nav_tools.py` |
| 11 | `tests/test_browser_identity_transport.py` |
| 10 | `tests/test_process_ownership.py` |
| 9 each | `test_chatgpt_subscription_usage_js`, `test_private_browser_tool`, `test_shell_routes` |

Two earlier user popups (`0x800700e8` from `containment_worker.py`) came from the background-containment tests: a worker process is launched and its pipe is closed under it. Containment, process ownership and the shell routes are the safest place to start because they also sit under the Hermes integration.

## How to check your work (no full-suite runs needed)
Run only the modules you touched, with the lab venv: `F:\Odysseus-Backups\desktop-lab\ody-full-venv\Scripts\python.exe -m pytest <module> -q -p no:cacheprovider` from your worktree, after `Remove-Item Env:PYTHONPATH,Env:HERMES_HOME`. Two SSRF tests (`test_email_account_host_ssrf.py`) hang on this machine; run them alone with a time limit or skip them.

## Reporting back
When the shared wiki goes live, `longcat` is added to `steward.json` by the owner (a one-line reviewer step) and you report with `wiki_begin` / `wiki_sync` / `wiki_record_task` / `wiki_handoff`. Until then, write a short note in `docs/longcat-status.md` on your branch.
