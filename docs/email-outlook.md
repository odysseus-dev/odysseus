# Outlook / Office 365 email accounts

Odysseus email accounts currently use IMAP and SMTP with username/password
authentication. That works for providers that still allow app passwords or
mailbox passwords for IMAP/SMTP.

Microsoft disables basic authentication for Outlook and Microsoft 365 in most
modern accounts and tenants. If you try to add an Outlook account with a normal
password, Microsoft may return errors such as:

- `IMAP: AUTHENTICATE failed`
- `SMTP: 535 5.7.139 Authentication unsuccessful, basic authentication is disabled`

This is expected. Odysseus does not support Microsoft OAuth or Graph Mail yet,
so Outlook / Office 365 accounts cannot currently be added through the password
form. Use another email provider with app-password support, or track the future
Microsoft Graph OAuth integration.
