# Documentation

Repository Markdown is the canonical source for project documentation. A public docs site may be generated from it; avoid maintaining an independent wiki copy unless there is a deliberate future decision to do so.

## Where information belongs

| Location | Purpose |
|---|---|
| `/design` | Product intent, proposals, and accepted decisions (when those paths are used) |
| `/specs` | Technical contracts and invariants |
| `/website` | User, setup, configuration, and deployment guidance (published as the docs site) |
| `/docs` | Developer, architecture, security, and maintainer guidance |
| Root Markdown | Discoverable entry points and policies |

Keep root documents easy to find:

- `README.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `THREAT_MODEL.md`

Not every named directory exists today; do not invent a parallel documentation store just to match this map.

## Updating documentation

When behavior, architecture, a workflow, or its verification requirements change, update the canonical documentation in the same PR when appropriate.

Separate **policy** from **examples**: examples may evolve with tooling; changes to role authority, review requirements, or product ownership require an explicit governance decision. Do not introduce a new mandatory approval rule through a checklist alone.

Documentation PRs should link the relevant issue, record what was reviewed (links, commands, or affected features), and avoid publishing private channels, account names, local paths, secrets, or confidential incident details.

For practical examples of review evidence, see the [review playbook](review-playbook.md). For contributor setup and PR requirements, use [CONTRIBUTING.md](../../CONTRIBUTING.md).
