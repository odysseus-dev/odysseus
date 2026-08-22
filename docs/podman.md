# Rootless Podman

This is the rootless Podman path for the existing Odysseus Compose stack. It
keeps `docker-compose.yml` as the source of truth and adds small overlays for
Podman-specific user mapping and optional GPU access.

## Requirements

- Podman with rootless user namespaces configured.
- A Compose provider available through [`podman compose`](https://docs.podman.io/en/latest/markdown/podman-compose.1.html).
- `crun` when using the AMD overlay's `keep-groups` device access.

Confirm the runtime before starting:

```bash
podman info --format '{{.Host.Security.Rootless}}'
podman compose version
```

The first command must print `true`. The Podman overlay deliberately uses a
rootless-only user mapping and is not supported with `sudo podman`.

## CPU setup

```bash
git clone https://github.com/odysseus-dev/odysseus.git
cd odysseus
cp .env.example .env
podman compose -f docker-compose.yml -f docker/podman.yml up -d --build
```

Open `http://localhost:7000` when the containers are healthy. The generated
first-login password is available without placing a fixed password in `.env`:

```bash
podman compose -f docker-compose.yml -f docker/podman.yml logs odysseus
```

Common lifecycle commands use the same file list:

```bash
podman compose -f docker-compose.yml -f docker/podman.yml ps
podman compose -f docker-compose.yml -f docker/podman.yml logs --tail=120 odysseus
podman compose -f docker-compose.yml -f docker/podman.yml down
```

The overlay uses Podman's
[`keep-id`](https://docs.podman.io/en/latest/markdown/podman-run.1.html#userns-mode)
mapping to place the rootless host user at UID/GID 0 inside the Odysseus
container and sets `PUID=0`/`PGID=0`. Container root still maps to the
unprivileged user on the host; this lets the existing entrypoint repair and use
bind mounts without leaving subordinate-UID files in `data/` or `logs/`.

The base Compose file keeps every published service on host loopback by
default. It also provides `host.docker.internal` for host-side Ollama and other
model endpoints.

## NVIDIA GPU through CDI

Install the host NVIDIA driver and NVIDIA Container Toolkit, then follow the
toolkit's [CDI setup](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html)
and confirm that it publishes devices:

```bash
nvidia-ctk cdi list
podman run --rm --security-opt=label=disable \
  --device=nvidia.com/gpu=all docker.io/library/ubuntu:24.04 nvidia-smi -L
```

Start Odysseus with the Podman CDI overlay:

```bash
podman compose \
  -f docker-compose.yml \
  -f docker/podman.yml \
  -f docker/podman.gpu-nvidia.yml \
  up -d --build
```

Do not combine `docker/podman.gpu-nvidia.yml` with
`docker/gpu.nvidia.yml`. The Docker overlay uses the Docker GPU reservation
and legacy NVIDIA environment variables; the Podman overlay requests the CDI
device directly.

If the host still has the legacy NVIDIA OCI hook, remove or disable that hook
before using CDI. Native CDI injection can conflict with the hook when
`NVIDIA_VISIBLE_DEVICES` is set.

## AMD ROCm GPU

The host must provide `/dev/kfd` and `/dev/dri`, and the rootless user must
already belong to the groups that can access them. Verify that before starting:

```bash
test -r /dev/kfd && test -w /dev/kfd
ls -l /dev/kfd /dev/dri/renderD*
```

Then use the Podman AMD overlay:

```bash
podman compose \
  -f docker-compose.yml \
  -f docker/podman.yml \
  -f docker/podman.gpu-amd.yml \
  up -d --build
```

The overlay preserves the rootless user's supplementary groups through
Podman's `keep-groups` extension. Do not combine it with
`docker/gpu.amd.yml`.

GPU overlays expose host devices only. Install the matching vLLM,
llama-cpp-python, or other serving engine separately through Cookbook after
the container can see the GPU.

## Security boundary

The Podman overlays do not mount a Docker socket, Podman socket, broad home
directory, or host network. Do not substitute a Podman socket into
`docker/host-docker.yml`: either control socket grants broad authority over the
host user's containers. Host Ollama and OpenAI-compatible endpoints work over
the existing network path without a control socket.

Keep `APP_BIND=127.0.0.1`, `AUTH_ENABLED=true`, and `LOCALHOST_BYPASS=false`
unless the deployment is intentionally protected by a trusted private network
or reverse proxy.

## Troubleshooting

Render the merged configuration without starting containers:

```bash
podman compose -f docker-compose.yml -f docker/podman.yml config
```

If bind-mounted files have unexpected numeric owners, confirm the command is
rootless and that `docker/podman.yml` is present in every lifecycle command.
If a GPU is absent, validate the host device/CDI path independently before
debugging Odysseus. GPU enumeration alone does not install CUDA or ROCm
userspace in the image.
