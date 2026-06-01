# Troubleshooting — the 30-second fixes

Self-hosting Odysseus means wiring up email, push, calendars, and a reverse
proxy yourself. Most of the friction is a handful of small config traps that
cost 30 minutes to find and 30 seconds to fix. Here they are.

> Most setup lives in the app (`/setup` or **Settings → Integrations**). A few
> deployment defaults live in `.env` — see [Configuration](../README.md#configuration).

## "Copy" buttons do nothing (plain-HTTP / Tailscale IP)

**Symptom:** Copy-to-clipboard (code blocks, message copy, transcripts) silently
fails when you open Odysseus at `http://<host>:7000` or a Tailscale IP like
`http://100.x.y.z:7000`.

**Cause:** Browsers only expose `navigator.clipboard` in a **secure context** —
HTTPS, or `localhost`. Over plain HTTP to an IP/hostname it isn't available, and
the legacy `execCommand('copy')` fallback is blocked on most mobile browsers.

**Fix:** Reach Odysseus over **HTTPS** through a reverse proxy
(see [Putting it behind HTTPS](../README.md#putting-it-behind-https)).
`localhost` is also a secure context, so desktop-local use is fine — it's remote
plain-HTTP that breaks. The same secure-context rule is also why installing the
mobile PWA requires HTTPS.

## Email won't connect to a local mail stack (Dovecot, cleartext)

**Symptom:** IMAP/SMTP fails against a local mailserver (e.g. Dovecot /
docker-mailserver) that has no TLS certificate, even though the same credentials
work against a public provider.

**Cause:** The defaults assume TLS — IMAP on **993 (implicit SSL)** or STARTTLS,
SMTP on **465 (implicit SSL)**. A cleartext local server offers none of that, so
the TLS handshake or STARTTLS upgrade fails before login.

**Fix:** In **Settings → Integrations → Email**, for a no-TLS local server:
- **IMAP:** port **143**, turn **SSL off** and **STARTTLS off**.
- **SMTP:** port **25** (or your server's plain port), **SSL off**; enable
  STARTTLS only if the server advertises it (typically port 587).
- On the server side, allow it — for Dovecot, `disable_plaintext_auth = no`.
  Only run cleartext over a trusted LAN / loopback, never the public internet.

The bundled MCP email server reads the same toggles from `.env`: `IMAP_SSL`,
`IMAP_STARTTLS`, `IMAP_PORT`, `SMTP_SSL`, `SMTP_STARTTLS`, `SMTP_PORT`.

## ntfy: green checkmark, but no notification on my phone

Two separate traps live here.

**Trap 1 — a path in the base URL.** If you set the ntfy base URL to something
like `http://host:8091/odysseus`, the topic gets appended and Odysseus ends up
publishing to `/odysseus/<topic>`, which ntfy returns 404 for — ntfy only serves
from the **root**. **Fix:** set the base URL to scheme + host only:
`http://host:8091`. The topic is configured separately (default `reminders`).

**Trap 2 — Android "Instant Delivery" on a self-hosted server.** The ntfy.sh
Android app gets instant push via Firebase. Your **self-hosted** server isn't on
Firebase, so the app has to hold its own persistent connection. **Fix:** in the
ntfy Android app → **Settings → add your server** as the default/user server,
subscribe to your topic **on that server**, turn on **Instant delivery** for the
subscription (it keeps a foreground-service WebSocket open), and **exempt the app
from battery optimization** so Android doesn't kill it. Without instant delivery
the app only polls occasionally, so reminders arrive late or not at all.

**Reachability:** the bundled ntfy binds to `127.0.0.1:8091` by default, so other
devices can't reach it. To expose it over Tailscale, set `NTFY_BIND` to your
host's Tailscale IP and `NTFY_BASE_URL=http://100.x.y.z:8091` in `.env`.

## CalDAV / CardDAV (Radicale): "Not found" or no calendars appear

**Symptom:** The calendar connects but nothing syncs, or **Test connection**
returns *"Not found — check the URL path"*.

**Cause:** Radicale doesn't reliably enumerate calendars from the server root the
way auto-discovery expects, so pointing the URL at `http://host:5232/` finds
nothing.

**Fix:** Point the CalDAV URL at the **specific collection**, not the root:

```
http://host:5232/USERNAME/COLLECTION_ID/
```

Copy that exact path (trailing slash included) from Radicale's web UI. Odysseus
falls back to treating the URL as a direct calendar, so the full collection URL
works even when principal discovery doesn't. CardDAV contacts follow the same
rule with a contacts-collection URL. For reference, the connection test maps
status codes to plain messages: *"Auth failed"* = wrong username/password,
*"Forbidden"* = that user can't access the collection, *"Not found"* = wrong path.

## The web UI won't load on port 7000 (macOS)

**Symptom:** Nothing on `http://localhost:7000`, or a different app answers.

**Cause:** macOS **AirPlay Receiver** listens on port 7000.

**Fix:** set `APP_PORT` in `.env` to a free port (e.g. `7001`) and restart, or
turn off AirPlay Receiver in **System Settings → General → AirDrop & Handoff**.

## Services run but other devices can't reach them

ChromaDB, SearXNG, and ntfy bind to `127.0.0.1` by default (safe out of the box).
If a feature works on the host but not from your phone or another machine, set
the matching `*_BIND` variable (e.g. `NTFY_BIND`, `CHROMADB_BIND`) to the
interface you want, and update the matching `*_BASE_URL`. Prefer a Tailscale IP
over `0.0.0.0` on shared networks.
