# Projects

Last updated: dev@working-tree | 2026-09-02

## Scope

This spec covers project organization and project-level chat context in:

- `core/database.py` for `Project` and `sessions.project_id`;
- `routes/project_routes.py` for project CRUD, chat membership, and brief refresh;
- `routes/session_routes.py` for session create/list/update project fields;
- `src/project_context.py` and `routes/chat_helpers.py` for project context injection;
- `src/session_search.py` and `/api/search?project_id=...` for project-scoped transcript search;
- `static/js/sessions.js`, `static/index.html`, and `static/style.css` for sidebar project grouping.

## Contract

Projects are a durable, owner-scoped workspace layer above chats. Folders remain lightweight sidebar grouping labels. A project can coordinate related chats by carrying configured project metadata, project instructions, a rolling brief, and retrieved sibling-chat excerpts into future turns.

Runtime behavior:

- `GET /api/projects` lists visible projects for the current storage owner;
- `POST /api/projects` creates a project with name, description, and instructions;
- `PATCH /api/projects/{project_id}` updates name, description, instructions, brief, archive, and pinned state;
- `DELETE /api/projects/{project_id}` archives the project without deleting chats;
- `POST /api/projects/{project_id}/sessions/{session_id}` attaches a chat to a project;
- `DELETE /api/projects/{project_id}/sessions/{session_id}` removes only that membership;
- `POST /api/projects/{project_id}/brief/refresh` summarizes recent project chats with a utility model or a project chat's configured model fallback.

Session creation and update accept `project_id`. `/api/sessions` returns `project_id` and `project_name` so the browser can render project grouping and show which project contributes context.

## Context

`src.project_context.build_project_context_messages()` loads project context for the current chat only when the session belongs to a visible project in the caller's owner scope. Configured project name, description, and instructions enter as a system message. The rolling project brief and retrieved sibling-chat excerpts enter as untrusted source data. Related excerpts are limited and exclude the current chat.

Project context is added in `routes.chat_helpers.build_chat_context()` after normal chat preface construction and before session history is sent to compaction/trimming. Incognito, casual low-signal turns, disabled preprocessing, and research-spinoff grounding skip project context.

## Ownership

Project routes and context retrieval use backend owner filtering. A caller cannot attach a chat to an inaccessible project, attach an inaccessible chat, or retrieve project context from another owner's project. Ownerless projects follow the same shared-row compatibility behavior as other owner-filtered domains.

## Current Gaps

- Project membership currently applies directly to chats; documents, notes, tasks, research reports, and uploads are not first-class project members yet.
- Project instructions use a single-line edit prompt in the sidebar; richer description/brief editing is backend-ready but needs a fuller UI.
- Brief refresh is manual and model-backed; there is no scheduled per-project rolling-summary task yet.
