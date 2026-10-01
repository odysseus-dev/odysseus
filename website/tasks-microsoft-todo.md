---
layout: default
---

# Microsoft To Do task sync

Odysseus syncs your tasks with **Microsoft To Do** in both directions. A task
you tick off on your phone is archived here; a to-do you add here shows up in
To Do, Outlook's Tasks pane and the To Do app.

Tasks live in **Notes**, not in the Tasks tool. The Tasks tool schedules
automation — recurring prompts and actions — which has no counterpart in To Do.
A note of type **To-do** is the thing that matches a To Do task, field for
field, so that is what syncs.

This runs over **Microsoft Graph**, the same API the
[calendar sync](calendar-office365.md) uses.

## 1. Register the application

Task sync uses the **same app registration** as
[Outlook mail](email-outlook.md) and the calendar. If either already works you
only need to add one permission (step 3) and one redirect URI (step 2).

1. Go to [entra.microsoft.com](https://entra.microsoft.com) →
   **App registrations** → your app, or **New registration** if you have none.
2. Under **Authentication**, add a **Web** redirect URI:

   ```
   http://localhost:7000/api/notes/oauth/microsoft/callback
   ```

   Replace the host and port for a hosted install. The value must match
   exactly, including the scheme. This is a *third* URI — mail, calendar and
   tasks sign in separately and each returns to its own callback.
3. Under **API permissions** → **Add a permission** → **Microsoft Graph** →
   **Delegated permissions**, add `Tasks.ReadWrite` (plus `openid`, `email`
   and `offline_access` if they are not already there).

If your tenant requires admin consent, an administrator must grant it before
any account can connect.

### Why each one signs in separately

Microsoft issues access tokens per resource, and the v2.0 endpoint rejects a
single authorization request that mixes scopes from two resources. IMAP and
SMTP live on Exchange Online; calendars and tasks live on Microsoft Graph, and
Graph still issues them per scope set. So the connections are made one after
the other against the same app registration. Connecting one does not require
the others.

## 2. Configure Odysseus

Task sync reads the same credentials as mail and calendar, so if either already
works there is nothing to add:

```sh
MICROSOFT_OAUTH_CLIENT_ID=00000000-0000-0000-0000-000000000000
MICROSOFT_OAUTH_CLIENT_SECRET=the-secret-value
MICROSOFT_OAUTH_TENANT_ID=your-directory-tenant-id
```

Set the callback explicitly for HTTPS, reverse-proxy or hosted deployments. It
must match a redirect URI registered on the app:

```sh
MICROSOFT_TODO_REDIRECT_URI=https://your-domain.com/api/notes/oauth/microsoft/callback
```

Restart Odysseus after editing `.env` — the variables are read at startup.

## 3. Connect

1. Open **Settings → Integrations**.
2. **Add** → **Microsoft To Do**.
3. Click **Connect with Microsoft** and sign in.

Your Microsoft password never reaches Odysseus: sign-in happens on Microsoft's
own page and only a revocable token is stored, encrypted at rest.

Open **Notes** and press **Sync**. The button appears only once an account is
connected.

Removing the integration card disconnects the account. The notes stay — they
are your tasks — they just stop syncing. Nothing is deleted from Microsoft.

## What maps to what

| Microsoft To Do | Odysseus |
| --- | --- |
| Task | A note of type **To-do** |
| Steps (checklist items) | The note's checklist items |
| Task list | A tag on the note |
| Due date | The note's due date |
| Reminder time | The due date, with a time of day |
| Repeat | The note's repeat setting |
| Completed | Archived |
| Important | Pinned |
| Notes field | The note's body |

**Completing a task archives the note**, and archiving a note completes the
task. That is what "done" already means for a note here: it leaves the main
grid and moves to the Archive, where you can still find it.

**Each To Do list becomes a tag.** Tasks in your default list ("Tasks") are
untagged, since the tag would say nothing. A list named `Work Stuff` becomes
the tag `Work-Stuff` — tags cannot contain spaces. Tags you add yourself
survive a sync; only the list tag is managed for you.

**A task cannot change lists.** The Graph API has no move operation, so the
list is chosen once, when the note first reaches To Do: the first tag that
names an existing list wins, otherwise the default list. Retagging a note
afterwards does not move the task. A tag that matches no list never creates
one.

## How the sync behaves

**Only to-dos sync.** Notes, drawings and goals stay local. Writing every
scratch note into your task list is not something a sync should do on your
behalf.

**Both directions, on every sync.** Local edits are pushed first, then remote
changes are pulled, so a task you just changed here is not overwritten by the
older copy still upstream.

**Write-through is immediate.** Adding, editing, ticking off or deleting a
to-do pushes it to Microsoft right away. If Microsoft is unreachable the local
change still stands and is marked pending; the next sync retries it. A local
change is never silently lost because the network was down.

**The first sync uploads what you already have.** Every existing to-do with no
counterpart upstream is created in To Do. If you would rather start clean,
archive or delete the ones you do not want first.

**Old completed tasks are left upstream.** A To Do list keeps everything you
have ever ticked off. Only tasks completed in the last 30 days are pulled, so
your Archive does not fill up with years of history. They are not deleted from
To Do, just not imported.

**Deleting here deletes upstream.** A deleted task is remembered until
Microsoft confirms the delete, so a failure mid-way does not resurrect it on
the next pull.

**Turning a to-do into a plain note removes it from To Do.** It is no longer a
task, so it does not stay in your task list.

**The assistant follows the same path.** A task the agent adds or ticks off
for you is written back to To Do the same way one you touched yourself is.

## Troubleshooting

Failures show the Microsoft error code directly in the UI, with a **Copy code**
button. The code is the fastest route to the fix:

| Code | Meaning | Fix |
| --- | --- | --- |
| `AADSTS50194` | The app is single-tenant but sign-in went through the shared `/common` endpoint | Set `MICROSOFT_OAUTH_TENANT_ID` to the Directory (tenant) ID from the app's Overview page, then restart |
| `AADSTS7000215` | Invalid client secret | Generate a new secret and copy its **Value** column, not its **Secret ID** |
| `AADSTS50011` | Redirect URI mismatch | Register `/api/notes/oauth/microsoft/callback` on the app — the mail and calendar callbacks are not enough |
| `AADSTS65001` | Nobody has consented to the app yet | An administrator grants consent under **API permissions** |
| `AADSTS90094` | The app needs admin approval | Ask a tenant administrator to grant consent |
| `AADSTS700016` | Application not found in the tenant | Check `MICROSOFT_OAUTH_CLIENT_ID` and `MICROSOFT_OAUTH_TENANT_ID` |
| `invalid_scope` | `Tasks.ReadWrite` is not registered on the app | Add it under **API permissions** → **Microsoft Graph** → **Delegated** |

**"Connect with Microsoft" is greyed out.** `MICROSOFT_OAUTH_CLIENT_ID` is not
set, or Odysseus has not been restarted since it was added to `.env`.

**No Sync button in Notes.** No account is connected. The button stays hidden
until one is, so it can never report "nothing connected".

**Connected, but nothing appears.** Check that the account you signed in with
is the one holding the tasks — To Do is per-mailbox, and a tenant with several
accounts is easy to mix up.

**"Sign-in expired — reconnect Microsoft To Do".** The stored refresh token was
revoked or expired; a tenant policy change or a password reset does this.
Remove the integration card and connect again.
