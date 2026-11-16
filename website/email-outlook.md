---
layout: default
---

# Outlook / Office 365 Email Accounts

Odysseus supports Microsoft OAuth2 authentication using the OAuth 2.0 Device Authorization Grant (device-code flow) for IMAP and SMTP. This enables connection to Microsoft 365 (work or school) and personal Microsoft accounts (Outlook.com, Hotmail, Live) without basic authentication or app passwords.

> [!NOTE]
> This integration connects to Exchange Online mailboxes via OAuth2-authenticated IMAP and SMTP (XOAUTH2). It does not implement the Microsoft Graph Mail API.

---

## 1. Prerequisites and Environment Variables

Before adding an account in Odysseus, create an Azure / Microsoft Entra ID App Registration and set the following environment variables:

```bash
# Required: Application (client) ID from your Azure app registration
MICROSOFT_OAUTH_CLIENT_ID=00000000-0000-0000-0000-000000000000

# Optional: Azure AD tenant ID or alias (defaults to 'common')
# Use 'common' for multi-tenant work/school + personal accounts.
# Use 'organizations' for work/school accounts only.
# Use 'consumers' for personal Microsoft accounts only.
# Use your tenant GUID for single-tenant organization registrations.
MICROSOFT_OAUTH_TENANT=common
```

---

## 2. Microsoft Entra ID (Azure) App Registration

### A. Work and School Accounts (Microsoft 365)

1. Sign in to the [Microsoft Entra admin center](https://entra.microsoft.com/) or Azure Portal.
2. Navigate to **Identity** > **Applications** > **App registrations** and click **New registration**.
3. Configure the registration:
   - **Name**: e.g., `Odysseus Mail`
   - **Supported account types**:
     - For accounts within your company only: choose **Accounts in this organizational directory only (Single tenant)**. You must set `MICROSOFT_OAUTH_TENANT` to your Directory (tenant) ID.
     - For accounts across multiple organizations: choose **Accounts in any organizational directory (Multitenant)**.
     - For both organizational and personal accounts: choose **Accounts in any organizational directory and personal Microsoft accounts**.
   - **Redirect URI**: Leave blank (not required for device flow).
4. Click **Register**.
5. Enable Public Client flows:
   - In the left sidebar, select **Authentication**.
   - Under **Advanced settings** > **Allow public client flows**, toggle **Enable the following mobile and desktop flows** to **Yes**.
   - Click **Save**.
6. Configure Delegated API Permissions:
   - In the left sidebar, select **API permissions** > **Add a permission**.
   - Select **APIs my organization uses** or **Microsoft APIs** > **Office 365 Exchange Online**.
   - Select **Delegated permissions**.
   - Check the following permissions:
     - `IMAP.AccessAsUser.All` (Read and write access to mailboxes via IMAP)
     - `SMTP.Send` (Send emails from mailboxes via SMTP)
     - `offline_access` (Maintain access to data via refresh tokens)
   - Click **Add permissions**.
   - If required by your tenant policy, click **Grant admin consent for [organization]**.

### B. Personal Accounts (Outlook.com / Hotmail)

1. Sign in to the [Azure Portal](https://portal.azure.com/).
2. Navigate to **Microsoft Entra ID** > **App registrations** and click **New registration**.
3. Configure the registration:
   - **Name**: e.g., `Odysseus Mail (Personal)`
   - **Supported account types**: Choose **Personal Microsoft accounts only** (or **Accounts in any organizational directory and personal Microsoft accounts**).
   - **Redirect URI**: Leave blank.
4. Click **Register**.
5. In **Authentication** > **Advanced settings**, set **Allow public client flows** to **Yes** and click **Save**.
6. In `.env`, set:
   ```bash
   MICROSOFT_OAUTH_CLIENT_ID=<your-app-id>
   MICROSOFT_OAUTH_TENANT=consumers
   ```
   (or keep `MICROSOFT_OAUTH_TENANT=common`).

---

## 3. Adding an Account in Odysseus

1. Open Odysseus and navigate to **Settings** > **Integrations** (or **Settings** > **Email Accounts**).
2. Click **Add Account** (or select the **Outlook / Office 365** preset).
3. The preset pre-fills:
   - **IMAP Host**: `outlook.office365.com` (Port `993`, SSL)
   - **SMTP Host**: `smtp.office365.com` (Port `587`, STARTTLS)
   *(Personal Outlook.com accounts send through `smtp-mail.outlook.com` on port `587` with STARTTLS).*
4. Leave password fields blank.
5. Click **Sign in with Microsoft**.
6. A code and verification link will appear in the panel.
7. Click **Authorize with Microsoft** or open [microsoft.com/devicelogin](https://microsoft.com/devicelogin), enter the code, and approve the authorization prompt.
8. Odysseus will automatically complete the connection, populate your mailbox address, and store encrypted OAuth tokens.
9. Click **Test Connection** to verify both IMAP and SMTP XOAUTH2 authentication.
10. Click **Save**.

---

## 4. Reconnecting and Editing

- To reconnect an existing Microsoft account, click **Reconnect with Microsoft** from the account form.
- The signed-in identity must match the configured mailbox username (`imap_user` and `smtp_user`).
- If you change the username on an OAuth-connected account, stored OAuth credentials are automatically cleared to prevent associating credentials with an unverified mailbox. You can then re-authenticate with the new mailbox.

---

## 5. Troubleshooting and Common Errors

- **`AADSTS700016: Application was not found in the directory`**:
  Verify that `MICROSOFT_OAUTH_CLIENT_ID` matches your Azure application ID, and verify `MICROSOFT_OAUTH_TENANT`. Single-tenant applications must use their specific tenant GUID, not `common`.
- **`AADSTS7000218: The request body must contain the following parameter: 'client_assertion' or 'client_secret'`**:
  You must enable **Allow public client flows** in Azure App Registration > **Authentication** > **Advanced settings**.
- **`AADSTS50076` or `AADSTS50079: Due to a configuration change by your administrator, you must use multi-factor authentication`**:
  Device code flow supports MFA. Follow the prompts on the Microsoft device login page to complete your organization's MFA challenge.
- **`AADSTS65001: The user or administrator has not consented to use the application`**:
  An administrator must grant tenant consent for the delegated Exchange Online permissions, or user consent must be enabled in the tenant.
- **`Authentication unsuccessful, basic authentication is disabled`**:
  Ensure you are using **Sign in with Microsoft** and not entering a regular account password.
- **`Microsoft OAuth IMAP requires outlook.office365.com` / `Microsoft OAuth SMTP requires STARTTLS on port 587`**:
  Microsoft tokens are strictly pinned to authoritative Microsoft mail hosts (`outlook.office365.com`, `smtp.office365.com`, `smtp-mail.outlook.com`) and secure transports to ensure tokens are never sent to arbitrary or insecure servers.

---

## 6. Known Limitations

- **No Microsoft Graph Mail API**: This feature uses IMAP and SMTP with OAuth2 (XOAUTH2) tokens. It does not communicate with `graph.microsoft.com/v1.0/me/messages` or Microsoft Graph mail endpoints.
- **Calendars, Contacts, and Tasks**: Microsoft calendars, contacts, and tasks are not synchronized through this email OAuth flow.
