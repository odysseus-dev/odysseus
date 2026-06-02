# Odysseus Mobile — server setup & connecting your phone

This guide gets the **Odysseus Mobile** app ([repo](https://github.com/mahdi-salmanzade/odysseus-mobile))
talking to *this* Odysseus server over your local network. You run Odysseus on a
machine at home; your phone (on the same Wi-Fi) pairs to it and drives chat,
agents, sessions, notes, tasks and memory — local-first, nothing leaves your LAN.

```
 ┌─────────────┐     same Wi-Fi / LAN      ┌──────────────────────────┐
 │  Odysseus    │   http://<ip>:7860       │   Odysseus Mobile (app)   │
 │  server      │ ◀──────────────────────▶ │   pair → chat → stream    │
 └─────────────┘   Authorization: Bearer    └──────────────────────────┘
                       ody_<token>
```

The phone authenticates with an `ody_` API token (chat scope). The token lives
only in the phone's keychain and in your Odysseus token list — revoke it anytime.

---

## 1. Run the server, bound to your LAN

By default Odysseus binds to `127.0.0.1` (loopback), which the phone **can't**
reach. Bind to `0.0.0.0` so it's reachable on your Wi-Fi. Keep `AUTH_ENABLED=true`.

**macOS (Apple Silicon, native — recommended):**
```bash
cd odysseus
./venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 7860
```
(First-time setup: `./start-macos.sh` once to create the venv + admin password,
then re-run with `--host 0.0.0.0` as above. Port `7860` because macOS AirPlay
often holds `7000`.)

**Native Linux / Windows:**
```bash
python -m uvicorn app:app --host 0.0.0.0 --port 7000
```

**Docker:** set `APP_BIND=0.0.0.0` in `.env`, then `docker compose up -d`.

> First boot prints a temporary **admin password** in the terminal
> (`docker compose logs odysseus` for Docker). Log in once and change it.

### Find your LAN IP (what the phone connects to)
```bash
# macOS
ipconfig getifaddr en0
# Linux
hostname -I | awk '{print $1}'
```
e.g. `192.168.1.21`. The phone connects to `http://192.168.1.21:7860`.

### Firewall
Allow inbound connections to the port. macOS: System Settings → Network →
Firewall → allow the Python/uvicorn process (or disable for a trusted home LAN).

---

## 2. Add a model

The phone chats with whatever models this server has configured. In the web UI
(**http://localhost:7860**) → **Settings → Add Models**, add an endpoint:

- **Local (Ollama):** Base URL `http://localhost:11434/v1` — start Ollama with
  `OLLAMA_HOST=0.0.0.0:11434 ollama serve` and `ollama pull llama3.2:3b`.
- **Local (llama.cpp / vLLM):** point at your `…/v1` server.
- **Cloud (OpenAI-compatible, e.g. DeepSeek):** Base URL `https://api.deepseek.com`,
  paste your API key (stored encrypted server-side — it never reaches the phone).

The phone discovers these via the companion bridge and picks one automatically.

---

## 3. Pair your phone

Three ways — all produce the same `{ v, host, port, token }` pairing code:

**A. In the web UI (easiest):**
**Settings → Mobile App** (Admin section) → **Generate pairing code** → a QR
appears. Scan it from the app's **Scan QR** screen.

**B. Direct pairing page:** open `http://localhost:7860/api/companion/pair` in the
browser where you're logged in as admin → scan the QR.

**C. Terminal:**
```bash
python scripts/pair_mobile.py
```
Prints host / port / token + an ASCII QR.

In the app you can also tap **Enter manually** and type host, port, and token.

Each pairing mints a fresh chat-scoped token. Manage/revoke them under
**Settings → Account → API tokens**.

---

## 4. Connect & use

Once paired, the app:
- discovers your models and opens a chat session,
- streams replies (with **Web search**, **Research**, and **Agent** toggles),
- lists past **Sessions** (rename / delete),
- shows your **Notes**, **Tasks**, and **Memory** (read-only) via the sidebar (≡).

---

## The companion bridge (what makes this work)

A small additive layer in [`companion/`](companion/) — it adds **no** LLM logic.
See [`companion/README.md`](companion/README.md). Endpoints (all token-auth):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/companion/ping` | pairing health check |
| GET | `/api/companion/info` | server identity + capability flags |
| GET | `/api/companion/models` | the token owner's models |
| GET | `/api/companion/notes` · `/tasks` · `/memory` | owner-scoped read views |
| GET | `/api/companion/pair` | admin-only QR pairing page (`?format=json` for the in-app tab) |

Chat/session traffic uses the stock Odysseus API (`/api/session`,
`/api/chat_stream`, `/api/sessions`, `/api/history/{id}`) with the Bearer token.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| App says "Could not reach Odysseus" | Server must be bound to `0.0.0.0`; phone + server on the **same Wi-Fi**; check the firewall and the IP/port. |
| "Pairing token was rejected" (401) | Token revoked or stale — generate a new code (Settings → Mobile App). |
| "No models available" | Add and enable a model endpoint in **Settings → Add Models**, then reopen the app. |
| Notes / Tasks / Memory empty | They're owner-scoped to you — they're just empty until you create some on the server. |
| Chat errors with a 4xx | Make sure the model endpoint's API key is set on the server (Settings → Add Models). |

---

## Security

Binding to `0.0.0.0` exposes Odysseus to your LAN — keep `AUTH_ENABLED=true`, and
treat the pairing token as a real credential. For access **beyond** a trusted home
network (e.g. over the internet), put Odysseus behind a TLS reverse proxy — see the
main [README](README.md#putting-it-behind-https). The app talks plain HTTP, which
is fine for a trusted LAN/VPN but not for the public internet.
