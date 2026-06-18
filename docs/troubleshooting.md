# Self-Host Troubleshooting Cookbook

The 30-second fixes that otherwise turn into 30-minute searches. Each entry is a
**Symptom -> Cause -> Fix** block for a trap that is specific to running Odysseus
yourself against self-hosted services.

For install, deployment, and the existing advanced notes (ChromaDB conflicts,
HTTPS via mkcert, optional dependencies), see
[setup.md -> Troubleshooting & Advanced Setup](setup.md#troubleshooting--advanced-setup).

## Local mail server (Dovecot) refuses login over a plaintext connection

**Symptom.** Adding a self-hosted mailbox (Dovecot/Postfix on your LAN) fails
with an authentication error such as
`Plaintext authentication disallowed on non-secure (SSL/TLS) connections`, even
though the username and password are correct.

**Cause.** Odysseus authenticates with a normal IMAP `LOGIN` / SMTP `AUTH`. If
the account is configured for IMAP port 143 without STARTTLS, or SMTP security
set to `none`, the credentials are sent in cleartext. Dovecot ships with
`disable_plaintext_auth = yes`, so it refuses `LOGIN` / `AUTH PLAIN` on any
connection that is not encrypted.

**Fix (recommended) — use an encrypted transport.** Enable TLS on the mail
server and match it in the Odysseus email account form:

- IMAP: port `993` (implicit SSL), or port `143` with the STARTTLS option enabled.
- SMTP: port `465` (SSL), or port `587` (STARTTLS). The SMTP security field
  accepts `ssl`, `starttls`, or `none`.

**Fix (trusted LAN only) — allow cleartext on the server.** If the mail server
is reachable only on a network you fully trust and you accept unencrypted
credentials, set in Dovecot's `conf.d/10-auth.conf`:

```
disable_plaintext_auth = no
auth_mechanisms = plain login
```

Then restart Dovecot. Do this only on a trusted private network — the password
crosses the wire unencrypted.

## CalDAV calendar (Radicale) connects but no events appear

**Symptom.** You add a Radicale (or other self-hosted CalDAV) calendar, the
connection succeeds, but no calendars or events show up.

**Cause.** Odysseus first tries CalDAV *principal discovery* (asking the server
to enumerate your calendars) and only falls back to treating the URL you gave as
a single calendar collection when discovery returns nothing. Radicale's
principal / `.well-known` discovery is easy to break behind a reverse proxy, or
if you point Odysseus at the server root instead of a calendar, so discovery
finds no calendars.

**Fix.** Point Odysseus directly at the **full collection URL**, including the
trailing slash, rather than the server base. Radicale collection URLs look like:

```
http://host:5232/<username>/<collection-id>/
```

You can copy the exact URL from Radicale's own web interface (it is shown for
each collection). With the full collection URL, Odysseus opens it directly and
sync works even when discovery fails. If you would rather rely on base-URL
discovery, make sure your reverse proxy forwards `/.well-known/caldav` to
Radicale.

## ntfy notifications are delayed on Android with a self-hosted server

**Symptom.** Reminders pushed through your self-hosted ntfy server arrive minutes
late, or only when you open the ntfy Android app — even though notifications from
`ntfy.sh` are instant.

**Cause.** This is an ntfy Android-app setting, not an Odysseus bug. Odysseus
just publishes to the topic you configured (`reminder_ntfy_topic`, default
`Reminders`) on the server set by `NTFY_BASE_URL`. "Instant delivery" on the
phone is a persistent foreground-service connection that the app only maintains
for servers you have explicitly added; for any non-`ntfy.sh` server it otherwise
falls back to periodic polling, which is what makes notifications feel delayed.

**Fix.** In the ntfy Android app:

1. Open Settings and add your self-hosted server (the same base URL as
   `NTFY_BASE_URL`, e.g. `http://100.x.y.z:8091`) as a/the default server.
2. Subscribe to your topic (e.g. `Reminders`).
3. Enable **Instant delivery** for that subscription.

Instant delivery keeps a live connection (shown as a persistent notification), so
messages arrive immediately instead of at the next poll. The phone must be able
to reach the server — over Tailscale, make sure it is on the tailnet. See the
[ntfy docs](https://docs.ntfy.sh/subscribe/phone/#instant-delivery) for details.

## "Copy" buttons do nothing over a plain-HTTP Tailscale or LAN URL

**Symptom.** Copy buttons (copy a code block, copy an admin token, copy a
diagnosis bundle) appear to do nothing when you open Odysseus over a plain-HTTP
address such as a Tailscale IP/MagicDNS name or a LAN IP. The same buttons work
on `localhost`.

**Cause.** The browser Clipboard API (`navigator.clipboard.writeText`) is only
available in a **secure context** — HTTPS or `localhost`. Over plain
`http://<tailscale-or-lan-ip>` the page is not a secure context, so the modern
clipboard call is unavailable. Odysseus falls back to the legacy
`document.execCommand('copy')` in most places, but that fallback is best-effort
and still fails in some browsers.

**Fix.** Serve Odysseus over HTTPS (or use `localhost`). The simplest self-host
path is the mkcert flow already documented in
[setup.md -> HTTPS + LAN/Tailscale exposure](setup.md#https--lantailscale-exposure):
generate a locally-trusted certificate for your LAN/Tailscale IP and run uvicorn
with `--ssl-certfile` / `--ssl-keyfile`. Alternatively use Tailscale's built-in
HTTPS certificates (`tailscale cert`) or a reverse proxy that terminates TLS.
Once the page loads over `https://`, the copy buttons use the native clipboard
API directly.
