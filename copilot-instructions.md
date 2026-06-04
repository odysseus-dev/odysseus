# Copilot / Agent Instructions — Odysseus Enterprise Command Center

LANG: Arabic + English

Purpose
- Provide persistent agent customization instructions derived from recent conversation and repository conventions. These rules guide automated responses, file edits, and collaboration style.

Extracted rules (from conversation)
- Always use the todo-list tool to plan and track multi-step work and update it during progress. (مثلاً: `manage_todo_list`).
- When referring to filenames or symbols in the workspace, wrap them in backticks (e.g., `knowledge_base/README.md`).
- Do not volunteer model name unless explicitly asked.
- Before any tool calls that edit files, present a short preamble (1–2 sentences) explaining what will be done.
- Provide concise progress updates after batches of 3–5 tool calls or when creating/editing >3 files.
- Keep final messages concise, professional, and bilingual (Arabic + English) when delivering substantive outputs.
- Follow repository path rules and create files under `/workspaces/odysseus/` using absolute paths for edits.

Scope and application
- Apply these rules to all agent customization, documentation, and repository-editing tasks unless the user specifies a different scope. Use discretion for ephemeral conversational guidance.

Hard vs Preference
- Hard rules (must-follow): todo-list usage, backtick filenames, no model name, tool-call preambles, progress cadence, use absolute paths for file edits.
- Preferences (follow when appropriate): bilingual outputs, concise style, formatting choices aligned with repository conventions.

Draft instruction (what agent enforces)
1. Start multi-step tasks by updating the todo list with clear items and statuses using `manage_todo_list`.
2. Before making any file edits or tool calls, post a one-line preamble describing the immediate actions.
3. Use `apply_patch` with an explanatory `explanation` field for all file changes; create files under `/workspaces/odysseus/` and reference created files with backtick-wrapped paths in messages.
4. After 3–5 tool calls or batches of edits, share a concise progress update (Arabic + English) and next steps.
5. Conclude each task with a short summary of changes, list of files added/modified, and a suggested next action.

Ambiguities & questions for confirmation
- Should bilingual output be applied to every single message or only to major deliverables (readmes, templates, reports)?
- Are there specific naming conventions beyond `YYYY-MM-DD_title_language` to enforce for non-document artifacts (e.g., PBIX, XLSX, STL)?

Examples (prompts to test behavior)
- "Create onboarding README for HR in Arabic and English and add to `knowledge_base/02_Human_Resources/`." — expected: update todo list, preamble, create file, progress update, summary.
- "Optimize Power BI template" — expected: ask scope (PBIX or dataset), update todos, preamble, then edit or add files.

Next customization suggestions
- Add a `AGENTS.md` describing agent roles and permissions.
- Add templates for commit messages and PR descriptions to match KB conventions.

Saved: 2026-06-04
