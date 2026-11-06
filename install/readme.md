# Odysseus Windows Installer

Two small batch scripts that install and run [Odysseus](https://github.com/odysseus-dev/odysseus), a self-hosted AI workspace (chat, agents, research, documents, email, notes, calendar and local model workflows), natively on Windows. No Docker needed.

| File | Purpose |
| --- | --- |
| `setup.bat` | One-time installer. Clones Odysseus, creates a Python virtual environment, installs dependencies, runs first-time setup and launches the app. Safe to re-run. |
| `start.bat` | Everyday launcher. Starts the server and opens your browser. |

> These scripts are an unofficial convenience wrapper. Odysseus itself is developed at [odysseus-dev/odysseus](https://github.com/odysseus-dev/odysseus) and is licensed under AGPL-3.0-or-later.

---

## Requirements

- **Windows 10 or 11**
- **[Git for Windows](https://git-scm.com/download/win)**. Also provides `bash.exe`, which Odysseus uses for Cookbook model downloads and the agent shell tool.
- **[Python 3.11 or newer](https://www.python.org/downloads/)**. Tick **"Add python.exe to PATH"** during install, or make sure the `py` launcher is installed. The script tries 3.12, 3.11 and 3.13 first, then falls back to `python`.
- An internet connection (the first install downloads a fair number of Python packages).

---

## Quick start

1. Download or clone this repository and keep `setup.bat` and `start.bat` in the same folder.
2. Double-click **`setup.bat`**.
3. Watch the console for the **temporary admin password** printed near the end of setup.
4. After 5 seconds Odysseus launches and your browser opens at <http://localhost:7000>.
5. Log in as `admin` with the temporary password, then change it under **Settings**.

From then on, just double-click **`start.bat`** to run Odysseus.

---

## What `setup.bat` does

1. **Checks prerequisites:** Git and Python 3.11+. If either is missing, it explains where to get it and stops.
2. **Clones the repo** into an `odysseus` folder next to the script. If the folder already exists, it runs `git pull` instead.
3. **Creates `.env`** from `.env.example` if you don't have one yet.
4. **Creates a virtual environment** (`odysseus\venv`) and installs `requirements.txt`.
5. **Runs `setup.py`**, which creates the `admin` account. Its output is shown on screen and appended to the log.
6. **Launches `start.bat`**, if it is present next to `setup.bat`.

Everything is logged to `odysseus_setup.log` next to the script.

> The log file contains the temporary admin password. Delete it or keep it private once you've changed the password.

### Options

Open `setup.bat` in a text editor and change these lines near the top:

| Setting | Default | Meaning |
| --- | --- | --- |
| `BRANCH` | *(empty)* | Empty uses the repo's default branch, `dev`, which has the newest changes but may be unstable. Set `BRANCH=main` for the more curated, stable branch. |
| `INSTALL_OPTIONAL` | `0` | Set to `1` to also install `requirements-optional.txt`: local speech-to-text, DuckDuckGo search, PDF viewer (PyMuPDF, AGPL) and Office/EPUB text extraction. |
| `PROJECT` | `odysseus` | Name of the install folder. If you change it, change `APP` in `start.bat` too. |

---

## Using Odysseus

- **URL:** <http://localhost:7000>
- **Stop the server:** press `Ctrl+C` in the server window.
- **Configure models, search and email:** inside the app under **Settings**. You only need to edit `.env` for deployment-level overrides such as `AUTH_ENABLED` or `DATABASE_URL`.
- **Local models:** the easiest option on Windows is [Ollama](https://ollama.com/download). Add `http://localhost:11434/v1` as an endpoint in Settings.
- **Where your data lives:** `odysseus\data\` (database, memory, uploads, settings). Back this folder up before updating or deleting anything.

### Changing the port

Edit `PORT` at the top of `start.bat` (default `7000`).

### Updating

Run `setup.bat` again. It pulls the latest code and reinstalls dependencies without touching your `data` folder or `.env`.

### Uninstalling

Delete the `odysseus` folder (back up `odysseus\data` first if you want to keep your data).

---

## Security notes

By default the server listens on `127.0.0.1` only, so it is reachable from your own machine but not from the network.

If you intentionally want LAN or Tailscale access, change `--host 127.0.0.1` to `--host 0.0.0.0` in `start.bat`, and:

- keep `AUTH_ENABLED=true` and `LOCALHOST_BYPASS=false` in `.env`
- never expose the port directly to the public internet; use HTTPS behind a trusted reverse proxy or private network
- note that the browser clipboard (copy buttons) only works over HTTPS or `localhost`

Odysseus has powerful local tools (shell access, file uploads, model downloads), so treat it like an admin console. See the upstream [security notes](https://github.com/odysseus-dev/odysseus/blob/dev/website/setup.md#security-notes) for details.

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| **"Git was not found"** | Install [Git for Windows](https://git-scm.com/download/win), then open a new terminal and re-run `setup.bat`. |
| **"Python 3.11 or newer was not found"** | Install Python 3.12 and tick *Add python.exe to PATH*, or install the `py` launcher. Older Python versions won't work. |
| **`pip install` fails** | Open `odysseus_setup.log` and look at the last error. Re-running `setup.bat` after fixing the cause (network, antivirus, missing build tools) is safe. |
| **Port 7000 already in use** | Change `PORT` in `start.bat`, or stop whatever is using the port. |
| **`AUTH_ENABLED=false` seems ignored** | Notepad may have saved `.env` with a UTF-8 BOM. Re-save it as *UTF-8 without BOM* (for example in VS Code). |
| **Lost the admin password** | Check `odysseus_setup.log`. If it's gone, see the upstream [setup guide](https://github.com/odysseus-dev/odysseus/blob/dev/website/setup.md) or open an issue in the Odysseus repo. |
| **Browser tools (Playwright MCP) not available** | Optional. Run `npx -y @playwright/mcp@latest --version` once (needs Node.js, about 300 MB), then restart Odysseus. |

---

## Folder layout after setup

```
your-folder/
├── setup.bat
├── start.bat
├── odysseus_setup.log
└── odysseus/            # cloned repository
    ├── venv/            # Python virtual environment
    ├── data/            # your data (database, uploads, settings)
    ├── .env             # local configuration
    └── app.py ...
```

---

## Links

- Odysseus repository: <https://github.com/odysseus-dev/odysseus>
- Upstream setup guide: <https://github.com/odysseus-dev/odysseus/blob/dev/website/setup.md>
- Odysseus landing page: <https://odysseus-dev.github.io/odysseus/>
