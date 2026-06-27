# Multi-Machine (Tailscale + Radicale) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document and automate a repeatable multi-machine Odysseus layout where each workstation runs its own app stack, shares calendar via Radicale on a Tailscale-reachable host, and discovers remote LLMs over Tailscale.

**Architecture:** One Odysseus instance per machine (native Windows or Docker). A optional central `docker/radicale/` stack on NAS/home server binds to a Tailscale IP. Each machine's `.env` sets `LLM_HOSTS`, `ODYSSEUS_ALLOW_PRIVATE_CALDAV=1`, host inference URLs (`LM_STUDIO_URL` / `OLLAMA_BASE_URL`), and optional Tailscale bind addresses for ntfy/UI. Pure-Python `scripts/multi_machine_env.py` validates `.env`; `scripts/bootstrap-multi-machine.ps1` seeds a template from `.env.example` + detected Tailscale IP.

**Tech Stack:** Docker Compose, PowerShell 5.1+, pytest, Tailscale CLI, Radicale (tomsquest/docker-radicale).

---

## File map

| File | Action |
|------|--------|
| `docs/multi-machine.md` | **Create** — operator guide |
| `docker/radicale/docker-compose.yml` | **Create** — shared CalDAV stack |
| `docker/radicale/.env.example` | **Create** |
| `docker/radicale/config/config` | **Create** |
| `docker/radicale/config/users.example` | **Create** |
| `docker/radicale/README.md` | **Create** |
| `scripts/multi_machine_env.py` | **Create** — `.env` checklist |
| `scripts/bootstrap-multi-machine.ps1` | **Create** — Windows bootstrap |
| `tests/test_multi_machine_env.py` | **Create** |
| `tests/test_docker_radicale_compose.py` | **Create** |
| `tests/test_docker_compose_multi_machine_env.py` | **Create** |
| `tests/test_bootstrap_multi_machine_script.py` | **Create** |
| `.env.example` | **Modify** — multi-machine block |
| `docker-compose.yml` | **Modify** — `LM_STUDIO_URL`, `ODYSSEUS_ALLOW_PRIVATE_CALDAV` |
| `docker-compose.gpu-nvidia.yml` | **Modify** — same env lines |
| `docker-compose.gpu-amd.yml` | **Modify** — same env lines |

---
