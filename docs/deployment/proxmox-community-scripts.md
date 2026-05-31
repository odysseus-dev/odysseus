# Odysseus on Proxmox Community Scripts

This repository includes a Proxmox-oriented installer package intended to be
ported into the Community Scripts workflow (ProxmoxVED first, then upstream).

## What is included

- `deploy/proxmox/install-odysseus-lxc.sh`
  - LXC guest installer (Debian/Ubuntu) using Docker Compose
  - Brings up the full Odysseus stack: `odysseus`, `chromadb`, `searxng`, `ntfy`
  - Sets `AUTH_ENABLED=true` by default
- `deploy/proxmox/model-profile-helper.sh`
  - Hardware-tier model profile helper
  - Tiers: `cpu`, `gpu_modest` (6-12GB VRAM), `gpu_high` (16GB+ VRAM)
  - Optional Ollama primary/fallback model pulls per tier

## Local test flow

Run inside a fresh Debian/Ubuntu environment:

```bash
cd /opt
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
sudo bash deploy/proxmox/install-odysseus-lxc.sh
```

Optional model auto-setup:

```bash
sudo INSTALL_OLLAMA=true AUTO_PULL_MODELS=true MODEL_TIER=cpu bash deploy/proxmox/install-odysseus-lxc.sh
```

Post-install profile helper examples:

```bash
odysseus-model-profile --tier cpu
odysseus-model-profile --tier gpu_modest --pull-models
odysseus-model-profile --tier gpu_high --pull-models --pull-alternatives
```

## Expected Community Scripts mapping

When upstreaming:

1. Keep this logic split between:
   - host-side CT creation script (Community Scripts template style)
   - in-guest install routine (this installer logic)
2. Keep `Default` mode minimal and `Advanced` mode configurable.
3. Keep security defaults private-by-default; add reverse-proxy/TLS notes.
4. Preserve model helper output contract so users get exact Odysseus settings
   after install.

When `INSTALL_OLLAMA=true`, the installer configures host Ollama to listen on
`0.0.0.0:11434` and adds a Docker Compose override so the Odysseus container can
reach it at `http://host.docker.internal:11434/v1`.

## Traefik example

If Traefik runs outside this LXC, route only the Odysseus web UI to the LXC IP
and keep ChromaDB, SearXNG, ntfy, and Ollama private.

Example dynamic config:

```yaml
http:
  routers:
    odysseus:
      rule: Host(`odysseus.example.com`)
      entryPoints:
        - websecure
      tls:
        certResolver: letsencrypt
      service: odysseus

  services:
    odysseus:
      loadBalancer:
        servers:
          - url: http://192.168.2.42:7000
        passHostHeader: true
```

Replace `odysseus.example.com`, `letsencrypt`, and `192.168.2.42` for your
Traefik deployment. The same example is available at
`deploy/proxmox/traefik-odysseus.yml`.

## Recommended defaults by hardware tier

- `cpu`: `qwen2.5:3b-instruct-q4_K_M` + `qwen2.5:1.5b-instruct-q4_K_M`
- `gpu_modest`: `qwen2.5:7b-instruct-q4_K_M` + `qwen2.5:3b-instruct-q4_K_M`
- `gpu_high`: `qwen2.5:14b-instruct-q4_K_M` + `qwen2.5:7b-instruct-q4_K_M`

If local inference is not desired, users can configure any external
OpenAI-compatible endpoint inside Odysseus settings.
