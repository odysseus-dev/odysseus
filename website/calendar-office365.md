---
layout: default
---

# Office 365 calendar sync

Odysseus syncs its internal calendar with an Office 365 or Outlook.com calendar
in **both directions**: events created or edited in Outlook appear in Odysseus,
and events you create in Odysseus are written back to your Microsoft calendar.

This runs over **Microsoft Graph**, not CalDAV. Exchange Online does not
support CalDAV at all, so the CalDAV integration cannot reach a Microsoft
mailbox — the two live side by side and you can use both at once.

## 1. Register the application

Calendar sync uses the **same app registration** as
[Outlook mail](email-outlook.md). If you already set that up, you only need to
add one permission (step 3 below) and one redirect URI (step 2).

1. Go to [entra.microsoft.com](https://entra.microsoft.com) →
   **App registrations** → your app, or **New registration** if you have none.
2. Under **Authentication**, add a **Web** redirect URI:

   ```
   http://localhost:7000/api/calendar/oauth/microsoft/callback
   ```

   Replace the host and port for a hosted install. The value must match
   exactly, including the scheme. This is a *second* URI — mail and calendar
   sign in separately and each returns to its own callback.
3. Under **API permissions** → **Add a permission** → **Microsoft Graph** →
   **Delegated permissions**, add `Calendars.ReadWrite` (plus `openid`,
   `email` and `offline_access` if they are not already there).

If your tenant requires admin consent, an administrator must grant it before
any calendar can connect.

### Why mail and calendar sign in separately

Microsoft issues access tokens per resource, and the v2.0 endpoint rejects a
single authorization request that mixes scopes from two resources. IMAP and
SMTP live on Exchange Online; calendars live on Microsoft Graph. So the two
connections are made one after the other, against the same app registration.
Connecting one does not require the other — you can sync only your calendar,
only your mail, or both.

## 2. Configure Odysseus

Calendar sync reads the same credentials as mail, so if Outlook mail already
works there is nothing to add:

```sh
MICROSOFT_OAUTH_CLIENT_ID=00000000-0000-0000-0000-000000000000
MICROSOFT_OAUTH_CLIENT_SECRET=the-secret-value
MICROSOFT_OAUTH_TENANT_ID=your-directory-tenant-id
```

Set the callback explicitly for HTTPS, reverse-proxy or hosted deployments. It
must match a redirect URI registered on the app:

```sh
MICROSOFT_CALENDAR_REDIRECT_URI=https://your-domain.com/api/calendar/oauth/microsoft/callback
```

Restart Odysseus after editing `.env` — the variables are read at startup.

## 3. Connect the calendar

1. Open **Settings → Integrations**.
2. **Add** → **Microsoft 365 Calendar**.
3. Click **Connect with Microsoft** and sign in.

Every calendar in the mailbox shows up as its own calendar in Odysseus, keeping
its name. Your Microsoft password never reaches Odysseus: sign-in happens on
Microsoft's own page and only a revocable token is stored, encrypted at rest.

Remove the integration card to disconnect. That deletes the stored tokens and
the synced calendars from Odysseus; nothing is deleted from Microsoft.

## How the sync behaves

**Both directions, on every sync.** Local edits are pushed first, then remote
changes are pulled, so an event you just changed in Odysseus is not overwritten
by the older copy still upstream.

**Write-through is immediate.** Creating, editing or deleting an event in
Odysseus pushes it to Microsoft right away. If Microsoft is unreachable the
local change still stands and is marked pending; the next sync retries it. A
local change is never silently lost because the network was down.

**The sync window** covers 90 days back and 365 days forward. Events outside it
are left alone in both directions.

**Recurring events** are pulled as individual occurrences, so editing one
occurrence in Odysseus changes exactly that occurrence upstream. A recurring
event *created* in Odysseus is translated into a Microsoft recurrence pattern;
daily, weekly, monthly and yearly rules are supported, with intervals, weekday
selections and an end date or occurrence count. A rule that cannot be expressed
as a Microsoft pattern is pushed as a single event rather than guessing at the
wrong series.

**Deleting in Odysseus deletes upstream.** A deleted event is remembered until
Microsoft confirms the delete, so a failure mid-way does not resurrect it on the
next pull.

## Troubleshooting

Failures show the Microsoft error code directly in the UI, with a **Copy code**
button. The code is the fastest route to the fix:

| Code | Meaning | Fix |
| --- | --- | --- |
| `AADSTS50194` | The app is single-tenant but sign-in went through the shared `/common` endpoint | Set `MICROSOFT_OAUTH_TENANT_ID` to the Directory (tenant) ID from the app's Overview page, then restart |
| `AADSTS7000215` | Invalid client secret | Generate a new secret and copy its **Value** column, not its **Secret ID** |
| `AADSTS50011` | Redirect URI mismatch | Register `/api/calendar/oauth/microsoft/callback` on the app — the mail callback alone is not enough |
| `AADSTS65001` | Nobody has consented to the app yet | An administrator grants consent under **API permissions** |
| `AADSTS90094` | The app needs admin approval | Ask a tenant administrator to grant consent |
| `AADSTS700016` | Application not found in the tenant | Check `MICROSOFT_OAUTH_CLIENT_ID` and `MICROSOFT_OAUTH_TENANT_ID` |
| `invalid_scope` | `Calendars.ReadWrite` is not registered on the app | Add it under **API permissions** → **Microsoft Graph** → **Delegated** |

**"Connect with Microsoft" is greyed out.** `MICROSOFT_OAUTH_CLIENT_ID` is not
set, or Odysseus has not been restarted since it was added to `.env`.

**Connected, but no events appear.** Check that the events fall inside the sync
window, and that the mailbox has calendars the signed-in account can read.

**"Sign-in expired — reconnect the calendar".** The stored refresh token was
revoked or expired — a tenant policy change or a password reset does this.
Remove the integration card and connect again.
