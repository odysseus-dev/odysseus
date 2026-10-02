# Container runtime contract

The Docker deployment supports the sandbox process boundary on a Linux host with Docker Engine, the Docker Compose plugin, an enforcing AppArmor kernel module, and user namespaces available to the dropped service user. The supported posture is intentionally narrower than arbitrary Docker, rootless, desktop, or non-Linux deployments.

## Host preparation

Install the host profile and load it before starting Compose:

```bash
sudo install -D -m 0644 docker/apparmor/odysseus-sandbox /etc/apparmor.d/containers/odysseus-sandbox
sudo apparmor_parser -r -W /etc/apparmor.d/containers/odysseus-sandbox
sudo aa-status | grep -F odysseus-sandbox
```

Verify the Docker daemon and host kernel provide the required facilities. The exact sysctl name varies by distribution; an absent `kernel.unprivileged_userns_clone` is not itself a failure when `user.max_user_namespaces` is positive and the kernel permits the AppArmor-scoped user namespace request.

```bash
docker info --format '{{json .SecurityOptions}}'
sysctl user.max_user_namespaces
sysctl kernel.unprivileged_userns_clone 2>/dev/null || true
```

The host must not disable Docker seccomp or AppArmor. The Compose service selects `odysseus-sandbox` and the checked-in outer profile by name/path; it does not fall back to `unconfined`. If the host profile is not loaded, Docker reports that named profile is unavailable before the container starts. If Bubblewrap, the trusted launcher, the egress helpers, or the fresh `/proc` contract is unusable after startup, the entrypoint prints a concise failure and exits instead of launching the app.

## Compose validation

Run the configuration check for each shipped variant from the repository root:

```bash
docker compose config
docker compose -f docker-compose.gpu-nvidia.yml config
docker compose -f docker-compose.gpu-amd.yml config
```

On a supported host, build and start the default profile, then inspect the service logs:

```bash
docker compose up -d --build
docker compose logs --tail=120 odysseus
```

The boot log ends with `odysseus-sandbox-self-test: sandbox and broker boundaries passed` before the application starts. The check runs as the configured non-root `PUID`/`PGID` user. It verifies root-owned immutable helpers, the version-pinned Bubblewrap package, a new PID and network namespace, a fresh `/proc` mount, no host PID or container `/etc` exposure, a writable selected workspace, and broker/bridge connectivity to a rejected private destination without making an external request.

The NVIDIA and AMD standalone files carry the same security options as the default service. GPU host prerequisites remain separate: NVIDIA requires the NVIDIA Container Toolkit, while AMD requires `/dev/kfd`, `/dev/dri`, and the configured render group. A GPU prerequisite failure must not cause Compose to remove the sandbox security options.

## `/proc` contract

The application container itself retains its normal container `/proc` view for startup and diagnostics. Every model-requested Sandbox or Full Access process is launched by Bubblewrap with `--unshare-pid --proc /proc`; the payload therefore sees a procfs mounted inside its private PID namespace, not the host or outer container process table. The inner generated policy denies payload `mount`, `pivot_root`, and `umount2` after this trusted bootstrap. The boot self-test checks this exact contract and refuses startup if it cannot establish it.

The outer seccomp profile is generated from the pinned Moby profile by `security/seccomp/generate.py`; its two deployment additions are the trusted Bubblewrap namespace `clone` mask and the mount bootstrap calls. Do not hand-edit the generated JSON. Regenerate and check it with:

```bash
make -C security/seccomp check-generated
```

The default service has no privileged mode, does not add `SYS_ADMIN`, does not use host PID or host networking, and has no unconfined security fallback. Full Access is a later server-owned process mode inside the same container boundary; it is not a Docker privilege escape.
