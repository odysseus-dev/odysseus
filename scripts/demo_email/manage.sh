#!/usr/bin/env bash
# Manage the isolated email demo: a throwaway, local-only Dovecot user +
# a switchable 'Demo' account in Odysseus + fake seed mail.
#
#   ./manage.sh setup      # add Dovecot user, reload, create account, seed mail
#   ./manage.sh reseed     # wipe + re-seed the fake mail (clean slate)
#   ./manage.sh teardown   # remove account row, Dovecot user, and the maildir
#
# Safe by design: the demo user is in NO mbsync channel, so nothing here ever
# reaches a real mail server. Non-demo accounts are untouched.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKER_DIR="${ODYSSEUS_DEMO_MAIL_DIR:-$HOME/docker/snappymail}"
USERS_FILE="$DOCKER_DIR/dovecot/conf/users"
DEMO_USER="demo@odysseus.local"
DEMO_PASS="demodemo"
HERE="$REPO/scripts/demo_email"
# Use the app's venv (has bcrypt/httpx + the app modules); fall back to python3.
PY="$REPO/venv/bin/python"; [ -x "$PY" ] || PY="python3"

reload_dovecot() {
  docker exec dovecot doveadm reload 2>/dev/null || docker restart dovecot >/dev/null
  sleep 1
}

add_user() {
  if grep -q "^${DEMO_USER}:" "$USERS_FILE"; then
    echo "Dovecot user $DEMO_USER already present."
  else
    printf '%s:{PLAIN}%s\n' "$DEMO_USER" "$DEMO_PASS" >> "$USERS_FILE"
    echo "Added Dovecot user $DEMO_USER."
  fi
  reload_dovecot
}

remove_user() {
  if grep -q "^${DEMO_USER}:" "$USERS_FILE"; then
    # portable in-place delete of the demo line
    grep -v "^${DEMO_USER}:" "$USERS_FILE" > "$USERS_FILE.tmp" && mv "$USERS_FILE.tmp" "$USERS_FILE"
    echo "Removed Dovecot user $DEMO_USER."
    reload_dovecot
  fi
  # Drop the maildir too (best-effort; the volume path needs root).
  docker exec dovecot sh -lc "rm -rf '/srv/vmail/${DEMO_USER}'" 2>/dev/null \
    && echo "Removed maildir for $DEMO_USER." || true
}

case "${1:-}" in
  setup)
    add_user
    "$PY" "$HERE/demo_account.py" setup
    "$PY" "$HERE/seed_demo_emails.py" --reset
    echo
    echo "Done. In Odysseus, switch to the 'Demo' account to show off the inbox."
    ;;
  reseed)
    "$PY" "$HERE/seed_demo_emails.py" --reset
    ;;
  teardown)
    # Clear seeded mail + cached AI reply/summary while the user still exists.
    "$PY" "$HERE/seed_demo_emails.py" --wipe-only || true
    "$PY" "$HERE/demo_account.py" teardown || true
    remove_user
    echo "Demo torn down. Real accounts untouched."
    ;;
  *)
    sed -n '2,12p' "$0"
    exit 2
    ;;
esac
