---
layout: default
---

# Outlook / Office 365 email accounts

Microsoft disabled basic authentication for Outlook and Microsoft 365 in most
modern accounts and tenants, so a mailbox password no longer works for IMAP or
SMTP. If you try to add an Outlook account with a normal password, Microsoft
returns errors such as:

- `IMAP: AUTHENTICATE failed`
- `SMTP: 535 5.7.139 Authentication unsuccessful, basic authentication is disabled`

Odysseus connects these mailboxes with OAuth2 instead. Pick the
**Outlook / Office 365** provider preset when adding an email account and use
**Connect with Microsoft** — the password fields disappear, because the account
authenticates with a token rather than a password.

## 1. Register the application

You need a Microsoft app registration, which supplies the client ID and secret
Odysseus uses to run the flow.

1. Go to [entra.microsoft.com](https://entra.microsoft.com) →
   **App registrations** → **New registration**.
2. Give it a name and choose the supported account types that match your users:
   single tenant, multitenant, or multitenant plus personal Microsoft accounts.
3. Add a **Web** platform with the redirect URI:

   ```
   http://localhost:7000/api/email/oauth/microsoft/callback
   ```

   Replace the host and port for a hosted install. The value must match
   exactly, including the scheme.
4. Under **API permissions** → **Add a permission**:
   - **APIs my organization uses** → **Office 365 Exchange Online** →
     **Delegated permissions** → `IMAP.AccessAsUser.All` and `SMTP.Send`.
   - **Microsoft Graph** → **Delegated permissions** → `openid`, `email`,
     `offline_access`.

   The Exchange permissions are what IMAP and SMTP actually check. Without
   them the browser consent succeeds but mail access is still refused.
5. Under **Certificates & secrets** → **New client secret**, create a secret
   and copy its **Value** (not the secret ID).

If your tenant requires admin consent, an administrator must grant it for the
app before any mailbox can connect.

## 2. Configure Odysseus

Add the credentials to your `.env`:

```sh
MICROSOFT_OAUTH_CLIENT_ID=00000000-0000-0000-0000-000000000000
MICROSOFT_OAUTH_CLIENT_SECRET=your-client-secret-value

# The tenant the sign-in goes through. Defaults to `common`, which only
# works for a multi-tenant app registration: a single-tenant one (the usual
# choice inside a company) is refused there with AADSTS50194. A single-tenant
# app needs a tenant-specific value — the Directory (tenant) ID shown on the
# app registration's Overview page, or your verified domain. `organizations`
# is not tenant-specific, so it only suits a multi-tenant registration.
MICROSOFT_OAUTH_TENANT_ID=00000000-0000-0000-0000-000000000000

# Set explicitly for HTTPS, reverse-proxy, or hosted deployments. Must match
# a redirect URI registered on the app above.
MICROSOFT_OAUTH_REDIRECT_URI=https://your-domain.com/api/email/oauth/microsoft/callback
```

Restart Odysseus so it picks up the new environment.

## 3. Connect the mailbox

1. Open **Settings → Integrations → Email accounts** and add an account.
2. Choose the **Outlook / Office 365** provider preset. The IMAP and SMTP
   settings fill in automatically:

   | | Host | Port | Security |
   |---|---|---|---|
   | IMAP | `outlook.office365.com` | 993 | TLS |
   | SMTP | `smtp.office365.com` | 587 | STARTTLS |

3. Enter the mailbox address, then click **Connect with Microsoft** and sign
   in. Odysseus saves the account first so the flow has something to attach
   the tokens to.
4. After consent you land back in Settings with the account connected.

These hosts and ports are fixed for OAuth accounts. The token only authorizes
Microsoft's own mail servers, so Odysseus refuses to send it anywhere else.

Reconnecting must use the same mailbox the account is configured for — signing
in as a different user is rejected, so the saved IMAP/SMTP usernames can never
end up paired with another identity's credentials.

## Troubleshooting

When a connect fails, Odysseus shows a panel naming the provider's own error
code (and Microsoft's `AADSTS` number when there is one), with a **Copy code**
button. The same codes are written to the server log, so
`docker compose logs odysseus | grep OAuth` finds them after the fact:

```
Microsoft OAuth authorization was refused (code=access_denied aadsts=AADSTS65001)
```

Common codes:

| Code | Meaning |
|---|---|
| `AADSTS65001` | Nobody has consented to the app for the tenant. Use **Grant admin consent** under API permissions. |
| `AADSTS90094` | The app needs administrator approval — ask a tenant admin. |
| `AADSTS7000215` | Wrong client secret. Copy the secret's **Value**, not its Secret ID. |
| `AADSTS700016` | The app was not found in the tenant — check the client and tenant ids. |
| `AADSTS50011` | The redirect URI does not match the registered one, exactly. |
| `AADSTS50194` | The app is single-tenant, so the default `common` endpoint is refused. Set `MICROSOFT_OAUTH_TENANT_ID` to the Directory (tenant) ID. `organizations` does not help here — it is not tenant-specific either. |

**`AUTHENTICATE failed` right after connecting.** The Exchange delegated
permissions are usually missing, or IMAP/SMTP AUTH is disabled for the mailbox.
An administrator can enable it per mailbox with
`Set-CASMailbox -Identity user@contoso.com -ImapEnabled $true -SmtpClientAuthenticationDisabled $false`,
and tenant-wide under the Exchange admin center's authentication policies.

**The connect fails with `token_exchange_failed`.** The client secret is wrong
or expired, or the redirect URI does not match the one registered on the app.
Client secrets expire — check the registration if a previously working setup
stops.

**The connect fails with `identity_verification_failed`.** You signed in as a
different mailbox than the account is configured for. Sign in with the address
in the account's Email field, or correct that field first.

**Mail stops working after a while.** The refresh token was revoked — by a
password change, a conditional-access policy, or an administrator. Reconnect
the account.
