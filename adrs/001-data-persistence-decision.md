# ADR: Decide on missing explicit intent and/or improvement plan in data persistence strategy.

- Status: proposed
- Deciders: Felix
- Date: 2026-06-12

## Context

So far the de-facto architectural decision has been to let LLMs handle data persistence however they see fit in order to improve feature creation speed.

https://github.com/pewdiepie-archdaemon/odysseus/blob/main/ROADMAP.md does not have anything on data persistence.

### Technical problems

Odysseus currently uses two different persistence styles:

1. Different SQLite dbs in data/*.db
2. A whole lot of JSON/state files under data/

This causes a lot of potential problems:

1. No obvious single source of truth. Problems with code maintenance.
2. No cross-store transactions. Problems with concurrency.
3. Referential integrity needs to be application logic.
4. High operational complexity. Migrations, backups require multiple approaches.


### Organizational problems
* https://github.com/pewdiepie-archdaemon/odysseus/issues/728 was closed without valid cause. CallumCarmichael could have been slowly iterating improvements this whole time.
* Odysseus has a vibe coding smell for developers. Demonstrated e.g. here: https://github.com/pewdiepie-archdaemon/odysseus/issues/1866

## Option 1
Decide to keep existing data architecture. Document this decision and try to solve as much of potential problems listed via documentation.

## Consequences
- ➕ No migration effort is needed — team can keep shipping features.
- ➕ No risk of introducing new bugs through a data layer rewrite.
- ➕ Documentation can be done incrementally with low effort.
- ➖ The dual-store problems (no single source of truth, no cross-store transactions) remain and will compound as the codebase grows.
- ➖ Documentation alone cannot enforce consistency — new LLM-generated code will continue to pick whichever persistence style it encounters first.
- ➖ Operational complexity (backups, migrations) stays high and error-prone.
- ➖ The "vibe coding smell" perception persists, making it harder to attract and retain contributors.

## Option 2
Decide to rework data persistence. Give clean goals such that people can write small PRs.

## Consequences
- ➕ A unified persistence layer gives a single source of truth and simplifies code maintenance.
- ➕ Cross-store transaction and referential integrity issues are eliminated at the infrastructure level rather than patched in application logic.
- ➕ Operational concerns (backups, migrations, monitoring) are consolidated into one approach.
- ➕ Clear incremental goals enable small PRs, reducing review burden and merge risk.
- ➕ Addresses the contributor perception issue and unblocks work like issue #728.
- ➕➖ An end-to-end testing initiative might be needed before the data layer refactor to establish a safety net and ensure stability throughout the migration.
- ➖ The migration requires non-trivial effort and coordination across the team.
- ➖ In-flight features may be slowed or blocked while the persistence layer is being reworked.
- ➖ Data migration carries risk of data loss or corruption if not carefully executed.