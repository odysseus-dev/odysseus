# Updating Odysseus

Odysseus is still early in its release lifecycle. For now, updates are manual.

In-app update status is **check-only** only. The status card in `Settings > System`
shows observed version/commit state and manual commands, but it does not download,
apply, or restart automatically.

## Current recommendation: source-based installs

If you installed from source (Docker, Linux/macOS, or Windows), treat updates as
`git`-driven refreshes.

### 1) Source Docker install (recommended today)

From your checkout:

```bash
cd /path/to/odysseus
git fetch --prune
git pull --ff-only
docker compose up -d --build
```

Use `docker compose logs --tail=120 odysseus` to confirm the container restarted
with the new commit.

### 2) Native source installs (Linux and macOS)

Use the same checkout workflow from your native terminal:

```bash
cd /path/to/odysseus
git pull --ff-only
python -m pip install -r requirements.txt
python setup.py
```

Restart your process (`start-macos.sh`, your service manager, or your own
`uvicorn` command) so the updated code is loaded.

### 3) Native Windows source update

From PowerShell in your checkout:

```powershell
Set-Location C:\path\to\odysseus
git pull --ff-only
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
```

If you need a fully manual flow, run `git pull --ff-only`, update dependencies
(`python -m pip install -r requirements.txt`), and restart `python -m uvicorn ...`
with your normal launch command.

## Future/alternate release channels

### 4) Prebuilt Docker image (future)

A release-image path is not the primary flow for this PR scope. When official image
tags are published, updates should follow:

```bash
docker compose pull
docker compose up -d
```

Watch your compose file for the correct image tag/repository at that time.

### 5) Windows portable/desktop releases

Desktop-focused release artifacts (portable zip / installer) should be handled as
a separate channel if they are published. For that style of release, the update
flow is usually:

- Download the latest released package from GitHub Releases.
- Stop the running app, then replace the app bundle/folder and start the new version.
- Preserve your `data/` directory and local config when replacing files.

### 6) Package-manager wrappers (later)

Package-manager installers (for example Homebrew, Scoop, Chocolatey, etc.) are
outside the supported baseline today. They are expected to wrap one of the
channels above and still require manual update commands after installation.

## Branch and mode notes

For source installs, stick to a branch you track (typically `main`) to keep
`git pull --ff-only` behavior predictable. If you are on a detached HEAD or a
custom branch, follow your normal workflow for review and updates instead of
running a blind pull.

If unsure which path you are on, use the Settings `System` check view to verify
the current mode before running an update.
