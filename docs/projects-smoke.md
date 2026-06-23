# Projects — Manual Smoke Test

Run these steps after enabling `FEATURES.projects_enabled = true` in
`data/features.json`. Each step is something to **verify visually** in
the UI, with a console / DB check where useful.

## Setup
1. Set `"projects_enabled": true` in `data/features.json`. Restart the app.
2. Open the UI. Confirm the **Projects** tab is visible in the top nav.

## Memory modes
3. **Isolated**: create "Notes A" (memory mode: Isolated). Send a chat that
   includes "I prefer dark mode." Confirm the project's brain hover shows
   the memory. Switch to main Chats and confirm the same memory is NOT
   there.
4. **Shared**: create "Notes B" (memory mode: Shared). Send the same chat.
   Confirm the brain hover shows the memory AND it appears in the main
   brain (Shared aliases global).
5. **Inherit**: first populate the main brain with at least one memory via
   the main Chats tab. Then create "Notes C" (memory mode: Inherit).
   Confirm the Settings → Memory explainer shows the snapshot count.

## Resources
6. Upload a small `.txt` and a `.pdf`. Confirm both appear in the
   Resources sub-tab with chunk counts > 0.
7. Send a chat that asks about content in the resource. Confirm the
   reply cites the source resource.
8. Remove a resource. Confirm the file is gone AND the resource no
   longer appears in retrieval.

## Settings
9. Edit custom prompt + custom instructions. Save. Confirm the next chat
   in the project reflects the new prompt.
10. Change Prompt → Override. Confirm the main-brain system prompt is no
    longer prepended (check the chat pipeline debug log).

## Delete
11. Delete "Notes A". Confirm the confirm-name modal appears.
12. After delete, confirm the project's directory is gone (`ls
    $DATA_DIR/projects/<owner>/`) AND no sessions with that `project_id`
    remain: `sqlite3 data/app.db "SELECT COUNT(*) FROM sessions WHERE
    project_id IS NOT NULL;"` should drop.
13. Confirm the main brain is unchanged.

## Rollout flag
14. Set `"projects_enabled": false`. Restart. Confirm the Projects tab
    disappears and `/api/projects` returns 404.
