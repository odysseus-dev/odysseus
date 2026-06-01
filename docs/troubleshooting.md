# Odysseus Troubleshooting Cookbook

Quick fixes for common self-hosting gotchas. These are the "weird 30-second fixes" that otherwise become 30-minute searches.

---

## Table of Contents

- [Login & Authentication](#login--authentication)
- [Email Integration](#email-integration)
- [Push Notifications (ntfy)](#push-notifications-ntfy)
- [Calendar (Radicale)](#calendar-radicale)
- [Web Search (SearXNG)](#web-search-searxng)
- [Database & Memory](#database--memory)
- [Docker & Networking](#docker--networking)
- [Model Providers](#model-providers)

---

## Login & Authentication

### "Which credentials do I use to log in?"

**Symptom:** Fresh install shows login page but you don't know the password.

**Solution:** The temporary admin password is printed in the terminal during setup:

```
5. Creating initial admin...
  [ok] Initial admin user created (admin)
        Temporary password: T**************
        ** Change it after first login. **
```

**If you missed it:**

1. Check your Docker logs:
   ```bash
   docker logs odysseus 2>&1 | grep "Temporary password"
   ```

2. Or reset by deleting the auth file and restarting:
   ```bash
   docker stop odysseus
   rm ./data/auth.json
   docker start odysseus
   # Check terminal for new temporary password
   ```

**Prevention:** Set `ODYSSEUS_ADMIN_PASSWORD` in your `.env` before first run to choose your own password.

---

## Email Integration

### Dovecot "cleartext auth" error (local stacks)

**Symptom:** Email integration fails with `authentication failed` or `PLAIN auth not allowed`.

**Cause:** Dovecot blocks cleartext authentication by default on non-SSL connections.

**Solution:** Edit your Dovecot config (`/etc/dovecot/conf.d/10-auth.conf`):

```ini
disable_plaintext_auth = no
auth_mechanisms = plain login
```

Then restart Dovecot:
```bash
sudo systemctl restart dovecot
```

**For Docker-based mail stacks:** Add to your `docker-compose.yml`:
```yaml
environment:
  - DOVECOT_DISABLE_PLAINTEXT_AUTH=no
```

---

### SMTP connection test fails

**Symptom:** Health check shows email as "unavailable" or integration won't save.

**Debug steps:**

1. Test SMTP connectivity manually:
   ```bash
   openssl s_client -connect smtp.example.com:587 -starttls smtp
   # Or for plain SMTP:
   telnet smtp.example.com 587
   ```

2. Verify EHLO response:
   ```
   EHLO localhost
   # Should list AUTH PLAIN LOGIN capabilities
   ```

3. Check firewall rules:
   ```bash
   sudo ufw status | grep 587
   ```

---

## Push Notifications (ntfy)

### ntfy.sh works but self-hosted ntfy doesn't

**Symptom:** Notifications work with `ntfy.sh` but fail with your own server.

**Solution:** Android Instant Delivery requires Firebase. For self-hosted ntfy:

1. **Option A:** Use polling (works without Firebase):
   - In Odysseus settings, set ntfy URL to your server
   - Notifications will poll every 30s (no instant push)

2. **Option B:** Configure Firebase for your ntfy server:
   - Follow [ntfy Firebase setup](https://docs.ntfy.sh/publish/#firebase)
   - Add `firebase.json` with your credentials
   - Restart ntfy service

**Quick test:**
```bash
curl -d "Test from Odysseus" https://your-ntfy-server.com/odysseus-test
# Check if notification arrives
```

---

### ntfy notifications arrive but don't show content

**Symptom:** You get notified but message body is empty.

**Cause:** ntfy server has `default_disallow_text` enabled.

**Solution:** Edit ntfy config (`/etc/ntfy/server.yml`):
```yaml
default_disallow_text: false
```

Or allow specific topics:
```yaml
access-control:
  - topic: odysseus-alerts
    user: "*"
    allow: [publish, publish:match:^odysseus-.*]
```

---

## Calendar (Radicale)

### "Invalid collection URL" error

**Symptom:** Calendar integration fails with "collection not found" or "invalid URL".

**Cause:** Radicale collection URLs are case-sensitive and path-structured.

**Correct URL format:**
```
http://localhost:5232/USERNAME/CALENDAR_NAME/
# Example:
http://localhost:5232/admin/personal/
http://localhost:5232/admin/work/
```

**Find your collections:**
```bash
curl http://localhost:5232/
# Lists all available collections
```

**Create a new collection:**
```bash
# Create via Radicale web UI at http://localhost:5232/
# Or via curl:
curl -X MKCOL http://localhost:5232/admin/new-calendar/
```

**Common mistakes:**
- ❌ `http://localhost:5232/admin` (missing collection name)
- ❌ `http://localhost:5232/ADMIN/personal/` (wrong case)
- ✅ `http://localhost:5232/admin/personal/` (correct)

---

## Web Search (SearXNG)

### Google results fail with `IndexError`

**Symptom:** SearXNG returns errors for Google searches, logs show `IndexError: list index out of range`.

**Cause:** Google frequently changes HTML structure, breaking SearXNG's parser.

**Solution:** Disable Google engines, use stable alternatives.

Edit `config/searxng/settings.yml`:
```yaml
engines:
  - name: google
    disabled: true
  - name: google scholar
    disabled: true
  - name: google news
    disabled: true
  - name: google images
    disabled: true
  - name: google videos
    disabled: true
  
  # Enable stable alternatives:
  - name: bing
    disabled: false
  - name: mojeek
    disabled: false
  - name: presearch
    disabled: false
  - name: duckduckgo
    disabled: false
  - name: startpage
    disabled: false
  - name: qwant
    disabled: false
```

Restart SearXNG:
```bash
docker restart odysseus-searxng
# Or if running standalone:
sudo systemctl restart searxng
```

---

### SearXNG returns no results

**Symptom:** All searches return empty results.

**Debug steps:**

1. Check SearXNG health:
   ```bash
   curl http://localhost:8080/healthz
   # Should return "OK"
   ```

2. Test engine directly:
   ```bash
   curl "http://localhost:8080/search?q=test&format=json"
   ```

3. Check rate limiting:
   - Some engines rate-limit SearXNG IPs
   - Add delays in `settings.yml`:
     ```yaml
     server:
       request_timeout: 5.0
       max_request_timeout: 10.0
     ```

4. Verify Redis connection (if using caching):
   ```bash
   redis-cli ping
   # Should return "PONG"
   ```

---

## Database & Memory

### ChromaDB connection fails

**Symptom:** Health check shows ChromaDB as "unavailable".

**Solution:**

1. Check if ChromaDB is running:
   ```bash
   docker ps | grep chroma
   # Or: systemctl status chromadb
   ```

2. Test connectivity:
   ```bash
   curl http://localhost:8000/api/v1/heartbeat
   # Should return JSON with uptime
   ```

3. Reset ChromaDB (last resort — **back up first!**):
   ```bash
   docker stop odysseus-chroma
   rm -rf ./data/chroma/*
   docker start odysseus-chroma
   ```

---

### SQLite database locked

**Symptom:** Errors like `database is locked` or `unable to open database file`.

**Cause:** Multiple processes accessing the same SQLite file.

**Solution:**

1. Find and kill lock holders:
   ```bash
   lsof ./data/odysseus.db
   # Kill the PIDs shown
   ```

2. Enable WAL mode (prevents future locks):
   ```sql
   sqlite3 ./data/odysseus.db "PRAGMA journal_mode=WAL;"
   ```

3. Reduce concurrent writes in Odysseus config:
   ```yaml
   database:
     max_connections: 1
   ```

---

## Docker & Networking

### Container can't reach host services

**Symptom:** Odysseus container can't connect to `localhost:5432` (Postgres), `localhost:6379` (Redis), etc.

**Cause:** `localhost` inside container refers to the container itself, not the host.

**Solutions:**

**Option A:** Use host networking (Linux only):
```yaml
# docker-compose.yml
services:
  odysseus:
    network_mode: host
```

**Option B:** Use special DNS name:
- Linux: `host.docker.internal` (requires `extra_hosts`)
- macOS/Windows: `host.docker.internal` (built-in)

```yaml
services:
  odysseus:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    # Then connect to host.docker.internal:5432
```

**Option C:** Put services in same Docker network:
```yaml
services:
  odysseus:
    networks:
      - odysseus-net
  postgres:
    networks:
      - odysseus-net

networks:
  odysseus-net:
    driver: bridge
```

---

### Tailscale URLs don't work in clipboard/copy

**Symptom:** Copying plain-HTTP Tailscale URLs fails or links don't open.

**Cause:** Modern browsers block mixed content and insecure clipboard writes.

**Solution:**

1. **Use HTTPS:** Set up Tailscale HTTPS:
   ```bash
   tailscale serve https / http://localhost:7000
   # Then access via https://your-machine.tailnet-name.ts.net
   ```

2. **Or allow insecure localhost:** Chrome/Edge flag:
   ```
   chrome://flags/#allow-insecure-localhost
   ```

3. **Or use MagicDNS:** Access via `.ts.net` domain which gets auto-HTTPS.

---

## Model Providers

### "Provider setup fails" for Anthropic/Gemini/OpenAI

**Symptom:** Provider test connection fails even with valid API key.

**Debug steps:**

1. **Test API key directly:**
   ```bash
   # Anthropic:
   curl https://api.anthropic.com/v1/models \
     -H "x-api-key: YOUR_KEY" \
     -H "anthropic-version: 2023-06-01"
   
   # OpenAI:
   curl https://api.openai.com/v1/models \
     -H "Authorization: Bearer YOUR_KEY"
   ```

2. **Check rate limits:**
   - Free tier keys may have low limits
   - Check provider dashboard for quota status

3. **Verify network access:**
   - Some countries/providers block AI APIs
   - Try via proxy if needed

4. **For OpenRouter:**
   - Ensure key has correct permissions
   - Check model availability in your region

---

### Local models (Ollama/vLLM) not detected

**Symptom:** Local provider shows "unavailable" or models don't list.

**Solution:**

1. **Test Ollama:**
   ```bash
   curl http://localhost:11434/api/tags
   # Should list available models
   ```

2. **Test vLLM:**
   ```bash
   curl http://localhost:8000/v1/models
   # Should return model list
   ```

3. **Docker networking:** If Ollama/vLLM runs on host, Odysseus container needs access:
   ```yaml
   # docker-compose.yml
   services:
     odysseus:
       extra_hosts:
         - "host.docker.internal:host-gateway"
       environment:
         - OLLAMA_HOST=http://host.docker.internal:11434
   ```

4. **Pull a model if none exist:**
   ```bash
   ollama pull llama3.2:3b
   # Or any model that fits your VRAM
   ```

---

## General Debugging

### Enable verbose logging

Add to `.env`:
```bash
LOG_LEVEL=DEBUG
ODYSSEUS_DEBUG=true
```

Then restart and check logs:
```bash
docker logs -f odysseus 2>&1 | grep -i error
```

---

### Health check endpoint

Use the built-in health endpoint to diagnose service status:

```bash
curl http://localhost:7000/api/health | jq .
```

Response format:
```json
{
  "status": "degraded",
  "services": {
    "chromadb": {"status": "healthy"},
    "searxng": {"status": "unavailable"},
    "email": {"status": "not_configured"},
    "database": {"status": "healthy"}
  }
}
```

---

### Reset everything (nuclear option)

**⚠️ Warning:** This deletes all data, memories, and settings.

```bash
docker stop odysseus
rm -rf ./data/*
# Recreate .env with your settings
docker start odysseus
# Check terminal for new admin password
```

---

## Still Stuck?

1. Check existing issues: https://github.com/pewdiepie-archdaemon/odysseus/issues
2. Search discussions: https://github.com/pewdiepie-archdaemon/odysseus/discussions
3. Open a new issue with:
   - Odysseus version (`/api/version`)
   - Docker logs (last 100 lines)
   - Health check output
   - What you've already tried
