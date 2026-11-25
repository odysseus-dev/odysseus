# Changelog

## 1.3.5

- **Fix: rebuilt images still served the old frontend.** Every JS module is
  loaded with a `?v=<date>` cache-bust parameter and the service worker
  precaches `/static/…` under a pinned `CACHE_NAME` — neither was bumped with
  1.3.4, so after a rebuild the browser kept running the pre-ingress JS:
  same 404s, same endless spinner. All 325 version params are bumped
  (`20260916…` → `20261125…`) and the precache name moves to
  `odysseus-v1138-ingress-apibase-precache`, so the old caches are dropped.

## 1.3.4

- **Fix: 404s and endless spinner in the sidebar panel.** Fifteen frontend
  modules still derived their API base from `window.location.origin`
  (`sessions`, `modelPicker`, `gallery`, `galleryEditor`, `emailInbox`, the
  whole `emailLibrary/*` set, `signature`, `tasks`, `workspace`,
  `chatRenderer`'s consumer). Under the ingress prefix that produced
  `https://<ha>/api/...` — outside the panel path — so the app hit Home
  Assistant core instead of Odysseus: "Failed to load sessions: Session
  request failed (HTTP 404)", and sending a message never opened
  `/api/chat_stream`, leaving the spinner running forever. All of them now use
  `window.__odysseusAppBase` (empty direct, mount prefix under ingress), the
  same source the bootstrap in `index.html` already provides.
- A guard test fails the build if any module reintroduces
  `... = window.location.origin` for an API base.

## 1.3.3

- **Fix: sidebar panel 502 (for real this time)**. 1.3.2's probe disabled
  `IPV6_V6ONLY` on its own test socket, but uvicorn creates a *fresh* socket
  that doesn't carry that flag — so with `net.ipv6.bindv6only=1` the listener
  stayed IPv6-only and Supervisor's IPv4 ingress connection kept failing
  (`Ingress error: Cannot connect to host 172.30.33.5:7000`). The default bind
  is now **`0.0.0.0`** (IPv4), which is what Supervisor's ingress proxy and
  ordinary clients use; set `bind_address: "::"` only on a network that
  really accepts IPv4-mapped connections.

## 1.3.2

- **Fix: sidebar panel 502 "Bad Gateway"**. Supervisor's ingress proxy
  connects to the add-on container over **IPv4** (`172.30.33.5:7000`), and the
  entrypoint probe only checked that an IPv6 wildcard socket *binds* — which
  also succeeds when the netns has `bindv6only=1`, leaving a listener that
  refuses every IPv4 connection. Supervisor then logged
  `Ingress error: Cannot connect to host …:7000` and returned 502, while the
  host port-map kept working. The probe now binds `::` with `IPV6_V6ONLY=0`
  and then *proves* dual-stack by connecting over `127.0.0.1`, falling back to
  `0.0.0.0` if IPv4 isn't actually accepted.

## 1.3.1

- **Fix: the add-on cloned upstream `dev`, not this fork** — so the 1.2.0/1.3.0
  auth features (HA credential login, sidebar panel SSO, mount-prefix
  awareness) never actually ran in the add-on; the image built upstream code.
  `ODYSSEUS_REPO` now defaults to `gkmzfk6um/odysseus` (keep the `dev` branch
  there in sync with the add-on branch).
- **Fix: bundle a ChromaDB server** — upstream runs it as a separate compose
  service; the add-on image now ships the server package and `run.sh` starts
  it loopback-only on `127.0.0.1:8100` with data under `/data/chromadb`, so
  vector memory / RAG work instead of booting degraded.

## 1.3.0

- **Sidebar panel with Home Assistant single sign-on**: `ingress: true` +
  `panel_title`/`panel_icon` add Odysseus to the HA sidebar. Supervisor
  injects the signed-in HA user's identity (`X-Remote-User-*`) on proxied
  panel requests and filters client-supplied versions; Odysseus trusts
  them only from the Supervisor container's own connections and issues a
  normal session cookie — open the panel and you are in, no login form.
  HA admins become Odysseus admins, other HA users regular accounts.
- The frontend is mount-prefix aware end to end: every URL the browser
  builds (`fetch`, `EventSource`, asset `<link>`/`<script>`, payload
  image URLs, redirects) carries the `/api/hassio_ingress/<token>/`
  prefix via a runtime-derived `appUrl()` helper; server redirects are
  relative so they resolve under any mount.
- Frame policy relaxes to `SAMEORIGIN` / `frame-ancestors 'self'` only for
  Supervisor-proxied requests; direct-port traffic keeps `DENY` / `'none'`.
- **Add-on icon and logo**: `icon.png` (128px) and `logo.png` (256px) in the
  add-on folder — Supervisor serves both from fixed filenames; without them
  the store showed a fallback image instead of the Odysseus mark.
- `ingress_stream: true` keeps POST bodies unbuffered through the panel so
  uploads and streaming responses stay low-latency.

## 1.2.0

- **Login with your Home Assistant credentials**: the Odysseus login form now
  accepts HA username/password pairs, verified by the Supervisor auth API
  (`auth_api: true`). HA admins become Odysseus admins on first login; other
  HA users get regular accounts. Local passwords keep working unchanged.
- New `hass_auth` option (default on) to toggle the HA credential login.
- **No admin setup needed**: with `hass_auth` on, leave `admin_user` /
  `admin_password` empty — the first HA login bootstraps the instance and
  becomes the Odysseus admin.
- Failure closed: if the Supervisor is unreachable or rejects the pair,
  Odysseus refuses the login instead of failing open.

## 1.1.0

- Track the current Odysseus `dev` line (588 upstream commits since 1.0.0),
  including the photo editor, tool-approval mode, local MCP tool declarations
  and the new search/agent fixes.
- Image now inherits the upstream runtime hardening: refreshed system and Python
  CA bundles with the eMudhra distrust checks, `NODE_OPTIONS=--use-openssl-ca`,
  `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH`, and the Chromium policy override.
- Chromium gains `fonts-noto-cjk` + `fontconfig` so CJK pages render glyphs, and
  the image ships the network inspection tools (`iproute2`, `iputils-ping`,
  `net-tools`, `dnsutils`, `nmap`) used by agent tasks.
- Added `agent-browser` so the native `private_browser` tool drives the bundled
  Chromium without per-call `npx` installs.
- New dependencies from upstream: `pypdfium2`, `pdfplumber`, `faster-whisper`,
  `yt-dlp`, `yt-dlp`-backed media tools, `Pillow>=13`, `certifi>=2026.10.23`,
  `grpcio>=1.84.1`, `fastembed>=0.9.1`.
- New options: `tool_approval_gate` (default on) and `response_compression`
  (default on). Extra environment variables documented in `DOCS.md`.
- Base image pinned to `python:3.14.9-slim`; `/data/.local/bin` is on `PATH` so
  `pip --user` installs from the Cookbook resolve.

## 1.0.0

- Initial Home Assistant OS add-on package.
- Direct web UI / API access on port `7000`.
- Model provider dropdown + API key (seeds a ready-to-use model endpoint).
- Database options (SQLite on `/data`, or an external PostgreSQL server).
- Web search provider options (SearXNG, Brave, Tavily, Serper, Google PSE, …).