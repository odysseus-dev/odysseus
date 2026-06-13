# Business Platform Profiles (Slice-1, Plan 2 of 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declarative métier catalog (YAML) + profile compiler that turns a
vertical catalog into the multiagent artifacts the approved multiagent spec
defines (`data/personas/<name>/SOUL.md` + `data/agents/<name>/agent.json`),
per spec `docs/superpowers/specs/2026-06-13-business-platform-slice1-design.md`
§4. Ships ONE catalog: travel agency.

**Architecture:** `services/business_platform/catalogs/travel_agency.yaml`
(the catalog), `services/business_platform/profile_compiler.py` (load,
validate, compile). Compiler writes persona/agent artifacts plus a
`front_desk.json` routing table under a target base dir (default `data/`,
tests pass `tmp_path`). Artifact formats come from the multiagent spec
(`docs/superpowers/specs/2026-06-12-odysseus-multiagent-orchestration-design.md`):
persona = `personas/<name>/SOUL.md` + `meta.json {description}`;
agent = `agents/<name>/agent.json {persona, tools, model}`. Names are
prefixed `<vertical>-<role>` so several verticals can coexist.

**Catalog schema (YAML):**

```yaml
vertical: travel_agency            # required, [a-z0-9_]+
display_name: Travel Agency
surface_policy: web_first          # web_only | web_first | app_invite | app_required
gated_classes: [payment_refund, booking, outbound_comms, quote]  # must ⊆ GATED_CLASSES
front_desk:                        # intent prefix -> role (catch-all "*" required)
  "booking.": booking-clerk
  "quote.": trip-planner
  "comms.": client-comms
  "*": front-desk
roles:                             # >= 1; front_desk targets must exist here
  front-desk:
    description: one-line role summary
    soul: |
      Markdown persona text (becomes SOUL.md)
    tools: [memory]                # allowlist; copied into agent.json verbatim
```

**Validation rules (CatalogError):** missing/invalid `vertical`; empty
`roles`; a `front_desk` target not in `roles`; missing `"*"` catch-all;
`gated_classes` not a subset of `envelope.GATED_CLASSES`; unknown
`surface_policy`; role without `soul` or `tools`.

**Tech Stack:** PyYAML (already in venv), existing
`services/business_platform/envelope.py` for `GATED_CLASSES`.

**Conventions:** match Plan 1 (tests under `tests/`, run with
`./venv/bin/python -m pytest` from repo root, one commit per task).

---

### Task 1: Travel-agency catalog + loader/validator

**Files:**
- Create: `services/business_platform/catalogs/travel_agency.yaml`
- Create: `services/business_platform/profile_compiler.py` (load_catalog only)
- Test: `tests/test_platform_profiles.py` (loader part)

- [ ] Failing test: `load_catalog()` returns validated dict for the shipped
  travel catalog (4 roles: front-desk, trip-planner, booking-clerk,
  client-comms; all four gated classes; `"*"` route present); raises
  `CatalogError` for: unknown gated class, route to missing role, missing
  catch-all, empty roles.
- [ ] Implement catalog YAML + `load_catalog(path) -> dict` with the rules
  above.
- [ ] Run; commit `feat(platform): travel-agency métier catalog + loader`.

### Task 2: Compiler — catalog → personas/agents/front-desk artifacts

**Files:**
- Modify: `services/business_platform/profile_compiler.py` (add compile)
- Test: `tests/test_platform_profiles.py` (compiler part) + golden files
  under `tests/golden/profiles/travel_agency/`

- [ ] Failing test: `compile_profile(catalog, base_dir)` writes, per role:
  `personas/travel_agency-<role>/SOUL.md` (catalog `soul`, verbatim),
  `personas/travel_agency-<role>/meta.json` (`{description}`),
  `agents/travel_agency-<role>/agent.json`
  (`{"persona": "travel_agency-<role>", "tools": [...], "model": null}`),
  plus `front_desk.json` (routing table + gated_classes + surface_policy);
  returns a manifest listing all written paths. Recompile is idempotent
  (same bytes). Golden-file comparison for one role end-to-end (spec §5).
- [ ] Implement `compile_profile`.
- [ ] Run; commit `feat(platform): profile compiler (catalog → multiagent artifacts)`.

### Task 3: Regression + wrap-up

- [ ] Full platform suite + whole-repo suite green.
- [ ] `graphify update`; `codex review --base <plan-2 start sha>`; fix findings; commit.

## Out of scope (Plan 3)

Mission loop, manager approval surface, E2E flow, runtime consumption of the
compiled artifacts (blocked by multiagent slice-1 implementation), additional
vertical catalogs, registry/company wiring of `front_desk.json`.
