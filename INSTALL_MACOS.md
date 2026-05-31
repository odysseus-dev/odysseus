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

## Known issues on macOS

| Issue | Cause | Fix |
|-------|-------|-----|
| `mcp` package not found | Python 3.9 (system default) | Use `python3.11 -m venv venv` |
| `SyntaxError: f-string expression part cannot include a backslash` | Python 3.11 restriction in `calendar_routes.py` | Fixed in this repo |
| `[Errno 48] address already in use` on port 7000 | macOS Control Centre owns port 7000 | Use `--port 7001` |
| `MemoryVectorStore DEGRADED: ChromaDB unavailable` | ChromaDB server not running | Warning only — app works fine without it |
| `deactivate: command not found` | Ran `deactivate` outside an active venv | Safe to ignore |

---

## Restarting after a reboot

Each time you want to use Odysseus:

```bash
cd /path/to/odysseus
source venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 7001
```

Then open http://localhost:7001.
