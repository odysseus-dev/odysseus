# `docker/sandbox/` — optional container sandbox for agent `bash`/`python`

The agent `bash` and `python` tools execute in-process with the app's full
environment and no filesystem/network isolation. This is an **opt-in, off-by-default**
sandbox that runs each such tool call inside a fresh, hardened, ephemeral container
instead. It's implemented in [`../../src/tool_sandbox.py`](../../src/tool_sandbox.py)
and wired into `src/tool_execution.py` with a two-line guarded delegation.

**Nothing changes unless you turn it on.** When `TOOL_SANDBOX` is unset/`off`, or the
desktop build is active, or Docker isn't present, tool calls run exactly as before.

## Enable

```bash
TOOL_SANDBOX=container                 # off (default) | container
# optional tunables (all have safe defaults):
TOOL_SANDBOX_RUNTIME=runc              # runc | runsc (gVisor) | kata-runtime | ...
TOOL_SANDBOX_IMAGE=registry/your-python-runtime
TOOL_SANDBOX_SHELL_IMAGE=registry/your-shell-runtime
TOOL_SANDBOX_NETWORK=none              # none (default) | a docker network name
TOOL_SANDBOX_MEMORY=512m
TOOL_SANDBOX_CPUS=0.5
TOOL_SANDBOX_PIDS=128
```

Each call runs as `docker run --rm` with: non-root `--user`, `--read-only` root fs,
`--tmpfs /tmp:noexec,nosuid`, `--cap-drop=ALL`, `--security-opt no-new-privileges`,
the seccomp profile below, `--pids-limit`/`--memory`/`--cpus`/ulimits, **no host env
passthrough**, and `--network none` by default. The container is force-removed on
timeout/cancel. A spawn audit line is logged (no command/code is logged).

## Files

| File | What |
|---|---|
| `seccomp-tenant.json` | Stricter-than-default syscall profile (`--security-opt seccomp=`); derived from the Moby default with privileged syscalls dropped. Defense-in-depth on top of gVisor. |
| `Dockerfile.tenant-runtime` | Distroless **python** runtime image (no shell, no package manager) for the `python` tool. |
| `Dockerfile.tenant-runtime-shell` | Minimal **BusyBox** shell runtime for the `bash` tool (distroless has no `/bin/sh`). |

Build the runtime images on a Linux host and point `TOOL_SANDBOX_IMAGE` /
`TOOL_SANDBOX_SHELL_IMAGE` at them. Pin base-image digests in CI.

## Notes

- For real kernel isolation set `TOOL_SANDBOX_RUNTIME=runsc` and install gVisor on the
  host (register `runsc` in `/etc/docker/daemon.json`); `runc` is the default if not.
- The seccomp profile is a curated start point — diff it against
  `moby/profiles/seccomp/default.json` for your Docker version and validate against your
  real workloads before relying on it.
- Code is passed as one argv argument (capped ~120 KB); the container has no host bind
  mounts and (by default) no network, so a one-shot call can't reach host services.
