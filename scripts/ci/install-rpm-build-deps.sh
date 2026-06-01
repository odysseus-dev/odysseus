#!/usr/bin/env bash
set -euo pipefail

target="${1:?usage: install-rpm-build-deps.sh <target>}"

retry() {
  local attempts="${1:?attempt count required}"
  shift

  local delay
  for attempt in $(seq 1 "$attempts"); do
    if "$@"; then
      return 0
    fi

    if [ "$attempt" -eq "$attempts" ]; then
      return 1
    fi

    delay=$((attempt * 15))
    echo "Command failed; retrying in ${delay}s (${attempt}/${attempts})..."
    sleep "$delay"
  done
}

case "$target" in
  fedora43|fedora44)
    retry 5 dnf --disablerepo='fedora-cisco-openh264' install -y \
      bash ca-certificates git python3 rpm-build systemd-rpm-macros
    ;;
  el10)
    retry 5 dnf install -y \
      bash ca-certificates git python3 rpm-build systemd-rpm-macros
    ;;
  opensuse-tumbleweed)
    retry 5 zypper --non-interactive --gpg-auto-import-keys refresh
    retry 5 zypper --non-interactive install --no-recommends \
      bash ca-certificates git python3 rpm-build systemd-rpm-macros
    ;;
  *)
    echo "Unknown RPM target: $target" >&2
    exit 1
    ;;
esac
