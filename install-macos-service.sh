#!/usr/bin/env bash
# install-macos-service.sh — install Odysseus as a per-user LaunchAgent
# so it auto-starts at login and survives crashes.
#
#   ~/Library/LaunchAgents/com.odysseus.ui.plist
#   /Users/USER/Library/Logs/Odysseus/{stdout,stderr}.log
#
# Run via:
#   ./odysseus.sh --install-service
# or directly:
#   ./install-macos-service.sh
#
# Idempotent: re-running with the same path updates the plist in place
# and reloads the agent.

set -e

# ── Resolve paths ──────────────────────────────────────────────────────────
# Follow symlinks so that `brew install` symlinks (in /opt/homebrew/bin)
# resolve back to the real libexec dir. Without this, the plist would
# bake in the symlink path and break the moment Homebrew updates the
# keg.
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  DIR=$(cd -P "$(dirname "$SOURCE")" && pwd)
  SOURCE=$(readlink "$SOURCE")
  [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
# Honor ODYSSEUS_REPO_DIR if set — useful when the user has symlinked
# install-macos-service.sh into a different location (e.g. a non-TCC
# wrapper script).
REPO_DIR="${ODYSSEUS_REPO_DIR:-$SCRIPT_DIR}"

LABEL="com.odysseus.ui"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/Odysseus"
STDOUT_LOG="$LOG_DIR/stdout.log"
STDERR_LOG="$LOG_DIR/stderr.log"

# Make sure we never trash an existing plist from a different repo path.
EXISTING_PROG=""
EXISTING_CWD=""
if [ -f "$PLIST_PATH" ]; then
  EXISTING_PROG=$(/usr/libexec/PlistBuddy -c "Print :ProgramArguments:0" "$PLIST_PATH" 2>/dev/null || true)
  EXISTING_CWD=$(/usr/libexec/PlistBuddy -c "Print :WorkingDirectory" "$PLIST_PATH" 2>/dev/null || true)
fi

# Sanity: the path the launcher is going to call must exist, and Python 3.11+
# must be reachable inside that environment. We don't try to *create* the
# venv here — that's the launcher's job. We just fail loud if the venv is
# missing so the user knows to run `odysseus --launch=native` first.
if [ ! -x "$REPO_DIR/odysseus.sh" ]; then
  echo "✗ odysseus.sh not found at $REPO_DIR/odysseus.sh" >&2
  exit 1
fi

# ── TCC path check ────────────────────────────────────────────────────────
# macOS's Transparency, Consent, and Control (TCC) framework restricts
# what launchd can access when it runs a LaunchAgent. Files under
# ~/Desktop/, ~/Documents/, and ~/Downloads/ are in TCC's "user data"
# set, and launchd will get "Operation not permitted" when it tries to
# exec a shell script living there. (A Terminal session has TCC consent
# for these folders; the launchd process does not.) This means a repo
# cloned into Desktop/ or Documents/ will install the plist fine but
# the agent will refuse to spawn.
#
# The right answer is to keep the repo outside those folders — `~/odysseus`
# is the convention. Detect and fail with a one-liner fix.
case "$REPO_DIR" in
  "$HOME"/Desktop/*|"$HOME"/Documents/*|"$HOME"/Downloads/*)
    echo "✗ Repo path is under a TCC-protected folder: $REPO_DIR" >&2
    echo "" >&2
    echo "  launchd cannot execute scripts under ~/Desktop, ~/Documents," >&2
    echo "  or ~/Downloads (macOS file-vault consent). The agent will" >&2
    echo "  install but refuse to start." >&2
    echo "" >&2
    echo "  Move the repo out of the protected folder and re-run:" >&2
    echo "" >&2
    echo "    mv \"$REPO_DIR\" \"$HOME/odysseus\"" >&2
    echo "    cd ~/odysseus" >&2
    echo "    ./odysseus.sh --install-service" >&2
    echo "" >&2
    echo "  Or set ODYSSEUS_REPO_DIR= to a non-protected path." >&2
    exit 1
    ;;
esac

# ── Detect brew prefix for the embedded PATH ──────────────────────────────
# launchd's default PATH is /usr/bin:/bin — no /opt/homebrew/bin, no
# /usr/local/bin. The native launcher needs brew's python3.11 and the
# `open` command lives in /usr/bin so that's fine, but `git` (used by
# --update) and a few other tools need to be on PATH inside the agent.
BREW_PREFIX=""
if [ -x /opt/homebrew/bin/brew ]; then
  BREW_PREFIX="/opt/homebrew"
elif [ -x /usr/local/bin/brew ]; then
  BREW_PREFIX="/usr/local"
fi
LAUNCHD_PATH="$BREW_PREFIX/bin:/usr/local/bin:/usr/bin:/bin"

# ── Generate the plist ────────────────────────────────────────────────────
mkdir -p "$(dirname "$PLIST_PATH")"
mkdir -p "$LOG_DIR"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${REPO_DIR}/odysseus.sh</string>
        <string>--launch=native</string>
        <string>--no-open</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>

    <!-- Run at user login (and on demand via launchctl start). -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Respawn on crash, but not on a clean exit. SuccessfulExit=false
         means: relaunch on anything other than exit code 0. Crashed=true
         is a synonym for "exit code != 0" but we keep both for clarity. -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
        <key>Crashed</key>
        <true/>
    </dict>

    <!-- Don't spin in a tight crash loop. -->
    <key>ThrottleInterval</key>
    <integer>10</integer>

    <!-- Logs. We append rather than truncate so the user can post-mortem. -->
    <key>StandardOutPath</key>
    <string>${STDOUT_LOG}</string>
    <key>StandardErrorPath</key>
    <string>${STDERR_LOG}</string>

    <!-- Interactive = share the user's GUI session, so anything that needs
         the keychain (e.g. secrets fetched from Keychain) works. -->
    <key>ProcessType</key>
    <string>Interactive</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${LAUNCHD_PATH}</string>
        <key>ODYSSEUS_SERVICE_MODE</key>
        <string>launchd</string>
    </dict>
</dict>
</plist>
PLIST

# ── Validate the plist is well-formed ─────────────────────────────────────
if ! plutil -lint "$PLIST_PATH" >/dev/null 2>&1; then
  echo "✗ plist failed plutil -lint; not loading." >&2
  plutil -lint "$PLIST_PATH" >&2 || true
  exit 1
fi

# ── Load (or reload) the agent ────────────────────────────────────────────
# launchctl bootstrap is the modern API (macOS 10.11+); it cleanly handles
# "already loaded" by unloading first.
DOMAIN="gui/$(id -u)"

# Clear any "disabled" override from a previous install. The per-user
# launchd override db (~/Library/Preferences/com.apple.launchd.<UID>.plist
# — or the in-memory version of it) can hold a `Disabled = true` entry
# left behind by a prior --uninstall-service, a `launchctl disable` call,
# or a botched install. If that entry is set, bootstrap will succeed
# but the agent will refuse to spawn, surfacing as a silent
# "Bootstrap failed: 5: Input/output error" on the *next* install attempt.
# Always clear it before bootstrapping.
launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl enable "system/$LABEL" 2>/dev/null || true

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  echo "▶ unloading existing agent (different path or update)…"
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
fi

# Also nuke any old-style "loaded into the system domain" copy of the
# same label — defensive, in case someone installed it manually with
# `launchctl load -w` before this script existed.
launchctl bootout "system/$LABEL" 2>/dev/null || true

echo "▶ loading $LABEL into $DOMAIN…"
if ! launchctl bootstrap "$DOMAIN" "$PLIST_PATH" 2>&1; then
  echo "✗ launchctl bootstrap failed." >&2
  exit 1
fi

# Re-enable in case the bootstrap above flipped the bit (it shouldn't,
# but be defensive). enable is idempotent.
launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true

# Kick it once now so the user can see it's working without re-logging in.
launchctl kickstart -k "$DOMAIN/$LABEL" 2>/dev/null || true

echo
echo "✓ Odysseus auto-start installed."
echo "  plist:  $PLIST_PATH"
echo "  logs:   $LOG_DIR/"
echo "  status: launchctl print $DOMAIN/$LABEL"
echo
if [ -n "$EXISTING_PROG" ] && [ "$EXISTING_PROG" != "${REPO_DIR}/odysseus.sh" ]; then
  echo "  ⚠ replaced previous install at: $EXISTING_PROG"
fi
if [ -n "$EXISTING_CWD" ] && [ "$EXISTING_CWD" != "$REPO_DIR" ]; then
  echo "  ⚠ replaced previous WorkingDirectory: $EXISTING_CWD"
fi
echo
echo "  Stop:   launchctl bootout $DOMAIN/$LABEL"
echo "  Start:  launchctl bootstrap $DOMAIN $PLIST_PATH"
echo "  Logs:   tail -f $STDOUT_LOG $STDERR_LOG"
echo "  Remove: ./odysseus.sh --uninstall-service"
