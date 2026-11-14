# Projects in HermesGUI: native management, import record, Sahib Desktop

Project management is the Hermes Webapp's own (per-profile `projects.db` in the gateway home, written through the gateway's `projects.*` methods). Odysseus does not copy those
controls and never holds the gateway session token. The Odysseus dashboard reads the same `projects.db` read-only and its **Manage in Hermes** button only opens Hermes.

## Turn on project grouping (exact steps; verified in Firefox on the isolated lab)
The sidebar groups conversations by date until you choose otherwise. This is a per-browser setting.
1. Open Hermes (Odysseus Projects dashboard -> **Back to Hermes** / **Manage in Hermes**, or the **Projects** row in the Hermes sidebar leads back and forth).
2. In the left sidebar find the **SESSIONS** header. At its right there are two small buttons: **+** and **Filters** (a funnel-style icon).
3. Click **Filters** -> **Grouping** -> **Project**. The list now shows project headings (registered projects first, then folders Hermes detected on its own).
4. The **+** at the SESSIONS header is now **New project** (name, **Add folder**, **Create**). A project row has *Open*, *Hide sessions* and *New session in <project>*.
5. On any conversation: **Session actions** -> **Move to project** -> pick a project. **This changes the conversation's real working folder** (it only updates what Hermes stores for that
   conversation and what its agent uses next; no files move, write grants are unchanged). Do not do it to a conversation that is running a turn.
6. Optional: Settings -> Keybinds has an unbound action **Cycle session grouping** (`view.cycleSidebarGrouping`); bind a key to flip date/project/status without the menu.

## Why discoverability is not improved further
Extension points checked in the pinned Webapp: plugins may add sidebar rows, routes, palette rows and keybinds, and run a fixed list of view/navigation actions
(`PLUGIN_APP_ACTIONS`). That list does not include grouping, and the grouping setting is a private store. Setting it from a plugin would mean writing the Webapp's private local-storage
key (unsupported, can break on an update), so it is not done. The supported aids are: this page, the **Manage in Hermes** tooltip, and the optional keybind in step 6.

## Import record (2026-11-13)
`scripts/hermes-projects-import.py` created, through `projects.create` only: Sahib Android (`E:\Project\Sahib-Android`), Masar (`E:\Perso\Job search\Jobup-Tauri-Phase1`), Mihak (`E:\Project\Mihakk`),
with the legacy names and colours. `projects.db` was copied first with SQLite's online backup (it includes the WAL), to `F:\Odysseus-Backups\projects-nav-deploy-20261113-1714\projects-db-pre-import\`.
Conversations, their folders and write grants were not touched. A second run plans nothing.

## Sahib Desktop (not imported yet)
- The recorded folder `E:\Backups\Sahib-Working-0.2.5-2026-11-03` no longer exists. The 0.3.0 backup (`E:\Backups\Sahib-Working-0.3.0-2026-11-10`) is a **snapshot**, not the working project: its own
  RESTORE.txt says so, it has no `.git` at its root (`source/` is a clone), and the live work is elsewhere.
- The live project is `C:\Users\Protagonist\workspace\sahib-work` (git, branch `design/desktop-v2`, HEAD `9f72001`, clean), the same commit as the 0.3.0 backup's `source/`. Its origin was the (now gone) 0.2.5 backup.
- The one historical conversation ("Desktop Design Review Skill Creation", id `4019e2a43544`) has stored working folder = the vanished 0.2.5 path.
- To associate it without changing its working folder: create the project on the live folder and attach the vanished path as an extra folder (Hermes matches conversations to projects by folder). Verified on the lab:
  `projects.add_folder` accepts a path that no longer exists, the conversation then counted under the project, and its stored folder stayed unchanged.
  `python scripts/hermes-projects-import.py --use-folder "Sahib Desktop=C:\Users\Protagonist\workspace\sahib-work"` shows this plan (dry run); `--apply --backup-dir DIR` does it.
- The native UI cannot attach a non-existent folder (its picker only selects existing ones), which is why this goes through the tool. Using **Move to project** instead would re-home the conversation to `sahib-work`: also safe (the old folder is gone) but it is a real working-folder change.
- Write access to `sahib-work` is not granted by any of this; the grant stays a separate decision.
