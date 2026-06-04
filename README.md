<p align="center">
  <img src="docs/odysseus-wordmark.png" alt="Odysseus" width="280">
</p>

<p align="center">
  A self-hosted AI workspace for chat, agents, research, documents, email, notes, calendar, and local model workflows.
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
  <img src="docs/odysseus.jpg" alt="Odysseus interface">
</p>

---

## Quick Start

> `dev` is the default branch and gets the newest changes first. Use [`main`](https://github.com/pewdiepie-archdaemon/odysseus/tree/main) if you want the more curated branch.

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
cp .env.example .env
docker compose up -d --build
```

Open `http://localhost:7000` when the containers are healthy. The first admin password is printed in `docker compose logs odysseus`.

Native installs, GPU notes, Windows/macOS instructions, HTTPS, and configuration live in the [setup guide](docs/setup.md).

## Features

- **Chat + Agents** — local/API models, tools, MCP, files, shell, skills, and memory.
- **Cookbook** — hardware-aware model recommendations, downloads, and serving.
- **Deep Research** — multi-step web research with source reading and report generation.
- **Compare** — blind side-by-side model testing and synthesis.
- **Documents** — writing-first editor with AI edits, suggestions, Markdown, HTML, CSV, and syntax highlighting.
- **Email** — IMAP/SMTP inbox with triage, tags, summaries, reminders, and reply drafts.
- **Notes, Tasks + Calendar** — reminders, todos, scheduled agent tasks, and CalDAV sync.
- **Extras** — gallery/image editor, themes, uploads, web search, presets, sessions, and 2FA.

## Demo

A full hover-to-play tour lives on the landing page: [`docs/index.html`](docs/index.html).

### Confidential cloud inference (TEE)
Running models locally gives you one hard guarantee: the data never leaves the
box. The cost is that you are bounded by your own VRAM, so in practice you run
small or heavily quantized models. A normal cloud API removes that ceiling but
inverts the guarantee — your prompts and completions are plaintext to the
operator, who can read, log, or retain them.

Trusted Execution Environment (TEE) inference is a third point that decouples
those two axes. The model runs inside a hardware-isolated enclave — an Intel TDX
confidential VM with the GPU in NVIDIA confidential-computing mode (H100/H200).
Enclave memory (CPU and GPU) is encrypted and is not readable by the host OS,
the hypervisor, or the operator with physical access. So you can run a
frontier-size model that would never fit locally while keeping a confidentiality
property close to local: the party serving the model cannot see your data in the
clear.

What makes this checkable rather than a promise is **remote attestation**.
Before traffic is sent, the client can verify a signed quote proving (a) it is
talking to genuine TEE hardware with confidential computing enabled, and (b) the
exact measured boot image and workload are the expected ones (the quote carries a
hash of the running software, not just "trust us"). NEAR AI pins the serving
TLS key to that attested enclave, so the TLS session is bound to the measured
code rather than only to a domain name.

Honest about the trust model, since this is not magic: the guarantee rests on
trusting Intel's and NVIDIA's attestation roots and their TEE implementations,
and TEEs have a documented history of side-channel research. Attestation tells
you *what code* is running; you still have to decide you trust that code (and
that it does not log elsewhere). It is a *different* trust model from local
inference — remote hardware/vendor trust instead of no remote trust at all — not
a strictly superior one. For threat models where the concern is the model
operator reading your data, it closes most of the gap; for threat models that
reject any third-party hardware trust, local is still the only answer.

Odysseus integrates NEAR AI (`https://cloud-api.near.ai/v1`) as an
OpenAI-compatible provider that serves models this way. Add it like any other
API provider; the attestation can be verified out of band.

## Contributing

Help is welcome. The best entry points are fresh-install testing, provider setup bugs, mobile/editor polish, docs, and small focused refactors. See [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md).

## Security

Odysseus is a self-hosted workspace with powerful local tools. Keep auth enabled, keep private data out of Git, and do not expose raw model/service ports publicly. Deployment details are in the [setup guide](docs/setup.md#security-notes).

## Star History

<a href="https://www.star-history.com/?repos=pewdiepie-archdaemon%2Fodysseus&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
 </picture>
</a>

## License

AGPL-3.0-or-later -- see [LICENSE](LICENSE) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
