# Projects: one place to create, open and resume project-bound coding sessions

This builds on the Projects overlay (`odysseus-resume` Desktop plugin, `src/hermes_local_api.py`, `src/projects_dashboard.py`). There is one dashboard and one
data layer; nothing here adds a database, a permission store or a second page.

## Four things that share a folder path (never interchangeable)

| Thing | Where it lives | What it means |
|---|---|---|
| Project registration | Hermes `projects.db` (`projects.*`) | Groups conversations by folder. Not a permission. |
| Folder added as context | one message (`@folder:`) | Read-only reference for that message. |
| Conversation working directory | `sessions.cwd`, set when the session is created | Where that conversation's tools run. Never changed silently; moving a conversation is an explicit action (`session.workspace.move`) refused while it runs. |
| Authorized write workspace | Odysseus workspace grants | What the agent may change. Managed in Access / the grants CLI, independent of the three above. |

## Flow

1. **Create or open**: Projects page > New project, with a folder field that offers the machine's drives (file system shown: NTFS, Btrfs) and a live check
   (exists, Git branch, current write permission). Read-only routes `fs/drives` and `fs/check` answer under the existing access grant; they list no folder contents.
2. **Start a conversation here**: `+ New conversation here` calls `host.newChat(undefined, { cwd })`. The Desktop opens a fresh draft with that folder as an explicit
   workspace target (the same code path as picking it in the composer), so the first message creates the session with `cwd` recorded. Requires the
   `webapp-desktop-workspace.patch` release; on an older Desktop the button falls back to copying the path (`host.supportsNewChatCwd` is the switch).
3. **Resume**: Sessions tab lists the conversations whose recorded workspace is inside the project's folders, with state, model and folder; Open resumes it in place.
4. **See the context**: Overview (conversations, branch, folder, open handoffs), Git (status, worktrees, commits), Wiki (verified and needs-review knowledge, tasks,
   handoffs), Access (write roots on this machine).
5. **Wiki knowledge**: `odysseus-wiki-context` matches the session's recorded `cwd` against `wiki-context.json`. A conversation created through step 2 is therefore
   recognised; a folder merely typed into a message is not.

## Windows folder picker

`webapp-desktop-workspace.patch` (applied after the route guard and MCP recovery patches) adds a typed absolute-path field and drive chips to the Desktop's remote folder
picker (the "Add folders as context" / working-directory dialog), and the `cwd` option above. It is a Hermes Desktop change and takes effect only in a release staged
with it (`scripts/hermes-webapp-component.py` stages side by side; the running release keeps working and is still reported as carrying a reviewed patch set).

## Not done / limits

- No per-project default model (Hermes has none); no Kanban or Claude Code / Codex activity feed.
- The plugin UI was checked for syntax and against the real data layer, not driven in a browser here.
- The patch builds only when a release is staged; nothing is deployed by this branch.
