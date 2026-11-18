# Notes markdown migration

A note used to keep its checklist in a JSON `items` column and one image in
`image_url`, next to a free-text `content`. It is now a single markdown
document: task lines (`- [ ]` / `- [x]`) and images (`![](/api/upload/<id>)`)
live in `content`, and the API derives `items`, `note_type` and a drawing's
`image_url` from it. The contract is in
[`specs/calendar-tasks-notes.md`](../specs/calendar-tasks-notes.md), "Note
Content Model". This page covers what the boot migration changes in an existing
database, how to undo it, and what keeps working while old writers still send
the legacy fields.

## When it runs

`core/database.py:init_db()` runs at import, so every app start (and every CLI
or test that imports `core.database`) runs these steps, after
`_migrate_add_notes_sort_order()`:

1. `_migrate_unify_notes()` folds `items` into `content`.
2. `_migrate_fold_image_url()` folds `image_url` into `content`.
3. `_migrate_note_legacy_fields_cleanup()` installs the trigger that deletes a
   note's backup row with the note, and drops backup rows already orphaned
   (see below).

They open the SQLite file named by `DATABASE_URL` directly, resolved through
`_sqlite_db_path()`, so a driver-qualified URL such as `sqlite+pysqlite:///`
migrates too. With a non-SQLite `DATABASE_URL`, or before the file exists, they
do nothing; rows keep their legacy columns and every reader folds both `items`
and `image_url` on the fly (see "Idempotency and per-row isolation").

## What gets folded

**`items`.** Every row whose `items` is not `NULL`, `''` or `'[]'` is decoded
and merged with `merge_items_into_content()`:

- an item counts as already present when an unused task line outside fenced
  code has the same text and the same done state; each such line matches one
  item, so two identical items, or an item ticked where the content's copy is
  not, both survive as their own lines;
- every other item becomes a task line (`- [x] text` when it was `done` or
  `checked`), with its `indent` coerced to 0..3, inserted after the last
  existing task line, or appended as a new block after a blank line;
- items with empty text are dropped. A line break inside an item's text becomes
  a space so it stays one line, and leading and trailing spaces are trimmed;
  nothing else in the text changes;
- existing content is never rewritten: its bytes and line endings (CRLF
  included) stay as they were, and a row can only gain lines. When content ends
  inside an unclosed code fence, the fence is closed before the new lines, so
  the tasks are not swallowed by the code block.

Task-looking lines inside a fenced code block are code, so a note whose only
`- [ ]` lines are a fenced sample still gets its real items. Then `items` is set
to `NULL`.

**`image_url`.** Every row with a non-blank `image_url` gets `![](image_url)`
appended unless content already references that URL (after closing an open
fence, as above), and `image_url` is set to `NULL`. `gallery_id` is left alone, so a drawing keeps its gallery link.

**`note_type`** is not rewritten. It is derived on read: `checklist` for a note
that is only task lines, `note` otherwise, while `goal` and `draw` keep their
stored value.

## Backup table and rollback

Before a row is first rewritten, its original `content`, `items`, `image_url`
and `note_type` are copied into `note_legacy_fields`, in the same transaction
as the rewrite:

```sql
CREATE TABLE IF NOT EXISTS note_legacy_fields (
  note_id TEXT PRIMARY KEY, content TEXT, items TEXT, image_url TEXT,
  note_type TEXT, migrated_at TEXT NOT NULL)
```

The insert is `INSERT OR IGNORE`, so a row is written once and never
overwritten: when both steps touch the same note, the table keeps the state from
before the first one.

Rolling back is plain SQL against `data/app.db` (also documented above
`NOTE_LEGACY_FIELDS_DDL` in `core/database.py`):

```sql
UPDATE notes SET
  content   = (SELECT content   FROM note_legacy_fields WHERE note_id = notes.id),
  items     = (SELECT items     FROM note_legacy_fields WHERE note_id = notes.id),
  image_url = (SELECT image_url FROM note_legacy_fields WHERE note_id = notes.id),
  note_type = (SELECT note_type FROM note_legacy_fields WHERE note_id = notes.id)
WHERE id IN (SELECT note_id FROM note_legacy_fields);
DELETE FROM note_legacy_fields;
DROP TRIGGER IF EXISTS note_legacy_fields_follow_note_delete;
```

- It restores the four columns as they were at migration time. Edits made to
  those notes since are lost: it is a rollback, not a merge.
- It only covers notes that existed at migration time. A note created
  afterwards has no backup row and stays as current code wrote it, so code from
  before the migration shows its checklist as literal `- [ ]` lines and finds no
  `image_url` for a drawing created since.
- Stop the app first, and take a copy with
  `sqlite3 data/app.db ".backup data/app.db.pre-rollback"`.
- Run it together with code from before the migration. Started on current
  code, the next boot finds `items` set again and folds the rows a second time.
- `tests/test_notes_unify_migration.py` runs this exact SQL and checks that the
  rows come back byte for byte.
- A deleted note is gone for good. The trigger
  `note_legacy_fields_follow_note_delete` (`AFTER DELETE ON notes`) removes its
  backup row on every delete path: the route, the agent tool, calendar reminder
  cleanup, the CLI and the admin wipe. So the rollback cannot
  bring a deleted note back.

## Idempotency and per-row isolation

- A converted row has `items` (or `image_url`) `NULL`, so it is never selected
  again; running the migration twice changes nothing the second time.
- Each row commits or rolls back on its own. A row whose `items` is not a JSON
  array is skipped with a `notes unify migration: skipped note <id>` warning,
  keeps its columns, and is retried on the next boot. It does not stop the rows
  after it.
- A skipped row still reads correctly: `effective_content()` folds a readable
  `items` and any `image_url` into the content it returns, in the REST payload,
  `manage_notes` list and view, selected-notes chat context, the daily brief,
  both reminder builders and the CLI. The first write through the routes or the
  agent tool folds them into the row (`fold_legacy_columns`). Unreadable
  `items` are left in place for the migration to retry, by the tool as well as
  the routes.

## What keeps working

| Surface | Behaviour after the migration |
| --- | --- |
| REST `items` / `image_url` | `GET` still returns `items`, `note_type` and a drawing's `image_url`, derived from content; other notes read `image_url: null`. `POST`/`PUT` with `items` rewrite the task lines in place. Items lose their `id` and any other key, a blank item removes its line, and `items: []` reads back as `null`. `image_url` replaces the note's legacy image line, or adds it, and `""` removes it. On `PUT` (and the `/api/codex/todos` `update` action), an explicit `null` on `content`, `items` or `image_url` is a 422 that saves nothing; leave the key out instead. |
| REST `content` | A `PUT` with `content` and no `items` keeps the existing task lines when the new text has none; `items: []` with it clears them. |
| Checklist routes | `POST /api/notes/{id}/items/{index}/toggle` flips the line in content, by index or by the `text` the client sends; `POST /api/notes/{id}/items` appends one. |
| `scripts/odysseus-notes` | `list`/`show`/`search` print derived `items` and `note_type`; `create --item TEXT` (repeatable) writes task lines. |
| Calendar writers | Event reminders from `routes/calendar_routes.py` and the `manage_calendar` tool write a task line through `items_to_markdown()` instead of `items` JSON. |
| Reminders | `/api/notes/fire-reminder` and the scheduler's note ping share `reminder_body()`: prose, then `Pending (N):` and the open tasks. |
| Daily brief | Lists open tasks from any note with task lines (it used to read only `note_type == "checklist"`). |
| Selected-notes chat context | Sends the content verbatim plus an `Items:` block restating the tasks. |
| `manage_notes` | Takes `checklist_items` as before (`[]` clears); `update` with `content` alone keeps the task lines; `get`/`view` print `[x] i: text` with the index `toggle_item` takes. |
| Backups | `scripts/odysseus-backup snapshot` copies `app.db` whole, including `note_legacy_fields`. The in-app JSON export (`/api/export`) never included notes and still does not. |
