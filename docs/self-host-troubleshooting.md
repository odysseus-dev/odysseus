# Self-Host Troubleshooting

The weird 30-second fixes that otherwise turn into 30-minute searches. These are
integration traps — mostly things that work on `localhost` but break the moment
you reach Odysseus from another device (LAN, Tailscale, phone) or wire it to a
self-hosted service.

For install, GPU, ChromaDB, and Outlook issues, see
[setup.md → Troubleshooting & Advanced Setup](setup.md#troubleshooting--advanced-setup).

---

## "Copy" buttons do nothing over a LAN or Tailscale URL

**Symptom:** copy buttons (chat messages, code blocks, share links) silently do
nothing when you open Odysseus at `http://<lan-ip>:7000` or
`http://<tailscale-ip>:7860`, but work fine at `localhost`.

**Why:** browsers expose the Clipboard API (`navigator.clipboard`) **only in a
secure context** — HTTPS, or a `localhost`/`127.0.0.1` origin. Over plain HTTP to
any other host the API is `undefined`, so the button has nothing to call.

**Fix** (pick one):
- Serve Odysseus over **HTTPS** with a locally-trusted cert — see
  [setup.md → HTTPS + LAN/Tailscale exposure](setup.md#https--lantailscale-exposure).
  This is the real fix and also unblocks other secure-context features.
- Open it on the host itself via `http://localhost:<port>` / `http://127.0.0.1:<port>`.
- Dev/throwaway only: in Chromium, add the origin under
  `chrome://flags/#unsafely-treat-insecure-origin-as-secure` and restart the browser.

---

## Phone never gets ntfy reminders (or they arrive late, not instantly)

**Symptom:** reminders fire inside Odysseus but your phone gets nothing — or they
only show up when you open the ntfy app.

**Why #1 — unreachable base URL.** The bundled ntfy binds to `127.0.0.1:8091` and
defaults `NTFY_BASE_URL=http://localhost:8091`. Your phone cannot reach
`localhost`, so the subscription points at nothing.

**Fix:** set the bind + base URL to an address the phone can reach (LAN or
Tailscale IP) in `.env`, then recreate the ntfy container:

```bash
NTFY_BIND=100.x.y.z
NTFY_BASE_URL=http://100.x.y.z:8091
```

`.env.example` documents this, and Odysseus surfaces the same hint when a reminder
fails to deliver. Subscribe the phone to that **server URL + topic** (the topic is
in **Settings → reminder ntfy topic**, default `reminders`).

**Why #2 — not actually "instant".** The Android ntfy app keeps a live connection
automatically only for `ntfy.sh`. For a self-hosted server it polls periodically
unless you enable **Instant Delivery** on that subscription (which needs a
battery-optimization exemption for the ntfy app).

---

## Local mail stack (Dovecot) rejects the login

**Symptom:** a local Dovecot/IMAP test account fails to authenticate from Odysseus
— typically "authentication failed", or a STARTTLS error.

**Why:** Odysseus attempts **STARTTLS by default** (`IMAP_STARTTLS=true`), and
Dovecot refuses cleartext logins on an unencrypted connection
(`disable_plaintext_auth = yes`). A bare local stack with no TLS therefore rejects
the login. Odysseus also deliberately closes the socket if STARTTLS is offered but
fails, rather than silently continuing in the clear (#3174).

**Fix** (trusted local network only):
- **Preferred:** give Dovecot a certificate and keep STARTTLS/SSL on. Odysseus
  auto-uses implicit SSL on port `993`.
- **Throwaway local stack:** set Dovecot `disable_plaintext_auth = no` and
  `auth_mechanisms = plain login`, and configure the account/env so STARTTLS is
  not forced (`IMAP_STARTTLS=false`). Never do this on an untrusted network — it
  sends the password in clear text.

---

## Self-hosted CalDAV / Radicale calendar won't sync

**Symptom:** saving Radicale/Nextcloud CalDAV credentials does nothing, or you get
`host is not allowed` / `Private CalDAV IPs require ODYSSEUS_ALLOW_PRIVATE_CALDAV=1`.

**Why:** CalDAV sync refuses SSRF-risky targets. `localhost`, `127.0.0.1`, and
`::1` are **hard-blocked**, and private/LAN/Tailscale IP ranges are refused unless
you explicitly opt in (`src/caldav_sync.py`).

**Fix:**
- Point Odysseus at the host's **LAN or Tailscale IP**, not `localhost`
  (e.g. `http://100.x.y.z:5232/<user>/` for Radicale).
- Set `ODYSSEUS_ALLOW_PRIVATE_CALDAV=1` in `.env` to permit private-range CalDAV
  hosts, then restart.
- The underlying `caldav` library does PROPFIND discovery, so a principal or
  collection URL like `http://<ip>:5232/<user>/` works across Radicale, Nextcloud,
  Apple, and Fastmail without you hand-crafting the protocol.

Contacts sync over **CardDAV** follows the same shape — set the collection URL via
`carddav_url` (or the `CARDDAV_URL` env var).

---

## (bonus) Search returns nothing / a service looks "degraded"

- **SearXNG returns 0 results:** the default general engines are routinely
  rate-limited or CAPTCHA-blocked, so Odysseus pins engines that actually respond
  (override with `SEARXNG_GENERAL_ENGINES`). Confirm with:
  ```bash
  docker compose logs odysseus | grep -E 'DEGRADED|SearXNG|unresponsive'
  ```
- **ChromaDB falls back to HTTP-only / fails to start:** usually the
  `chromadb-client` vs full `chromadb` conflict — see
  [setup.md](setup.md#chromadb-client-conflicts-with-embedded-chromadb).

---

Found another 30-second fix that cost you 30 minutes? Add it here — that is exactly
what this page is for.
