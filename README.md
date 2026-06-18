````markdown
<p align="center">
  <img src="docs/hellaine-logo.svg" alt="Hellaine's Jade Palace" width="280">
</p>

<h1 align="center">Hellaine's Jade Palace</h1>

<p align="center">
  <strong>Intelligence without compromise.</strong>
</p>

<p align="center">
  A self-hosted AI command chamber for private chat, agents, research, documents, email, notes, calendar workflows, and local model operations.
</p>

<p align="center">
  Built for those who prefer their intelligence close, their data private, and their tools under their own command.
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="docs/setup.md">Setup Guide</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="ROADMAP.md">Roadmap</a>
</p>

<p align="center">
  <a href="https://repology.org/project/odysseus-ai/versions"><img src="https://repology.org/badge/vertical-allrepos/odysseus-ai.svg" alt="Packaging status"></a>
</p>

<p align="center">
  <img src="docs/odysseus.jpg" alt="Hellaine interface">
</p>

---

## Quick Start

> `dev` is the default branch and receives the newest changes first. Use `main` if you prefer a more curated branch, assuming one is maintained.

```bash
git clone https://github.com/TheMagistrateofMordor/hellaine.git
cd hellaine
cp .env.example .env
docker compose up -d --build
````

Open `http://localhost:7000` when the containers are healthy.

The first admin password is printed in:

```bash
docker compose logs odysseus
```

> Note: the service may still be named `odysseus` internally for compatibility. Hellaine may wear jade and gold now, but some machinery underneath still answers to its old name. Software, like nobility, clings to lineage.

Native installs, GPU notes, Windows/macOS instructions, HTTPS, reverse proxy setup, and configuration details live in the [setup guide](docs/setup.md).

## Features

* **Chat + Agents** — local and API models, tools, MCP, files, shell access, skills, and memory.
* **Cookbook** — hardware-aware model recommendations, downloads, and serving workflows.
* **Deep Research** — multi-step web research with source reading and report generation.
* **Compare** — blind side-by-side model testing and synthesis.
* **Documents** — writing-first editor with AI edits, suggestions, Markdown, HTML, CSV, and syntax highlighting.
* **Email** — IMAP/SMTP inbox with triage, tags, summaries, reminders, and reply drafts.
* **Notes, Tasks + Calendar** — reminders, todos, scheduled agent tasks, and CalDAV sync.
* **Local Model Workflows** — Ollama, llama.cpp, GGUF models, GPU-aware serving, and private inference.
* **Extras** — gallery/image editor, themes, uploads, web search, presets, sessions, and 2FA.

## Privacy

Hellaine is designed to run under your control.

Your models, your server, your data, your rules.

Keep authentication enabled, keep secrets out of Git, and do not expose raw model or service ports directly to the public internet. Use HTTPS, a reverse proxy, Cloudflare Tunnel, Tailscale, or another controlled access method when publishing the interface beyond your local network.

Some conversations are not for the world.

## Demo

A full hover-to-play tour lives on the landing page: [`docs/index.html`](docs/index.html).

## Contributing

Help is welcome, especially in these areas:

* fresh-install testing;
* provider setup bugs;
* mobile and editor polish;
* documentation;
* GPU and local model workflow improvements;
* privacy-first deployment patterns;
* small focused refactors.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md).

## Security

Hellaine is a self-hosted AI workspace with powerful local capabilities.

Treat it like an admin console, not a toy. Depending on your configuration, it may have access to files, tools, models, shell workflows, documents, email, tasks, notes, and private memory.

Recommended rules:

* keep authentication enabled;
* use strong admin credentials;
* keep `.env`, tokens, API keys, logs, and local data out of Git;
* avoid exposing raw backend or model ports publicly;
* place the UI behind HTTPS when accessed remotely;
* review Docker mounts before deploying;
* assume local tools can be powerful enough to be dangerous.

Discretion is not decoration. It is infrastructure.

Deployment details are in the [setup guide](docs/setup.md#security-notes).

## Upstream

Hellaine is based on the original Odysseus project and carries its foundation forward with a darker interface, stronger privacy emphasis, and a Jade Palace identity.

Respect upstream work. Keep acknowledgments and license terms intact.

## Star History

<a href="https://www.star-history.com/?repos=TheMagistrateofMordor%2Fhellaine&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=TheMagistrateofMordor/hellaine&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=TheMagistrateofMordor/hellaine&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=TheMagistrateofMordor/hellaine&type=date&legend=top-left" />
 </picture>
</a>

## License

AGPL-3.0-or-later — see [LICENSE](LICENSE) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).

```
```
