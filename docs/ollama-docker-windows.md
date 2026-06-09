# Connecting Ollama to Odysseus on Windows (Docker)

When running Odysseus via Docker on Windows, connecting a local Ollama instance
requires a few extra steps that aren't needed on native installs. This guide
covers the exact setup so you don't hit the common pitfalls.

---

## Why this is different from native installs

On a native (non-Docker) install, Odysseus and Ollama both run directly on your
machine and share `localhost`. On Docker, Odysseus runs inside a container —
it has its own isolated network and **cannot reach your machine's `localhost`**.

This means two things need to change on the Ollama side:

1. Ollama must listen on all interfaces, not just `127.0.0.1`
2. Ollama must allow requests from origins outside localhost

---

## Step 1 — Start Ollama with the correct environment variables

Do **not** run plain `ollama serve`. Instead, run:

**PowerShell:**
```powershell
$env:OLLAMA_HOST="0.0.0.0:11434"; $env:OLLAMA_ORIGINS="*"; ollama serve
```

**Command Prompt:**
```cmd
set OLLAMA_HOST=0.0.0.0:11434 && set OLLAMA_ORIGINS=* && ollama serve
```

Keep this terminal open. Ollama must stay running while you use Odysseus.

> **What these do:**
> - `OLLAMA_HOST=0.0.0.0:11434` — tells Ollama to accept connections from any
>   network interface, not just localhost
> - `OLLAMA_ORIGINS=*` — allows cross-origin requests from Docker containers

---

## Step 2 — Find your machine's local IP address

Run this in PowerShell:

```powershell
ipconfig
```

Look for **Wireless LAN adapter Wi-Fi** (or your active Ethernet adapter) and
copy the **IPv4 Address** — it will look like `192.168.x.x`.

> **Do not use** `172.x.x.x` addresses — these are Docker's internal bridge
> network IPs and are not stable. Always use your Wi-Fi or Ethernet IPv4.

---

## Step 3 — Add the endpoint in Odysseus

1. Open Odysseus at `http://localhost:7000`
2. Go to **Settings → Add Models → LOCAL**
3. Enter your endpoint:
   ```
   http://192.168.x.x:11434
   ```
   Replace `192.168.x.x` with the IP you found in Step 2.
4. Click **Add**. The endpoint should show **online** with your models listed.

---

## Troubleshooting

### Endpoint shows "offline"
- Make sure Ollama is running with the correct env vars from Step 1
- Double-check the IP — use your Wi-Fi IPv4, not a `172.x.x.x` address
- Make sure Windows Firewall isn't blocking port `11434`

### Chat returns "Error 503"
This means Odysseus reached the endpoint but got no response from Ollama.
- Kill and restart Ollama with the env vars from Step 1
- Verify the endpoint in Settings is correct and shows "online"

### Models not appearing after adding endpoint
- Click the endpoint row to expand it and check if models are enabled
- Run `ollama list` in a separate terminal to confirm models are pulled locally

### Ollama keeps reverting to localhost after restart
The env vars set with `$env:` in PowerShell only last for that session.
To make them permanent, add them to your system environment variables:

1. Search **"Edit environment variables"** in the Windows Start menu
2. Under **User variables**, add:
   - `OLLAMA_HOST` = `0.0.0.0:11434`
   - `OLLAMA_ORIGINS` = `*`
3. Restart Ollama

---

## Quick reference

| What you need | Value |
|---|---|
| Ollama host binding | `0.0.0.0:11434` |
| Ollama origins | `*` |
| Odysseus endpoint | `http://<your-wifi-ip>:11434` |
| Find your IP | `ipconfig` → Wi-Fi IPv4 Address |
