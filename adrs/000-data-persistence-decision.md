# ADR: Data Persistence Strategy

- Status: proposed
- Deciders: Felix, active maintainers
- Date: 2026-06-12 (original), 2026-06-15 (revised after PR #4101 discussion)

## Context

Odysseus currently uses multiple persistence styles without explicit architectural decisions about which backend serves which data domain:

- SQLite databases: `data/app.db` (primary) and `data/scheduled_emails.db`
- ~11 JSON/state files under `data/`
- File-system directories for uploads, generated images, skills, caches
- Optional ChromaDB for vector embeddings

This creates concrete problems: no cross-store transactions, no single source of truth, referential integrity enforced in application code, and operational complexity where migrations and backups require different approaches per store. See [persistence.md](https://github.com/RaresKeY/odysseus/blob/docs/specs-bootstrap/specs/persistence.md) for the full implementation-truth map.

### What this ADR is not

The [original version of this proposal](https://github.com/pewdiepie-archdaemon/odysseus/pull/4101) framed persistence as a binary choice: keep the current architecture or rework it. Feedback from maintainers ([pewdiepie-archdaemon](https://github.com/pewdiepie-archdaemon/odysseus/pull/4101#pullrequestreview-2929533189), [RaresKeY](https://github.com/pewdiepie-archdaemon/odysseus/pull/4101#issuecomment-4705852839)) identified that a blanket migration proposal is premature without:

1. An implementation-grounded map of current persistence domains
2. Per-domain decisions based on ownership, migration risk, access patterns, and query needs
3. Concrete rationale for each recommendation — not "migrate everything to SQLite"

RaresKeY's framing: *"define a clear source of truth per data domain, document why that storage backend is appropriate, and only migrate when there is a concrete reviewable reason."*

This revised ADR adopts that approach. The binary Option 1 / Option 2 framing is replaced by per-domain analysis in [001-data-persistence-decision.md](001-data-persistence-decision.md).

## Approach

Rather than choosing between "keep everything" and "rework everything," the per-domain analysis in [001](001-data-persistence-decision.md) evaluates each persistence domain individually across 8 subsystem groups (46 use-cases total). Each domain is assessed on:

- Current backend and access pattern
- Ownership model and query needs
- Atomicity and backup coverage
- Whether SQLite solves a real problem or adds unnecessary friction

Recommendations fall into four categories: **Keep current**, **Add SQLite reference** (files stay on disk, SQLite tracks metadata), **Migrate to SQLite**, and **Needs discussion**.

The full analysis is in [001-data-persistence-decision.md](001-data-persistence-decision.md).

## Prior Art

[Issue #728](https://github.com/pewdiepie-archdaemon/odysseus/issues/728) by CallumCarmicheal is comprehensive prior art that independently identified the same problems and proposed detailed schema designs. The per-domain analysis in 001 builds on that work, the specs from [PR #2538](https://github.com/pewdiepie-archdaemon/odysseus/pull/2538), and direct codebase analysis (SQLAlchemy models, storage constants, and route-level storage call sites).
