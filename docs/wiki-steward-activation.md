# Wiki Steward: controlled activation (plan, order, rollback)

Status: prepared and rehearsed on disposable copies. Nothing here has been applied to production.

## Roles of the two paths

| Role | Today | Target |
|---|---|---|
| Wiki Steward SERVICE source (`src/wiki_steward.py`, `scripts/wiki_mcp.py`, the context plugin) | worktree `integration-backups\webapp-proxy` (branch `feature/wiki-steward`, 4b61b8fb, about 40 commits behind) | `F:\Odysseus` (versioned with the application) |
| TRACKED repository (what claims, evidence and freshness are checked against; `steward.json` `projects.*.repo`) | the same worktree | `F:\Odysseus` |
| The wiki itself (data) | `F:\Projects\Odysseus-Wiki` (private git repo, no remote) | unchanged |

Both roles move to the production checkout once it contains the Wiki code. One stable path, no development worktree in the live path. The two roles stay separate settings (MCP entry, `steward.json`, `wiki-context.json`), so they can diverge later.

## Branches (all local, unpushed)

| Branch | Content | Production impact |
|---|---|---|
| `integration/wiki-on-production` | production `7b5db688` + Wiki Steward + context plugin. 29 files added, 1 doc modified (`docs/master-plan-next-phases.md`), no application code touched, no merge conflicts | files only |
| `feature/component-plugins` (on top) | opt-in component actions `wiki-context` and `security-plugin`, the reviewed claims, the importer, this document | adds methods and constants to `src/hermes_webapp_component.py`; nothing calls them unless asked |

Existing Wiki records stay valid: all 24 commit references in the task records resolve and are ancestors of the new HEAD; the audit chain (7 entries) is untouched by a repoint.

## Order (each step waits for approval; one gateway restart in total)

0. Safety: `git -C F:\Projects\Odysseus-Wiki bundle create <backup>\wiki-pre.bundle --all`; copy `config.yaml` and `component.json` of the managed home; note the wiki HEAD (`9e59db2`) and production HEAD (`7b5db688`); `git -C F:\Odysseus tag pre-wiki-integration 7b5db688`.
1. Production files: `git -C F:\Odysseus merge --ff-only feature/component-plugins` (needs a clean checkout; the untracked `.env.pre-projects-*` is not touched). No restart: the running app has its modules loaded, and nothing imports the new files.
2. Source alignment in the wiki (human): replace the two `repo` values in `steward.json` (`F:\Odysseus\integration-backups\webapp-proxy` becomes `F:\Odysseus`; a 2-line diff), then in a terminal `python scripts\wiki.py --root F:\Projects\Odysseus-Wiki adopt "repoint odysseus and selftest to the stable Odysseus checkout"`, then `integrity`.
3. Import: `python scripts\wiki-import-claims.py --wiki-root F:\Projects\Odysseus-Wiki --claims config\wiki\odysseus-initial-claims.json` (check only), then again with `--apply`. Expected: 5 `verified`, 5 `needs-review`.
4. Overlays (writes to the managed home, takes effect at the restart):
   - `python scripts\hermes-webapp-component.py wiki-context --wiki-root F:\Projects\Odysseus-Wiki --steward-src F:\Odysseus\src --workspace F:\Odysseus --apply`
   - `python scripts\hermes-webapp-component.py security-plugin --apply`
   - the `wiki` MCP entry: `args` from the worktree script to `F:/Odysseus/scripts/wiki_mcp.py` (one line in the managed `config.yaml`, backup first).
5. One gateway restart at an idle moment: `python scripts\hermes-webapp-component.py restart --drain 300` from a separate terminal (it refuses inside the gateway and while turns run). The Odysseus app is not restarted. New chats then load the plugin, the patched skill index and the MCP entry. The first MCP start runs the usual mechanical reconcile and records the repository's new commits as task logs (40 on the current branch); it writes no claim.
6. Check in a new chat in `F:\Odysseus`: the start note appears once, a question about the proxy returns the verified claim, an unrelated chat shows nothing.

Possible without a restart (not verified): the Desktop Plugins page can enable a plugin live; whether a `pre_llm_call` hook registers that way is untested, so the plan assumes the restart.

## Rollback

| Step | Undo |
|---|---|
| 5/4 | disable `odysseus-wiki-context` in `plugins.enabled`, delete `plugins\odysseus-wiki-context` and `wiki-context.json`; move the folder under `<component>\backups\hermes-security-<stamp>` back to `plugins\hermes-security`; restore the `wiki` MCP args line from the backup; restart with `--drain`. |
| 3, 2 | `git -C F:\Projects\Odysseus-Wiki reset --hard 9e59db2` (the recorded pre-migration commit). Rehearsed on a clone: claims 0, 24 task records, 7 audit lines, `steward.json` byte-identical, `integrity` ok. The steward's own `revert` undoes only the newest commit, so use the reset for more than one step. |
| 1 | `git -C F:\Odysseus reset --hard pre-wiki-integration` (the change is additive files only). |
| any | the wiki bundle from step 0, or the encrypted disaster-recovery backup. |

## Attribution

`hermes-security` is by NousResearch (hermes-official-plugins, Apache-2.0). The reviewed patch modifies its `hermes_security/plugin.py`; the patch states the change and its origin, the patched plugin carries `ODYSSEUS_PATCH.txt`, and the upstream LICENSE and NOTICE stay in place. The installed folder is never edited in place: a staged copy is checked (`git apply --check`, compile) and swapped in, with the original kept in the component backups.

## Known limits

- Plugins load at gateway start; a chat that is already open keeps its old context behaviour.
- Workspaces outside `F:\Odysseus` (for example the LongCat worktree) are intentionally not tracked.
- `selftest-unit` still runs from the lab venv `F:\Odysseus-Backups\desktop-lab\ody-full-venv`; switching it to `F:\Odysseus\venv` is optional and not part of this change.
