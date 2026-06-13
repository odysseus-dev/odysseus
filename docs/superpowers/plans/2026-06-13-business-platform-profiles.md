# Business Platform Profiles (Slice-1, Plan 2 of 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declarative métier catalog (YAML) + profile compiler that turns a
catalog into multiagent artifacts (`data/personas/<name>/SOUL.md` +
`data/agents/<name>/agent.json`), per spec
`docs/superpowers/specs/2026-06-13-business-platform-slice1-design.md` §4.

**Direction change (owner, 2026-06-13):** first catalog is **general office
staff** — the baseline departments ANY company needs to go live ("paperclip
company"), SEO first — not a travel vertical. Travel becomes a later catalog.
Role capabilities come from **native Odysseus skills** (`data/skills/`,
SkillsManager); the catalog only **references** skills. Métier-authored
content is used ONLY where no native/community skill exists. Community
skills are sourced via the SkillsMP MCP, security-vetted, and seeded into
the repo.

**Skill sourcing decisions (vetted this session):**

| Role need | Source | Verdict |
|---|---|---|
| SEO | SkillsMP `jikime-marketing-seo` (5★, vendor-neutral specialist body) | vetted clean → seed `marketing/seo` |
| content writing / page publishing / web search / email triage / task queue | agentkit-web `skills/` (content-writer, page-writer, web-search, email-triage, task-queue) | reuse as seeds (own codebase) |
| front-desk, sales, office-manager identity | none usable on SkillsMP (hits were security-research datasets / dev-tool routers) | author minimal natively |
| ServiceNow `email-recommendation` (30★) | rejected — hard-coupled to SN MCP/REST stack | — |

**Architecture:**
- `services/business_platform/seed_skills/<category>/<name>/SKILL.md` —
  repo-tracked seeds (`data/` is gitignored). Odysseus frontmatter + body;
  imported bodies keep source attribution.
- `install_seed_skills(skills_dir)` — copy seeds into `data/skills/` ONLY
  when missing (native-first: operator-edited skills are never overwritten).
- `services/business_platform/catalogs/general_office.yaml` — roles
  (departments) with `skills:` references, thin identity `soul`, `tools`.
- `profile_compiler.py` — `load_catalog()` validates (incl. every referenced
  skill resolvable in seeds∪data/skills; hard error listing missing);
  `compile_profile(catalog, base_dir)` emits per role:
  `personas/<vertical>-<role>/SOUL.md` + `meta.json`,
  `agents/<vertical>-<role>/agent.json`
  `{persona, tools, skills, model: null}` (`skills` = forward-compatible
  extension; runtime consumption is Plan 3), plus `front_desk.json`.

**Catalog (general_office) roles:** seo (first), content, front-desk,
support, sales, office-manager. Gated classes used: `payment_refund`
(office-manager), `outbound_comms` (support/content), `quote` (sales);
`booking` omitted (travel-specific). Routing: `quote.→sales`,
`payment.→office-manager`, `comms.→support`, `*→front-desk`.

**Validation rules (CatalogError):** missing/invalid `vertical`; empty
`roles`; `front_desk` target not in `roles`; missing `"*"` catch-all;
`gated_classes` ⊄ `envelope.GATED_CLASSES`; unknown `surface_policy`; role
without `soul`/`tools`; unresolvable skill reference.

---

### Task 1: Seed skills + installer

- [ ] Write 6 seeds under `seed_skills/` (marketing/seo from vetted jikime
  body; 5 agentkit-web bodies verbatim + frontmatter).
- [ ] `install_seed_skills()` + test: installs missing, skips existing,
  SkillsManager can load the result.
- [ ] Commit `feat(platform): seed office-staff skills + native-first installer`.

### Task 2: general_office catalog + loader

- [ ] Catalog YAML (6 roles, skills refs, thin souls).
- [ ] `load_catalog()` + `CatalogError` validation incl. skill resolution.
- [ ] Tests: shipped catalog loads; each invalid-catalog case rejects.
- [ ] Commit `feat(platform): general-office métier catalog + loader`.

### Task 3: Compiler

- [ ] `compile_profile()` → personas/agents/front_desk.json, idempotent,
  manifest returned; golden-file test for one role (spec §5).
- [ ] Commit `feat(platform): profile compiler (catalog → multiagent artifacts)`.

### Task 4: Regression + wrap-up

- [ ] Platform + whole-repo suites green; `graphify update`;
  `codex review --base <plan-2 start sha>`; fix findings; commit.

## Out of scope (Plan 3 / later)

Travel and other vertical catalogs; runtime consumption of artifacts &
skills injection (blocked by multiagent slice-1); mission loop; manager
surface; E2E; registry wiring of `front_desk.json`.
