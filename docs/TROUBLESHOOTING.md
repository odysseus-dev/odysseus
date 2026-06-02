# Self-Host Troubleshooting Cookbook
───────────────────────────────────────────────
The weird 30-second fixes that otherwise become 30-minute searches.
───────────────────────────────────────────────

Each section is a trap that bites self-hosters. The fix is almost always
short; finding it is the hard part. If you hit something not listed here,
open an issue so the next person doesn't repeat the search.

---

## 1. Docker Setup Issues

### 1.1 Container writes root-owned files into bind mounts

**Symptom:** After `docker compose up`, files in `data/` or `logs/` are
owned by root. The host user (or a later non-root container run) can't
write to them. Skills, preferences, and mail attachments silently fail.

**Cause:** Docker containers run as root by default. The Odysseus
entrypoint (`docker/entrypoint.sh`) fixes this automatically via
PUID/PGID, but only if the IDs match your host user.

**Fix:** Set PUID/PGID to your host user's UID/GID in `.env`:

```bash
# Find your IDs
id -u    # e.g. 1000
id -g    # e.g. 1000

# In .env:
PUID=1000
PGID=1000
```

Then recreate:

```bash
docker compose down
docker compose up -d
```

If files are already root-owned from a previous run, fix them once:

```bash
sudo chown -R $(id -u):$(id -g) data/ logs/
```

### 1.2 GPU passthrough: NVIDIA

**Symptom:** `nvidia-smi` works on the host but fails inside the
container. Cookbook can't detect GPU. vLLM/llama.cpp won't start.

**Fix (three steps):**

1. Install the NVIDIA Container Toolkit on the host:

   ```bash
   # Debian/Ubuntu
   sudo apt install nvidia-container-toolkit
   # Arch
   sudo pacman -S nvidia-container-toolkit
   # Fedora
   sudo dnf install nvidia-container-toolkit
   ```

2. Configure Docker to use the NVIDIA runtime:

   ```bash
   sudo nvidia-ctk runtime configure --runtime=docker
   sudo systemctl restart docker
   ```

3. Enable the GPU overlay in `.env`:

   ```bash
   COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml
   ```

Verify:

```bash
docker compose up -d
docker compose exec odysseus nvidia-smi -L
```

**Still failing?** The slim Odysseus image does NOT bundle CUDA
userspace. The overlay only passes the GPU device through. You still
need to install the actual inference engine (vLLM, llama-cpp-python,
SGLang) via **Cookbook > Dependencies** or `pip install` inside the
container before you can serve GPU models.

### 1.3 GPU passthrough: AMD ROCm

**Symptom:** ROCm works on the host but the container can't see `/dev/kfd`
or `/dev/dri`.

**Fix:** Enable the AMD overlay in `.env`:

```bash
COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml
```

Your host user must be in the `video` and `render` groups:

```bash
sudo usermod -aG video,render $USER
# Log out and back in for the group change to take effect
```

Verify:

```bash
docker compose exec odysseus rocm-smi
```

### 1.4 GPU passthrough: `COMPOSE_FILE` ignored

**Symptom:** Set `COMPOSE_FILE` in `.env` but GPU still not passed
through. `docker compose config` shows no device reservations.

**Cause:** `COMPOSE_FILE` must be a colon-separated list of actual
files. If the overlay file path is wrong, Docker silently ignores it.

**Fix:** Make sure the value uses colons (not commas) and the files exist:

```bash
# Correct:
COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml

# Wrong (comma):
COMPOSE_FILE=docker-compose.yml,docker/gpu.nvidia.yml
```

Verify the merged config:

```bash
docker compose config | grep -A5 "devices\|reservations"
```

### 1.5 Port already in use (7000)

**Symptom:** `docker compose up` fails with `Bind for 0.0.0.0:7000
failed: port is already allocated`.

**Cause:** macOS AirPlay commonly holds port 7000. Other services
may too.

**Fix:** Change the port in `.env`:

```bash
APP_PORT=7001
```

Then `docker compose up -d`. Access Odysseus at `http://localhost:7001`.

### 1.6 Container health check fails / services not ready

**Symptom:** Odysseus container shows `unhealthy` or restarts in a
loop. Logs show ChromaDB or SearXNG connection errors during startup.

**Cause:** Odysseus depends on ChromaDB and SearXNG being healthy
before it starts. If those take too long to initialize, the startup
timeout may be hit.

**Fix:** Check which dependency is slow:

```bash
docker compose ps
docker compose logs --tail=50 chromadb
docker compose logs --tail=50 searxng
```

Common slow-start causes:
- First-time ChromaDB image pull (one-time)
- SearXNG generating a secret key on first boot
- Slow disk I/O on the volume mount

Give it a minute. If it consistently fails:

```bash
docker compose down -v   # WARNING: deletes volumes
docker compose up -d --build
```

### 1.7 File permission errors after host user change

**Symptom:** After changing the host user (or moving the install to a
new machine), the app can't write to `data/` or logs show EPERM errors.

**Fix:**

```bash
# Stop the stack
docker compose down

# Fix ownership on the host
sudo chown -R $(id -u):$(id -g) data/ logs/

# Update PUID/PGID in .env to match
echo "PUID=$(id -u)" >> .env
echo "PGID=$(id -g)" >> .env

# Restart
docker compose up -d
```

The entrypoint will also chown files inside the container on every
start, but host-side ownership must be correct first.

---

## 2. Ollama Connection Issues

### 2.1 Odysseus (Docker) can't reach host Ollama

**Symptom:** Models don't appear in the model list. Logs show
`Connection refused` to `127.0.0.1:11434` or similar.

**Cause:** Inside a Docker container, `localhost` refers to the
container itself, not the host. Ollama also defaults to listening on
`127.0.0.1` only.

**Fix (two parts):**

1. Start Ollama to listen on all interfaces:

   ```bash
   OLLAMA_HOST=0.0.0.0:11434 ollama serve
   ```

   To make this permanent, add `OLLAMA_HOST=0.0.0.0:11434` to your
   Ollama systemd service override or shell profile.

2. In Odysseus **Settings > Endpoints**, add the Ollama endpoint as:

   ```
   http://host.docker.internal:11434/v1
   ```

   The `docker-compose.yml` already includes
   `extra_hosts: ["host.docker.internal:host-gateway"]`, so this
   hostname resolves to the Docker host from inside the container.

**Verify from inside the container:**

```bash
docker compose exec odysseus curl -s http://host.docker.internal:11434/api/tags
```

### 2.2 Ollama connection works but model list is empty

**Symptom:** Endpoint is reachable but no models appear. Health check
passes.

**Cause:** The endpoint URL might be wrong. Odysseus needs the
OpenAI-compatible `/v1` path for model listing. Some setups use the
native Ollama API path (`/api`) which returns different formats.

**Fix:** Make sure the endpoint URL ends with `/v1`:

```
http://host.docker.internal:11434/v1
```

Not `/api` or `/api/tags` or `/v1/chat/completions`. Just `/v1`.

### 2.3 CORS errors when accessing Odysseus from a different origin

**Symptom:** Browser console shows `CORS policy: No
'Access-Control-Allow-Origin' header`. Requests from the frontend fail.

**Cause:** The default CORS allowlist is `localhost` and `127.0.0.1`
only. If you access Odysseus via a Tailscale IP, LAN IP, or domain
name, the origin won't match.

**Fix:** Set `ALLOWED_ORIGINS` in `.env` to include your actual origin:

```bash
# Single origin:
ALLOWED_ORIGINS=https://odysseus.my-tailnet.ts.net

# Multiple origins (comma-separated):
ALLOWED_ORIGINS=http://localhost:7000,https://odysseus.my-tailnet.ts.net,http://192.168.1.50:7000
```

Restart Odysseus after changing.

### 2.4 Model loads but generation is extremely slow

**Symptom:** Ollama responds but takes minutes per token. `nvidia-smi`
shows the model is on CPU.

**Cause:** Ollama may have fallen back to CPU if it can't allocate
enough GPU VRAM, or the model is too large for your GPU.

**Fix:**

```bash
# Check if Ollama sees the GPU
ollama ps
# Should show GPU memory usage, not just CPU

# Check VRAM availability
nvidia-smi
```

If VRAM is full, try a smaller quantization (Q4_K_M instead of Q8_0)
or close other GPU consumers.

### 2.5 `host.docker.internal` doesn't resolve

**Symptom:** `curl: (6) Could not resolve host: host.docker.internal`
from inside the container.

**Cause:** On older Docker Engine versions or certain Linux
configurations, the `host-gateway` magic doesn't work.

**Fix:** Use the host's actual IP instead:

```bash
# Find the Docker bridge gateway IP
docker network inspect bridge | grep Gateway
# Usually 172.17.0.1

# Use that in Settings:
# http://172.17.0.1:11434/v1
```

Or, if you're on a known LAN IP:

```bash
# http://192.168.1.50:11434/v1
```

---

## 3. ChromaDB Issues

### 3.1 `Connection refused` to ChromaDB

**Symptom:** Logs show `MemoryVectorStore DEGRADED: ChromaDB vector
memory unavailable`. Memory search returns no results.

**Cause:** ChromaDB container isn't running, or Odysseus is trying to
connect on the wrong host/port.

**Docker fix:** Inside `docker-compose.yml`, Odysseus connects to
`chromadb:8000` (the internal Docker network). The host-port mapping
is `127.0.0.1:8100:8000` for host-side debugging only. Do NOT set
`CHROMADB_HOST=localhost` and `CHROMADB_PORT=8100` in the Docker `.env`
-- the compose file overrides these to `chromadb:8000` automatically.

Check that ChromaDB is healthy:

```bash
docker compose ps chromadb
docker compose logs --tail=20 chromadb
```

**Native (non-Docker) fix:** If running Odysseus natively with a
separate ChromaDB container:

```bash
# Start ChromaDB standalone
docker run -d --name chromadb -p 8100:8000 \
  -e ANONYMIZED_TELEMETRY=FALSE \
  -v chromadb-data:/chroma/chroma \
  chromadb/chroma:latest

# In .env:
CHROMADB_HOST=localhost
CHROMADB_PORT=8100
```

### 3.2 ChromaDB starts but memory search returns nothing

**Symptom:** ChromaDB is healthy, the vector store initialized, but
`MemoryVectorStore` count is 0 and searches return empty.

**Cause:** The embedding backend is not working. ChromaDB stores
pre-computed embeddings (it does NOT generate them itself). If the
embedding client failed to initialize, entries can't be indexed.

**Fix:** Check embedding status in the logs:

```bash
docker compose logs odysseus | grep -i "embedding\|fastembed\|DEGRADED"
```

You should see one of:
- `Using HTTP embedding API: http://...` (Ollama/vLLM endpoint)
- `Using local FastEmbed: model=...` (local ONNX fallback)

If you see `HTTP embedding API unavailable` and no fallback, install
fastembed:

```bash
# Inside the container:
docker compose exec odysseus pip install fastembed

# Or for native install:
pip install fastembed
```

### 3.3 `chromadb-client` not installed

**Symptom:** `RuntimeError: ChromaDB integration is not installed.
Install the optional dependency with: pip install chromadb-client`

**Fix:**

```bash
# Docker:
docker compose exec odysseus pip install chromadb-client

# Native:
pip install chromadb-client
```

Then restart Odysseus.

### 3.4 Embedding dimension mismatch

**Symptom:** ChromaDB errors about dimension mismatch when adding or
querying vectors. Happens after switching embedding models.

**Cause:** The collection was created with one embedding dimension
(e.g., 384 for all-MiniLM-L6-v2) but the new model produces a
different dimension (e.g., 1024 for a larger model).

**Fix:** Rebuild the memory vector index. In Odysseus, this happens
automatically on restart if the collection is empty. To force it:

```bash
# Stop Odysseus
docker compose stop odysseus

# Delete the ChromaDB collection (via Python in the container)
docker compose exec chromadb python3 -c "
import chromadb
c = chromadb.HttpClient(host='localhost', port=8000)
try:
    c.delete_collection('odysseus_memories')
    print('Deleted odysseus_memories collection')
except:
    print('Collection not found (already clean)')
"

# Restart — the app will rebuild the index with the new model
docker compose start odysseus
```

---

## 4. SearXNG Issues

### 4.1 SearXNG secret mismatch after volume recreation

**Symptom:** SearXNG returns 403 or CSRF errors. Web search in
Odysseus returns empty results. Browser shows SearXNG's own error page.

**Cause:** Docker generates a random `SEARXNG_SECRET` on first boot and
bakes it into the SearXNG settings file. If you recreate the volume
(`docker compose down -v`) but keep the old `.env` with a pinned
`SEARXNG_SECRET`, or vice versa, the cookie secret won't match.

**Fix:** Let SearXNG regenerate its secret:

```bash
docker compose down
docker volume rm odysseus_searxng-data   # or: docker compose down -v
docker compose up -d
```

If you need a pinned secret (e.g., for cookie persistence across
rebuilds), set it in `.env` BEFORE the first boot:

```bash
SEARXNG_SECRET=your-long-random-string-here
```

Do NOT change it after first boot without also recreating the volume.

### 4.2 SearXNG is running but web search returns nothing

**Symptom:** No error in Odysseus, but search results are always empty.

**Cause:** SearXNG's default config has `json` format enabled (the
Odysseus template at `config/searxng/settings.yml` ensures this), but
if the template was overridden or the secret is wrong, the JSON API
may not respond.

**Fix:** Test SearXNG directly:

```bash
# From the host (port 8080 is bound to 127.0.0.1):
curl -s "http://localhost:8080/search?q=test&format=json" | head -c 500

# From inside the Docker network:
docker compose exec odysseus curl -s "http://searxng:8080/search?q=test&format=json" | head -c 500
```

If the first works but the second doesn't, the SearXNG container may
not be healthy. Check:

```bash
docker compose ps searxng
docker compose logs --tail=30 searxng
```

### 4.3 SearXNG shows "too many requests" or rate limits

**Symptom:** Search works initially but starts returning errors after
a few queries.

**Cause:** SearXNG proxies search engines on your behalf. Upstream
engines (Google, Bing, etc.) rate-limit based on IP. Heavy use from a
single server IP triggers blocks.

**Fix:** This is expected behavior. Odysseus has a fallback chain
(`search_fallback_chain` in settings, default: `["duckduckgo"]`) that
tries DuckDuckGo when the primary provider fails. No configuration
needed.

For higher reliability, enable multiple search providers in **Settings >
Search** or add a paid API (Brave, Tavily, Serper) as the primary.

### 4.4 SearXNG container keeps restarting

**Symptom:** `docker compose ps` shows SearXNG in a restart loop.

**Cause:** Usually a malformed `settings.yml` template or a volume
permission issue.

**Fix:**

```bash
# Check the logs
docker compose logs --tail=30 searxng

# Verify the template is valid YAML
cat config/searxng/settings.yml
```

The template must contain `__SEARXNG_SECRET__` as a placeholder -- the
entrypoint script replaces it with the real secret. If you edited the
template and removed that placeholder, the sed replacement fails.

---

## 5. Email Integration

### 5.1 Dovecot: cleartext authentication not allowed

**Symptom:** IMAP login fails with `Authentication failed` even though
the username and password are correct. Logs may show `[AUTHENTICATIONFAILED]` or `cleartext logins are disabled`.

**Cause:** Dovecot (and some other IMAP servers) refuse plaintext LOGIN
commands over unencrypted connections by default. If you're running a
local Dovecot on a non-standard port (e.g., 31143) without TLS, the
LOGIN command is rejected.

**Fix:** In Odysseus email account settings:
- **Turn OFF** "Use STARTTLS"
- **Set the port** to your Dovecot port (e.g., 31143, NOT 993)
- Odysseus detects: STARTTLS off + port != 993 = plain IMAP4 (no TLS)

The relevant code path in `_imap_connect`:
- STARTTLS on -> plain socket, then upgrade with STARTTLS
- STARTTLS off + port 993 -> implicit SSL (IMAPS)
- STARTTLS off + other port -> plain IMAP4 (no TLS, for local servers)

If you control the Dovecot server, alternatively enable cleartext auth:

```ini
# /etc/dovecot/conf.d/10-auth.conf
disable_plaintext_auth = no
```

Then restart Dovecot: `sudo systemctl restart dovecot`

### 5.2 SMTP: `[SSL: WRONG_VERSION_NUMBER]` on port 587

**Symptom:** Sending email fails with `[SSL: WRONG_VERSION_NUMBER]`
even though credentials are correct.

**Cause:** Port 587 uses STARTTLS (plain connection upgraded to TLS),
not implicit SSL. If the client tries `SMTP_SSL` (implicit TLS) against
port 587, the TLS handshake fails because the server expects a plain
text `STARTTLS` command first.

**Fix:** Odysseus handles this automatically -- port 587 uses
`smtplib.SMTP` + `starttls()`, port 465 uses `smtplib.SMTP_SSL`. Just
make sure you set the correct port in your email account settings:
- **587** = STARTTLS (most providers)
- **465** = Implicit SSL (some providers)

### 5.3 Gmail / Google Workspace: app passwords required

**Symptom:** IMAP login fails with `Authentication failed` for a Gmail
account even with the correct password.

**Cause:** Google requires OAuth2 for IMAP/SMTP, or an **App Password**
if you have 2FA enabled. Your regular Google password won't work.

**Fix:**

1. Enable 2-Step Verification on your Google account (required for App
   Passwords).
2. Go to https://myaccount.google.com/apppasswords
3. Generate an app password for "Mail" on "Other (Custom name)" -- call
   it "Odysseus".
4. Use that 16-character password (no spaces) as the IMAP/SMTP password
   in Odysseus.

**IMAP settings for Gmail:**
- Host: `imap.gmail.com`
- Port: `993`
- STARTTLS: OFF (port 993 = implicit SSL)
- Username: `you@gmail.com`
- Password: the app password (not your Google password)

**SMTP settings for Gmail:**
- Host: `smtp.gmail.com`
- Port: `465` or `587`
- Username: `you@gmail.com`
- Password: same app password

### 5.4 Fastmail / ProtonMail Bridge / other providers

**IMAP/SMTP settings for common providers:**

| Provider | IMAP Host | IMAP Port | SMTP Host | SMTP Port | STARTTLS |
|----------|-----------|-----------|-----------|-----------|----------|
| Gmail | imap.gmail.com | 993 | smtp.gmail.com | 465 | OFF |
| Outlook/365 | outlook.office365.com | 993 | smtp.office365.com | 587 | ON |
| Fastmail | imap.fastmail.com | 993 | smtp.fastmail.com | 465 | OFF |
| ProtonMail Bridge | 127.0.0.1 | 1143 | 127.0.0.1 | 1025 | OFF* |
| Generic Dovecot | (your host) | 993 or custom | (your host) | 587 or 465 | depends |

*ProtonMail Bridge uses its own local TLS. Set STARTTLS OFF and use the
Bridge's local ports.

### 5.5 Docker: Odysseus can't reach a local mail server

**Symptom:** IMAP/SMTP connection times out when the mail server runs
on the host.

**Cause:** Same as the Ollama issue -- `localhost` inside the container
is the container itself, not the host.

**Fix:** Use `host.docker.internal` as the mail host:

```
IMAP host: host.docker.internal
SMTP host: host.docker.internal
```

Or use the host's LAN/Tailscale IP directly.

---

## 6. Calendar / CalDAV

### 6.1 Radicale: "Discovery failed" or empty calendar list

**Symptom:** CalDAV test connection works but sync returns 0 calendars.
Logs show `Discovery failed: ...` or `CalDAV principal discovery failed`.

**Cause:** Radicale's default CalDAV URL is NOT the principal URL. The
`caldav` Python library tries to discover calendars via
`/.well-known/caldav` or the principal URL first. Radicale serves
calendars at specific collection paths, not at the root.

**Fix:** Point Odysseus at the full calendar collection URL:

```
# Wrong (discovery fails):
http://localhost:5232/

# Right (direct calendar URL):
http://localhost:5232/user/calendar/

# Or with Radicale's default user-based path:
http://localhost:5232/username/collection-name/
```

If you don't know your collection URL, find it:

```bash
# List all collections on the Radicale server:
curl -u "user:password" -X PROPFIND -H "Depth: 1" \
  "http://localhost:5232/" 2>/dev/null | grep -oP 'href>[^<]+'
```

The Odysseus CalDAV sync code tries principal discovery first, then
falls back to treating the URL as a direct calendar. If discovery fails,
it will still work if the URL points to an actual calendar collection.

### 6.2 Nextcloud: CalDAV URL

**Correct URL format for Nextcloud:**

```
https://nextcloud.example.com/remote.php/dav/calendars/username/personal/
```

Not the WebDAV root, not the Nextcloud homepage. The full
`/remote.php/dav/calendars/...` path.

### 6.3 Apple iCloud: requires app-specific password

**Symptom:** CalDAV auth fails with Apple ID credentials.

**Cause:** iCloud CalDAV requires an app-specific password, not your
Apple ID password.

**Fix:**

1. Go to https://appleid.apple.com/account/manage
2. Sign-In and Security > App-Specific Passwords
3. Generate a password for "Odysseus"
4. Use it as the CalDAV password

**CalDAV URL for iCloud:**

```
https://caldav.icloud.com/
```

Username: your Apple ID (email). Password: the app-specific password.

### 6.4 Fastmail: CalDAV URL

```
https://caldav.fastmail.com/dav/calendars/user/you@fastmail.com/
```

Use your Fastmail email as username and your Fastmail password (or app
password if 2FA is enabled).

### 6.5 Events appear at wrong times after sync

**Symptom:** Events from CalDAV appear shifted by your timezone offset.

**Cause:** Odysseus converts all datetimes to UTC for storage and flags
them `is_utc=True`. The frontend then renders them in the browser's
local timezone. If the source event has no TZID (naive datetime), it's
treated as local time, which may be wrong.

**Fix:** Make sure your CalDAV server sends timezone-aware datetimes
(most do). If events from a specific server consistently shift, check
that the server's timezone config matches the event creators' timezone.

---

## 7. Tailscale / VPN

### 7.1 HTTPS certificates for Tailscale

**Symptom:** Browser shows "Not Secure" or `NET::ERR_CERT_AUTHORITY_INVALID` when accessing Odysseus over Tailscale.

**Cause:** Odysseus serves plain HTTP. Tailscale IPs are routable
within your tailnet but don't automatically get TLS certificates.

**Fix (pick one):**

**Option A: Tailscale HTTPS + Caddy (recommended)**

Tailscale can issue TLS certificates for `*.ts.net` hostnames:

```bash
# Enable HTTPS in Tailscale
tailscale cert your-machine-name.your-tailnet.ts.net

# Caddy reverse proxy
# /etc/caddy/Caddyfile:
your-machine-name.your-tailnet.ts.net {
    reverse_proxy localhost:7000
}
```

**Option B: Caddy with automatic Tailscale certs**

```caddy
# Caddyfile
odysseus.ts.net {
    reverse_proxy localhost:7000
    tls internal
}
```

Caddy's `tls internal` tells it to use Tailscale's built-in CA for
`.ts.net` domains.

**Option C: Self-signed cert (quick and dirty)**

```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem \
  -days 365 -nodes -subj "/CN=odysseus.local"
```

Then use Caddy or nginx with those certs. Browsers will warn but
traffic is encrypted.

### 7.2 Clipboard limits on plain-HTTP Tailscale URLs

**Symptom:** You copy a Tailscale URL like
`http://100.x.y.z:7000/some/path` and paste it somewhere, but it gets
truncated, mangled, or the receiving app strips the IP.

**Cause:** Some apps and clipboard managers (especially on mobile) have
trouble with long `http://` URLs containing raw IPs and ports. The URL
may get truncated at special characters or wrapped in markdown link
syntax.

**Fix:** Use the MagicDNS hostname instead of the raw IP:

```
# Instead of:
http://100.64.0.1:7000/chat/abc123

# Use:
http://my-machine.tail12345.ts.net:7000/chat/abc123
```

Even better, set up HTTPS (section 7.1) and use a short Caddy-managed
domain. Shorter URLs are less likely to be mangled.

### 7.3 Tailscale DNS not resolving inside Docker

**Symptom:** From inside the Odysseus container, `tailscale status`
works on the host but Tailscale hostnames can't be resolved.

**Cause:** Docker containers don't inherit the host's Tailscale DNS
configuration by default.

**Fix:** Odysseus has built-in Tailscale hostname resolution. When a
hostname can't be resolved via normal DNS, it runs `tailscale status
--json` to look up the Tailscale IP. But this only works if the
`tailscale` binary is available inside the container.

For Docker, the simplest fix is to use the Tailscale IP directly in
your configuration rather than the hostname:

```
# Instead of:
http://ollama-server.tailnet:11434/v1

# Use:
http://100.64.0.5:11434/v1
```

Or configure Docker to use Tailscale's DNS:

```yaml
# In docker-compose.yml, under the odysseus service:
services:
  odysseus:
    dns:
      - 100.100.100.100  # Tailscale DNS
```

### 7.4 ntfy notifications not received on Android (non-ntfy.sh server)

**Symptom:** ntfy test message from Odysseus succeeds (HTTP 200), but
the Android ntfy app never shows the notification.

**Cause:** The official ntfy Android app uses Firebase Cloud Messaging
(FCM) for instant delivery when connected to `ntfy.sh`. For
self-hosted ntfy servers, the app falls back to background polling,
which has significant delays (sometimes minutes) or may not work at all
if Android kills the background process.

**Fix:** This is a known ntfy limitation. Options:

1. **Use polling mode in the ntfy app:** Open the ntfy app > Settings
   > "Use WebSocket for instant delivery" -- make sure this is ON for
   self-hosted servers. The app will maintain a WebSocket connection for
   faster delivery.

2. **Set a shorter poll interval:** In the ntfy Android app, for each
   subscription, set the poll interval to a shorter value (e.g., 30
   seconds).

3. **Ensure the ntfy server is reachable from the phone:** If ntfy is
   bound to `127.0.0.1` (the Docker default), your phone can't reach
   it. Set `NTFY_BIND` to your Tailscale IP or LAN IP in `.env`:

   ```bash
   NTFY_BIND=100.64.0.1
   NTFY_BASE_URL=http://100.64.0.1:8091
   ```

   Then `docker compose up -d` to recreate the ntfy container.

4. **On the phone, subscribe with the full server URL:**
   - Server: `http://100.64.0.1:8091` (your actual ntfy URL)
   - Topic: `reminders` (or whatever `reminder_ntfy_topic` is set to)

   Do NOT leave the server field blank (that defaults to `ntfy.sh`).

### 7.5 Tailscale IP changes break integrations

**Symptom:** Everything worked yesterday. Today, connections to other
Tailscale nodes fail.

**Cause:** Tailscale IPs are generally stable but can change if a node
is removed and re-added, or if the tailnet configuration changes.

**Fix:** Use Tailscale MagicDNS hostnames instead of IPs where
possible. In Odysseus endpoint settings, prefer:

```
http://ollama-host.tailnet:11434/v1
```

over:

```
http://100.64.0.5:11434/v1
```

If DNS resolution is the issue (section 7.3), consider setting up a
local `/etc/hosts` entry or using Tailscale's `tailscale ip` command
to look up the current IP:

```bash
tailscale ip -4 ollama-host
```

---

## 8. Common Error Messages and Their Fixes

### `Connection refused` (any service)

**Diagnosis order:**
1. Is the service running? `docker compose ps`
2. Is it on the right host/port? Check `.env` and `docker-compose.yml`
3. Is it the Docker `localhost` trap? Use `host.docker.internal` for host services
4. Is there a firewall blocking the port?

### `MemoryVectorStore DEGRADED: ChromaDB vector memory unavailable`

See section 3.1. ChromaDB is not reachable. Memory search and semantic
dedup are disabled. The app still works for everything else.

### `[AUTHENTICATIONFAILED]` (IMAP)

See section 5.1 (Dovecot cleartext auth) and 5.3 (app passwords).
Check that the password is correct, the port matches the TLS mode, and
the server allows the auth method.

### `[SSL: WRONG_VERSION_NUMBER]` (SMTP)

See section 5.2. Port 587 wants STARTTLS, not implicit SSL. Make sure
you set the right port in your email account settings.

### `RuntimeError: ChromaDB integration is not installed`

See section 3.3. Install `chromadb-client`.

### `RuntimeError: No embedding backend available`

The embedding client failed to initialize. Either the HTTP embedding
endpoint (Ollama/vLLM) is down AND fastembed is not installed. Fix by
starting Ollama or installing fastembed (`pip install fastembed`).

### `Could not find nvcc` / `CUDA compiler and toolkit headers are incompatible`

**Symptom:** vLLM installed via Cookbook crashes on startup with CUDA
errors.

**Cause:** pip-installed vLLM wheels include `nvidia-cuda-*` packages
but don't set `CUDA_HOME`. FlashInfer's JIT sampler can't find `nvcc`.

**Fix:** The Odysseus Docker entrypoint handles this automatically --
it sets `CUDA_HOME` and disables the FlashInfer sampler if it detects a
pip-installed nvcc. If you're running natively, set these env vars:

```bash
export CUDA_HOME=/path/to/cuda   # e.g. /usr/local/cuda
export VLLM_USE_FLASHINFER_SAMPLER=0
```

### `DEGRADED` in logs but everything seems to work

This is normal. Odysseus reports `DEGRADED` for optional subsystems
(ChromaDB, embeddings, etc.) that aren't available. Core chat, agent,
and document features work without them. Check which subsystem is
degraded:

```bash
docker compose logs odysseus | grep DEGRADED
```

### Health check returns non-200

The health endpoint is `GET /api/health`. If it fails:

```bash
# Check from inside the container
docker compose exec odysseus curl -f http://localhost:7000/api/health

# Check from the host
curl -f http://localhost:7000/api/health
```

If the in-container check works but the host check fails, the port
mapping may be wrong. If neither works, Odysseus itself isn't starting.

---

## Quick Diagnostic Commands

```bash
# Overall status
docker compose ps

# Logs for a specific service
docker compose logs --tail=120 odysseus
docker compose logs --tail=50 chromadb
docker compose logs --tail=50 searxng
docker compose logs --tail=50 ntfy

# Check for memory/embedding issues
docker compose logs odysseus | grep -E 'ChromaDB|MemoryVectorStore|DEGRADED|embedding'

# Test SearXNG
curl -s "http://localhost:8080/search?q=test&format=json" | python3 -m json.tool | head -20

# Test ChromaDB
curl -s http://localhost:8100/api/v1/heartbeat

# Test ntfy
curl -s http://localhost:8091/v1/health

# Test Ollama from inside Docker
docker compose exec odysseus curl -s http://host.docker.internal:11434/api/tags

# Check GPU passthrough
docker compose exec odysseus nvidia-smi -L
# or
docker compose exec odysseus rocm-smi

# Rebuild everything from scratch
docker compose down -v
docker compose up -d --build
```

---

## Still Stuck?

1. Check the logs first: `docker compose logs --tail=200 odysseus`
2. Search the issue tracker: https://github.com/pewdiepie-archdaemon/odysseus/issues
3. Open a new issue with:
   - What you expected to happen
   - What actually happened
   - Output of `docker compose ps` and the relevant log snippet
   - Your `.env` (redact API keys/passwords)
