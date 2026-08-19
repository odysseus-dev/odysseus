#!/bin/sh
# Fail-closed Docker boot check for the dropped Odysseus service user.
#
# This deliberately does not call application Python or inspect user data. It
# verifies only the immutable deployment helpers and the smallest boundary
# that the process-sandbox foundation requires: a fresh /proc, PID/network
# namespaces, a writable selected directory, and the broker socket bridge.
set -eu

fail() {
    echo "odysseus-sandbox-self-test: $*" >&2
    exit 1
}

[ "$(id -u)" != "0" ] || fail "the sandbox boot check must run as the dropped non-root service user"

LAUNCHER=/usr/local/libexec/odysseus-seccomp-launcher
BWRAP=/usr/bin/bwrap
BROKER=/usr/local/libexec/odysseus-egress-broker
BRIDGE=/usr/local/libexec/odysseus-egress-bridge
PRLIMIT=/usr/bin/prlimit
PYTHON=/usr/local/bin/python3
[ -x "$PYTHON" ] || PYTHON=/usr/bin/python3

check_trusted_file() {
    path="$1"
    description="$2"
    [ -f "$path" ] && [ -x "$path" ] || fail "missing trusted $description at $path"
    [ "$(stat -c '%u:%g' "$path")" = "0:0" ] || fail "trusted $description is not root-owned: $path"
    if [ -n "$(find "$path" -maxdepth 0 \( -perm /022 -o -perm /6000 \) -print -quit)" ]; then
        fail "trusted $description is writable or set-id: $path"
    fi
}

check_trusted_file "$LAUNCHER" "seccomp launcher"
check_trusted_file "$BWRAP" "Bubblewrap binary"
check_trusted_file "$BROKER" "egress broker"
check_trusted_file "$BRIDGE" "egress bridge"
check_trusted_file "$PRLIMIT" "prlimit helper"
check_trusted_file "$PYTHON" "Python interpreter"

[ "$(bwrap --version)" = "bubblewrap 0.11.0" ] || fail "unsupported Bubblewrap version"
[ -r /proc/self/mountinfo ] || fail "container /proc mountinfo is unavailable"

WORKSPACE="$(mktemp -d /tmp/odysseus-sandbox-self-test.XXXXXX)" || fail "unable to create a self-test workspace"
trap 'rm -rf "$WORKSPACE"' EXIT HUP INT TERM
chmod 700 "$WORKSPACE"

run_boundary() {
    brokered="$1"
    probe="$2"
    label="$3"
    shift 2

    set -- "$LAUNCHER" "$BWRAP" \
        --unshare-user \
        --unshare-ipc \
        --unshare-pid \
        --unshare-net \
        --unshare-uts \
        --unshare-cgroup \
        --die-with-parent \
        --new-session \
        --clearenv \
        --cap-drop ALL \
        --ro-bind /usr /usr \
        --symlink usr/bin /bin \
        --symlink usr/lib /lib
    if [ -d /usr/lib64 ]; then
        set -- "$@" --symlink usr/lib64 /lib64
    fi
    set -- "$@" \
        --dev /dev \
        --proc /proc \
        --tmpfs /tmp \
        --dir /tmp/odysseus-home \
        --dir "$WORKSPACE" \
        --bind "$WORKSPACE" "$WORKSPACE" \
        --setenv HOME /tmp/odysseus-home \
        --setenv LANG C.UTF-8 \
        --setenv LC_ALL C.UTF-8 \
        --setenv PATH /usr/local/bin:/usr/bin:/bin \
        --setenv TMPDIR /tmp \
        --chdir "$WORKSPACE" \
        --
    if [ "$brokered" = "yes" ]; then
        set -- "$@" "$BRIDGE" /run/odysseus-egress/broker.sock --
    fi
    set -- "$@" "$PYTHON" -I -

    if ! printf '%s\n' "$probe" | if [ "$brokered" = "yes" ]; then
        "$BROKER" "$@"
    else
        "$@"
    fi; then
        fail "$label boundary probe failed"
    fi
}

NETWORKLESS_PROBE='import os, pathlib, socket
mounts = [line.split() for line in pathlib.Path("/proc/self/mountinfo").read_text().splitlines()]
proc_mounts = [fields for fields in mounts if len(fields) > 6 and fields[4] == "/proc" and "-" in fields]
assert proc_mounts and proc_mounts[0][proc_mounts[0].index("-") + 1] == "proc", "sandbox /proc is not a fresh procfs"
pids = [int(entry.name) for entry in pathlib.Path("/proc").iterdir() if entry.name.isdigit()]
assert pids and max(pids) < 32, "host process IDs leaked into the sandbox"
assert pathlib.Path("/proc/1/status").is_file(), "sandbox PID 1 is unavailable"
assert not pathlib.Path("/proc/1/root/etc/passwd").exists(), "sandbox root exposes the image /etc"
assert not pathlib.Path("/etc/passwd").exists(), "sandbox exposes an unmounted container /etc"
assert {name for _, name in socket.if_nameindex()} <= {"lo"}, "sandbox exposes a non-loopback interface"
probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
probe.settimeout(0.2)
try:
    probe.connect(("127.0.0.1", 7000))
except OSError:
    pass
else:
    raise AssertionError("networkless sandbox reached the container loopback")
finally:
    probe.close()
pathlib.Path("boot-boundary-write").write_text("ok", encoding="ascii")
assert os.getuid() == 0, "sandbox user namespace did not establish the expected payload mapping"
print("networkless boundary passed")'

run_boundary no "$NETWORKLESS_PROBE" networkless

BROKER_PROBE='import socket
client = socket.create_connection(("127.0.0.1", 3128), timeout=2)
client.sendall(b"GET http://127.0.0.1/ HTTP/1.1\\r\\nHost: 127.0.0.1\\r\\nConnection: close\\r\\n\\r\\n")
response = client.recv(4096)
client.close()
assert response.startswith(b"HTTP/1.1"), "broker bridge returned no HTTP response"
print("broker bridge passed")'

run_boundary yes "$BROKER_PROBE" brokered
echo "odysseus-sandbox-self-test: sandbox and broker boundaries passed" >&2
