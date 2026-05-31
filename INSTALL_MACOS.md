# Installing Odysseus on macOS

This guide covers a clean install on macOS (Apple Silicon and Intel). It documents the issues you will hit and how to fix them.

---

## Requirements

- macOS 11 or later
- [Homebrew](https://brew.sh) — if you don't have it:
  ```bash
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  ```

---

## Step 1 — Install system dependencies

```bash
brew install tmux python@3.11
```

> **Why Python 3.11 specifically?**  
> macOS ships with Python 3.9, which is too old — the `mcp` package requires 3.10+, and there is an f-string syntax restriction in 3.11 that affects one route (fixed in this repo). Use 3.11.

---

## Step 2 — Clone the repo

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus
cd odysseus
```

---

## Step 3 — Create a virtual environment with Python 3.11

```bash
python3.11 -m venv venv
source venv/bin/activate
```

> **Important:** Do not use `python3` here — on macOS that resolves to the system 3.9. Use `python3.11` explicitly.

---

## Step 4 — Install dependencies

```bash
pip install -r requirements.txt
```

This installs ~60 packages including FastAPI, SQLAlchemy, fastembed, and the MCP SDK. Expect it to take 1-2 minutes.

---

## Step 5 — Run setup

```bash
python setup.py
```

This creates the `data/` directories, initialises the database, and prints a temporary admin password. **Save it.**

Example output:
```
[ok] Initial admin user created (admin)
      Temporary password: XXXXXXXXXXXXXXXXXXXX
```

---

## Step 6 — Start the server

```bash
uvicorn app:app --host 0.0.0.0 --port 7001
```

> **Why port 7001?**  
> On macOS, port 7000 is reserved by Control Centre (`ControlCenter` process). Using 7000 will give `[Errno 48] address already in use`. Use 7001 or any other free port.

Then open **http://localhost:7001** in your browser and log in with:
- Username: `admin`
- Password: the one printed by `setup.py`

---

## Step 7 — Connect an AI model

Odysseus needs an AI backend to chat. Two options:

### Option A — Local models with Ollama (free, no API key)

1. Install Ollama: https://ollama.com/download
2. Pull a model, e.g.: `ollama pull qwen3:8b`
3. In Odysseus: click the **gear icon (bottom-left) → Admin → Add Models**
4. Enter `http://localhost:11434/v1` as the URL — note the `/v1`, this is required
5. Leave API key blank, click **Add**

> Ollama's OpenAI-compatible endpoint is at `/v1`. Using `http://localhost:11434` alone will fail because Odysseus probes `/models`, which 404s on plain Ollama. The `/v1/models` path works.

### Option B — Cloud API (OpenRouter, OpenAI, Anthropic, etc.)

1. Get an API key from your provider (e.g. https://openrouter.ai)
2. In Odysseus: Admin → Add Models → select your provider or enter the base URL
3. Paste your API key and click **Add**

---

## Install as a desktop app (PWA)

Odysseus ships a PWA manifest so you can install it as a standalone app with a dock icon:

**Chrome / Brave:**
1. Open `http://localhost:7001`
2. Click `⋮` menu → **Cast, save and share** → **Install page as app**

**Safari:**
1. Open `http://localhost:7001`
2. Click the Share button → **Add to Dock**

The server (`uvicorn`) must be running for the app to work.

---

## Auto-start on login (optional but recommended)

By default you need to run `uvicorn` in a terminal every time you reboot. To have the server start automatically on login with no terminal needed:

**1. Create a venv outside TCC-restricted folders**

macOS launchd cannot access `~/Desktop` or `~/Documents` due to sandbox restrictions. Create a separate venv in an unrestricted location:

```bash
python3.11 -m venv ~/.odysseus-venv
~/.odysseus-venv/bin/pip install -r /path/to/odysseus/requirements.txt
```

**2. Create a startup script** — save as `~/Library/LaunchAgents/odysseus-start.sh`:

```bash
#!/bin/bash
export VIRTUAL_ENV="$HOME/.odysseus-venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"
export PYTHONHOME=""
cd /path/to/odysseus
exec $HOME/.odysseus-venv/bin/python3 -m uvicorn app:app --host 0.0.0.0 --port 7001
```

Make it executable:
```bash
chmod +x ~/Library/LaunchAgents/odysseus-start.sh
```

**3. Create a launch agent** — save as `~/Library/LaunchAgents/com.odysseus.server.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.odysseus.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/YOUR_USERNAME/Library/LaunchAgents/odysseus-start.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/Library/Logs/odysseus.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/Library/Logs/odysseus.error.log</string>
</dict>
</plist>
```

Replace `YOUR_USERNAME` with your macOS username (run `whoami` to check).

**4. Load it:**
```bash
launchctl load ~/Library/LaunchAgents/com.odysseus.server.plist
```

The server now starts at login and restarts automatically if it crashes.

To stop it: `launchctl unload ~/Library/LaunchAgents/com.odysseus.server.plist`  
To check logs: `tail -f ~/Library/Logs/odysseus.log`

---

## Known issues on macOS

| Issue | Cause | Fix |
|-------|-------|-----|
| `mcp` package not found | Python 3.9 (system default) | Use `python3.11 -m venv venv` |
| `SyntaxError: f-string expression part cannot include a backslash` | Python 3.11 restriction in `calendar_routes.py` | Fixed in this repo |
| `[Errno 48] address already in use` on port 7000 | macOS Control Centre owns port 7000 | Use `--port 7001` |
| `MemoryVectorStore DEGRADED: ChromaDB unavailable` | ChromaDB server not running | Warning only — app works fine without it |
| `deactivate: command not found` | Ran `deactivate` outside an active venv | Safe to ignore |
| Ollama "Request failed" in Settings | Wrong URL format | Use `http://localhost:11434/v1` not `http://localhost:11434` |
| Launch agent `PermissionError: pyvenv.cfg` | launchd blocked from Desktop/Documents by TCC | Create venv at `~/.odysseus-venv` — see auto-start section |
| `Built-in: Browser MCP` fails on auto-start | `npx` not in launchd PATH | `brew install node` then reload the launch agent |

---

## Restarting after a reboot

**If you set up auto-start:** nothing to do — open http://localhost:7001 directly. The server starts automatically on login.

**If running manually:**

```bash
cd /path/to/odysseus
source venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 7001
```

Then open http://localhost:7001.
