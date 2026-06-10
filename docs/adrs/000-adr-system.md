# ADR-000: Adoption of ADRs

## Decision

Facing an inability to accept and thereby achieve architectural change, this working group adopts Architecture Decision Records (ADRs)

## Context

The Odysseus was launched on May 31st, 2026 by Felix Kjellberg as a Self-Hosted AI Workspace, created to provide a first-class AI/ML UI experience on users' own hardware, with their own data. Local. Private. Secure.

Within the first week, the project accumulated hundreds of Issues, Pull Requests, and Discussions. Many of these would be Merged into the project after a cursory review. The project evolved at a breakneck speed that was hard for contributors to keep up with and verify.

An Issue was opened by supporters: [Proposal: Architecture & Codebase Structure v3](https://github.com/pewdiepie-archdaemon/odysseus/issues/605) to express concerns regarding the structural engineering of the project. In it detailed, specific advisement on large-scale changes that could improve the verification and usability of the project. Many contributors attempted to craft PRs in order to improve the structure, only to discover difficulties owing to a lack of agreement. In order to coordinate agreement on large-scale architectural changes, it became neccessary to consider a system that accepted work.

## Solution

This working group adopts Architecture Decision Records (ADRs) as the acceptance criteria for large-scale architectural planning.

**Architecture** is the set of things that are costly to change later. ADRs exist to agree on and document those decisions before implementation work spreads.

Our format is adapted from the community catalog at [architecture-decision-record/architecture-decision-record](https://github.com/architecture-decision-record/architecture-decision-record/tree/main). We cherry-picked three widely used traditions and kept the result small:

| Source | What we took |
| --- | --- |
| **Nygard** | The core shape: **Context**, **Decision**, **Consequences**. We kept that spine and borrowed wording for the consequences section (what becomes easier or harder because of the change). |
| **Alexandrian** | Explicit, structured language in **Context** and **Decision** so the record states forces and the chosen option clearly, not vaguely. |
| **Tyree & Akerman** | Group-oriented records and **Alternative Positions** — other viable options treated as named positions, not footnotes. |

We **removed Status** (Accepted, Superseded, etc.). A merged PR that adds `docs/adrs/{N}-*.md` with the correct number is enough to establish a valid ADR. To change direction later, add a new ADR; do not rewrite an old one.

We **added Signature** so each ADR names the people accountable for driving the initial implementation and review. Signers are the trusted set who confirm the record was planned before large work lands.

### Immutability

Once an ADR is merged, treat it as **read-only**. Typos and formatting fixes are fine if they do not change meaning. If the decision changes, add a new ADR (**ADR-{M}**), say so in its Context, and rename the old file from `{N}-{short-title}.md` to `{N}-{M}-{short-title}.md` (**M** = the ADR that updates it). Do not edit the old record in place — the git history stays the audit trail.

### How to create an ADR

1. Pick the next number under `docs/adrs/` (this file is **000**).
2. Copy the template below into `docs/adrs/{N}-{short-title}.md`.
3. Fill every section. Link issues, PRs, and prior ADRs where relevant.
4. Open a PR. Reviewers check structure, alternatives, and signatures — not re-litigation of every detail in chat.
5. After merge, the ADR is binding for planning and review of related work.

### Template

Every ADR **must** use this structure and these headings (order fixed):

```markdown
# ADR-{N}: {Short title}

## Decision

{One sentence — what was decided. Formatted as "In the context of (use case), facing (concern), The working group decided for (option), to achieve (quality), accepting (downside)." }

## Context

{Why this decision needed to be made. What forces are at play (technical, political, social, project). This is the story explaining the problem the working group is looking to resolve.}

## Solution

{The chosen position in technical terms. Does not need every implementation detail,
but must set criteria contributors can follow.}

## Alternative Positions

{Other positions considered and why they were not chosen. Link references when possible. The null position must always be included.}

| Option | Why rejected |
| --- | --- |
| {Alternative A} | {Reason} |
| {Alternative B} | {Reason} |

## Consequences

{What becomes easier or more difficult because of this decision — benefits, costs, and follow-on work.}

## Signature

{Names (and optionally roles) of people responsible for initial implementation and
ensuring the ADR was planned before dependent changes merge.}
```

## Alternative Positions

| Option | Why rejected |
| --- | --- |
| **Null — no change** (continue merging large structural work from issues/PRs alone) | Leaves no durable agreement; contributors repeat the same debates and work is not coordinated. |
| **Informal design docs** (wiki, long issue threads, README sections) | Easy to edit in place, so “current truth” drifts; highly mutable and does not have an accepted process. |
| **Full RFC / status lifecycle ADRs** (Accepted, Deprecated, Superseded in every file) | Clear lifecycle, but heavier process than this project needs right now; Can be modified by a future ADR. |
| **Alternative ADR Format** | The establishment of any format is preferred. Can be modified by a future ADR. |

## Consequences

**Easier:** Large or cross-cutting changes have a numbered, reviewable record before implementation spreads. Contributors can point PRs at `docs/adrs/{N}-*.md`. Reviewers can ask “does this match ADR-N?” instead of reconstructing intent from chat or discussions. Immutable merged ADRs preserve why a choice was made.

**Harder:** Non-trivial architecture work should not land without an ADR (or an explicit decision that the change is out of scope for ADRs). Writing alternatives and consequences takes time up front. Reversing direction requires a new ADR.

**Follow-on:** Maintain sequential numbering under `docs/adrs/`, keep this template aligned with ADR-000, and reference prior ADRs in Context when superseding.

## Signature

{The submission of this ADR into `main` constitutes initial implementation}
- (@crazyjackel) Jackson Levitt