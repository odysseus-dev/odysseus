# Notes markdown migration

A note used to keep its checklist in a JSON `items` column and one image in
`image_url`, next to a free-text `content`. It is now a single markdown
document: task lines (`- [ ]` / `- [x]`) and images (`![](/api/upload/<id>)`)
live in `content`. The API derives `items` and `note_type`; the legacy
`image_url` column identifies the attachment for older clients and drawings. The contract is in
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
4. `_migrate_note_shares_cascade()` rebuilds a `note_shares` table that lacks
   `ON DELETE CASCADE` (see below).

They open the SQLite file named by `DATABASE_URL` directly, resolved through
`_sqlite_db_path()`, so a driver-qualified URL such as `sqlite+pysqlite:///`
migrates too. With a non-SQLite `DATABASE_URL`, or before the file exists, they
do nothing; rows keep their legacy columns and every reader folds both `items`
and `image_url` on the fly (see "Idempotency and per-row isolation").
The SQL rollback below applies to rows backed up by the SQLite boot migration.
Keep a database snapshot before allowing writes on a different backend.

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
appended unless content already has a standalone image of that URL outside
fenced code. A standalone image can have alt text and a title. Inline images
remain part of the document. The URL stays in `image_url` so a legacy prose
edit keeps the attachment. `gallery_id` is left alone, so a drawing keeps its
gallery link.

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

- It restores the four columns as they were at migration time, discarding later edits to those fields on backed-up notes. Other columns retain their current values.
- It only covers notes that existed at migration time. A note created
  afterwards has no backup row and keeps its Markdown. Older code shows its
  checklist as literal `- [ ]` lines. Legacy attachment URLs and gallery links
  still exist for new drawings created through the canvas.
- Stop the app first, and take a copy with
  `sqlite3 data/app.db ".backup data/app.db.pre-rollback"`.
- Run it together with code from before the migration. Started on current
  code, the next boot finds `items` set again and folds the rows a second time.
- `tests/test_notes_unify_migration.py` runs this exact SQL and checks that the
  rows come back byte for byte.
- A deleted note is gone for good. The trigger
  `note_legacy_fields_follow_note_delete` (`AFTER DELETE ON notes`) removes its
  backup row on every delete path: the route, the agent tool, calendar reminder
  cleanup, the CLI, the admin wipe and account deletion. So the rollback cannot
  bring a deleted note back.

## Idempotency and per-row isolation

- Converted checklist rows have `items` set to `NULL`. The image step checks rows with a legacy URL and skips those that already reference it. Repeated startup leaves both body and backup unchanged.
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

Installations that ran a preview which cleared `image_url` already have the
image Markdown. They do not automatically recover the legacy attachment
pointer. The rollback backup can predate subsequent edits, so reapplying its
URL could restore a deliberately removed image. On those installations,
review the affected notes before using the legacy attachment or canvas controls.

## `note_shares` cascade rebuild

Note sharing adds the `note_shares` table, created by `create_all`. Databases
that ran test builds of an earlier sharing design already have a `note_shares`
table with a plain `REFERENCES notes (id)` and a differently named unique
index, and `create_all` leaves an existing table alone. On those databases,
deleting a shared note raised an `IntegrityError`.

`_migrate_note_shares_cascade()` reads `PRAGMA foreign_key_list(note_shares)`.
When the `note_id` foreign key has no `CASCADE`, it recreates the table from the
current `NoteShare` model (columns, foreign key and indexes, including
`ux_note_shares_note_user`), copies the rows that still point at an existing
note, drops the old table and renames the new one, all in one transaction.

- A missing or NULL `id`, timestamp or `permission` gets a default (`view` for
  the permission, the least access), and an unknown permission becomes `view`.
  Usernames are trimmed and lowercased, and duplicate grants for one account
  collapse into one, keeping `edit` over `view`.
- Only rows that cannot be kept are dropped: no note, no username, or a
  duplicate. The log counts each reason, and `PRAGMA foreign_key_check` runs
  before the commit.
- A table that already cascades, or a database with no `note_shares` table, is
  left alone, so the step is idempotent.
- A failure rolls the transaction back and logs
  `note_shares cascade migration skipped: ...`; the old table stays as it was.
- `tests/test_note_shares_cascade_migration.py` seeds the legacy `CREATE TABLE`
  and checks the rebuild against a fresh `create_all`.

## What keeps working

| Surface | Behaviour after the migration |
| --- | --- |
| REST legacy fields | `items` returns derived indices, text, completion and nesting. Legacy item IDs and other properties are dropped. `note_type` is derived except for goals and drawings. The legacy `image_url` remains available for all note types. A null clears the corresponding part of the body on PUT. |
| REST `content` | A prose-only PUT keeps the legacy attachment and, if it contains no task lines, keeps the checklist. Send `replace_content: true` (or `base_updated_at`) with the full document to remove omitted images or tasks. |
| Concurrency tokens | Every note payload carries `body_revision` (a hash of the body fields) and `updated_at`. A PUT that writes the body (`content`, `items`, `image_url`, `note_type`) and the checklist toggle must send `body_revision` or `base_updated_at`; a missing or stale token is a 409 whose `detail` is `{"message", "note"}` with the current note. `body_revision` ignores metadata changes (pin, colour, order); `base_updated_at` asks for exactly that version. |
| Checklist routes | `POST /api/notes/{id}/items/{index}/toggle` flips the line in content, by index or by the `text` the client sends, and needs a token (above). `POST /api/notes/{id}/items` appends one and needs none: a lost race re-reads the committed body and appends again. |
| `scripts/odysseus-notes` | `list`/`show`/`search` print derived `items` and `note_type`; `create --item TEXT` (repeatable) writes task lines. |
| Calendar writers | Event reminders from `routes/calendar_routes.py` and the `manage_calendar` tool write a task line through `items_to_markdown()` instead of `items` JSON. |
| Reminders | `/api/notes/fire-reminder` and the scheduler's note ping share `reminder_body()`: prose, then `Pending (N):` and the open tasks, as plain text with image markdown removed. The browser's local notification follows the same rules. |
| Daily brief | Lists open tasks from any note with task lines (it used to read only `note_type == "checklist"`), leaving out the calendar's own reminder notes. |
| Selected-notes chat context | Sends the content verbatim plus an `Items:` block restating the tasks. |
| `manage_notes` | Takes `checklist_items` as before (`[]` clears); `update` with `content` alone keeps the task lines; `get`/`view` print `[x] i: text` with the index and body revision `toggle_item` takes. |
| Backups | `scripts/odysseus-backup snapshot` copies `app.db` whole, including `note_legacy_fields` and `note_shares`. The in-app JSON export (`/api/export`) never included notes and still does not. |
