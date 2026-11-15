# E3/E5 activation runbook (eighth patch + staged configuration)

Production Odysseus: `db79d11c` (rollback tag `pre-e3-activation` = `4f5b68d0`). Component release `c6b93a58d456` is active in `component.json`; the running gateway still serves `d17f92d164c3`. Backup taken before activation: `F:\Odysseus-Backups\deploy-e3-20261115-194046` (config, `.env`, `component.json`, plugins, online copies of `state.db`, `projects.db`, `kanban.db`, all integrity `ok`).

## 1. Apply the staged configuration (your PowerShell; nothing restarts)
```powershell
cd F:\Odysseus
$py = "C:\Users\Protagonist\AppData\Local\hermes\installs\562e58e2ea9aca80\environments\6eff8cc60cb848909fbabec0b09c88c2\venv\Scripts\python.exe"
& $py scripts\apply-e3-config.py            # dry run: prints exactly what would change
& $py scripts\apply-e3-config.py --apply    # backs up first to F:\Odysseus-Backups\config-backups\e3-config-<stamp>
```
It sets: `rea` removed from the default profile's `plugins.enabled` (folder kept); `odysseus-local-extract` installed and enabled; `mcp_servers.alyn.sampling.enabled: false`; `memory.write_approval` and `skills.write_approval: true`; `checkpoints` on with 10 snapshots, 200 MB, 7 days; `web.search_backend: searxng`; `web.extract_backend: odysseus-local-extract`; `web.keyless_fallback` and `web.keyless_rescue: false` (no silent fall back to Exa/Parallel/Firecrawl/Keenable); `SEARXNG_URL` in the Hermes `.env`; and weights in SearXNG `settings.yml` (Google CSE 1.5, Brave 0.6; DuckDuckGo stays disabled).

## 2. Restart (separate PowerShell windows, in this order)
SearXNG, only if step 1 changed its settings:
```powershell
& "C:\Users\Protagonist\Apps\searxng-windows\stop-searxng.cmd"
& "C:\Users\Protagonist\Apps\searxng-windows\start-searxng.cmd"
```
Hermes gateway, waiting up to 300 s for running turns to finish and stopping only if none are running:
```powershell
cd F:\Odysseus
.\venv\Scripts\python.exe scripts\hermes-webapp-component.py restart --drain 300
```
Do not use `--force` while a turn is running.

## 3. Live checks (needs your sign-in; about five minutes)
1. Open an old conversation that has several tool calls. Settled activity is one grey line ("Explored 3 files, ran 2 commands") with a duration such as `28s` at its right when the run took 2 s or more.
2. Find or trigger a run in which one command fails. The failed call stays visible under the folded line without clicking; expanding the line shows every call once.
3. Trigger a flagged command (for example a recursive delete of a scratch folder). The card shows "Flagged by Hermes: …", a line that approval is not a sandbox, and the menu "Allow for…" with "Allow this session" and "Always allow (all workspaces)…". Choose Reject.
4. Reload the page: the folded/expanded state is kept, and the conversation reopens with the same grouping.
5. `web_search` returns results from the local SearXNG, `web_extract` of a public page returns text, and `web_extract` of `http://127.0.0.1:8888/` is refused.
6. Composer and AI Usage page still open (untouched by this patch).

## 4. Roll back
- Desktop only: `.\venv\Scripts\python.exe scripts\hermes-webapp-component.py rollback`, then the restart above (previous release `d17f92d164c3` is kept).
- Configuration: copy back the files in `F:\Odysseus-Backups\config-backups\e3-config-<stamp>` (`MANIFEST.txt` has the hashes), and remove `plugins\odysseus-local-extract`.
- Odysseus code: `git reset --hard pre-e3-activation` (this only affects docs, the patch file, the component registration, tests, the plugin and the script).
