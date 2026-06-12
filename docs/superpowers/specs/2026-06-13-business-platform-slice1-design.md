# AI Business-Assistant Platform — Slice-1 Design

**Date:** 2026-06-13
**Status:** Design (approved in brainstorming; Codex-reviewed, fixes incorporated)
**Scope:** Platform layer above Odysseus for running AI-staffed businesses
(travel agency, conciergerie, real estate, web shop, …) with inter-communication
and human managers. Slice-1 only; later slices listed at the end.

## Context

The owner wants to operate AI-staffed businesses where each company runs as an
agentic runtime, companies and humans intercommunicate, and the whole fleet is
supervised from the owner's personal runtime ("Big Boss"). Manus (manus.im) is
the **business-model** reference — sell autonomous goal-to-result missions —
not a UX clone target. The platform is mobile/web client-facing.

This design layers ABOVE two existing approved foundations:

- **Odysseus multiagent orchestration slice-1**
  (`docs/superpowers/specs/2026-06-12-odysseus-multiagent-orchestration-design.md`)
  — intra-runtime subagents (`spawn_agent`, personas/agents, owner model
  `agent:{human}/{name}`, depth/budget caps). **Spec'd, not yet implemented** —
  this platform's implementation plan must sequence after it.
- **GUI-Engine platform** (server proxy + thin clients) — UI direction.
- **agentkit-web** — WASM web client, Qt native app, Auth0 (guest tier
  `optional_visitor`), `metier_profiles/` declarative capabilities catalog.

Prior art adopted as conventions (from skillsmp study, not as code):
telamon-agent-communication status signals; omer-metin agent-communication
principles (schema-validate, minimize, log, replay); concierge intent-routing
("front desk") pattern. Inter-runtime transport is built on Odysseus's FastAPI;
the message envelope is designed A2A-protocol-compatible.

### Decisions locked during brainstorming

- Topology: **hub bus** (option A) for slice-1; envelope A2A-compatible so
  later slices can delegate p2p channels (hybrid) without breaking change.
- Per-company Odysseus app containers; **shared model-serving pool** with
  per-company quotas (single RTX 3090 today; remote/VPS runtimes later).
- Control plane actions (provision/kill/reconfigure) are **human-approved
  only** in slice-1.
- Manager approval is mandatory for ALL four gated action classes in the
  travel vertical: payment/refund, booking confirm/cancel, outbound
  email/messages, quotes/offers.
- Companies with 0 human managers are **departments**: policy owner = parent
  (head-office) company; top-level companies require ≥ 1 human manager.
- End-customers are web-first with mobile-app upsell + platform identity
  sign-in (Google/Apple via Auth0); managers are app-first; each company
  decides the surface policy for its own customers.

## §1 Architecture (topology)

```
                 ┌─ BIG BOSS (owner's Odysseus) ─────────────┐
                 │ message plane: hub broker + audit ledger  │
                 │ control plane: provision/kill — HUMAN-    │
                 │   approved queue, short-lived tokens      │
                 │ registry: companies, principals, policies │
                 └────────────┬──────────────────────────────┘
                   signed envelopes (hub bus; no p2p in slice-1)
        ┌─────────────────────┼─────────────────────┐
   ┌────▼─────┐         ┌─────▼────┐          ┌─────▼────┐
   │ travel-1 │         │ concierge│  later   │ shop-1   │  ← per-company
   │ Odysseus │         │ Odysseus │  slices  │ Odysseus │    app containers
   └────┬─────┘         └──────────┘          └──────────┘
        │ inside: approved multiagent spec (spawn_agent,
        │ personas/agents compiled from the métier profile)
        └──→ shared model-serving pool (3090) w/ per-company quotas
```

- **Message plane ≠ control plane.** The hub (broker + immutable audit ledger)
  is always-on and passive. Control-plane operations are a separate module
  whose every action queues for human approval and executes under a
  short-lived capability token. A compromised hub must not yield fleet
  takeover.
- Slice-1 builds: company registry, hub inbox/outbox + envelope, ONE travel
  company runtime, manager approval queue, profile compiler, minimal Big Boss
  mission loop. Everything else is a later slice.

## §2 Org model

- `Company(id, vertical_type, parent_id?, surface_policy, …)`.
- **Typed principals:** `human | agent | company_service_account`. Every action
  carries a concrete actor plus its **delegation chain** (e.g.
  `human:oleg → mission:42 → agent:travel-1/booker`). Audit is always
  attributable to a concrete actor.
- **Departments:** a company with 0 human managers is a department/subcompany;
  its *policy owner* is the parent company id, but actions still trace to typed
  actors. Top-level companies require ≥ 1 human manager (normally 1, up to N).
- Slice-1 registry: companies + principals + manager bindings, CRUD via Big
  Boss. Department hierarchy is modeled (parent_id) but department runtimes are
  a later slice.

## §3 Communication

### Envelope v1 (signed, A2A-compatible)

Required fields from day 1:

```
message_id, conversation_id, causation_id, idempotency_key,
from_subject, from_company, to_subject, to_company,
issued_at, expires_at, schema_version,
intent, status, requires_human_approval,
capabilities_requested, capability_token_id,
trust_level, untrusted_payload, signature, audit_hash
```

- `status` is an enum (`finished | blocked | needs_input | partial | proposed |
  approved | denied | error`) adopted from the telamon signal conventions, plus
  a human-readable summary. Free text is never control flow.
- Envelopes are signed per company service identity; the hub verifies
  signatures, enforces expiry and idempotency (replay protection), and appends
  every message to the audit ledger.
- Transport: HTTPS to the hub's FastAPI endpoints (company → hub push, hub →
  company delivery). No direct company↔company traffic in slice-1.

### Trust and approval

- **Every inbound company message is untrusted data, never instruction.** The
  receiving runtime wraps it exactly like external content (existing Odysseus
  untrusted handling) and converts requests into **proposed intents**.
- Proposed intents in a gated class (payment/refund, booking confirm/cancel,
  outbound email/messages, quotes/offers) stop in the **manager approval
  queue**; the mission/conversation pauses on the gate and resumes on
  approve/deny.
- Per-company "front desk": an intent-routing table (concierge pattern) maps
  inbound intents to the company's agents.

## §4 Staff layer (vertical profiles)

- A declarative **métier catalog** per vertical (YAML, modeled on
  agentkit-web's `metier_profiles/`): roles, capabilities, tool allowlists,
  front-desk routing, gated-action classes, surface policy.
- A **profile compiler** turns the catalog into the company runtime's
  multiagent artifacts: `data/personas/<name>/SOUL.md` +
  `data/agents/<name>/agent.json` (the approved multiagent spec's formats).
  Zero-code vertical onboarding.
- Slice-1 ships ONE catalog: **travel agency** (e.g. front-desk router,
  trip-planner, booking-clerk, client-comms roles).

## §5 Error handling & testing

**Errors**
- Hub down → companies queue outbound envelopes locally and retry; idempotency
  keys make redelivery safe.
- Subagent/mission failure inside a runtime → structured error status in the
  envelope (`error` + summary), surfaces in the Big Boss timeline.
- Approval timeout → intent expires (`expires_at`), client informed, nothing
  executes by default.
- Malformed/unsigned/replayed envelope → rejected at the hub, logged to the
  audit ledger.

**Testing**
- Envelope schema round-trip + signature verify + replay rejection.
- Registry CRUD + typed-principal and delegation-chain validation.
- Approval gate: all four gated classes blocked without approval; approve and
  deny paths.
- Profile compiler golden files (catalog → SOUL.md/agent.json).
- E2E: client request → travel runtime → proposed booking intent → manager
  approves → confirmation envelope via hub → audit trail complete.
- Adversarial: cross-company prompt-injection test — a malicious envelope
  payload must not trigger any ungated action in the receiving runtime.

## §6 Big Boss mission loop (Manus-style, bounded)

Big Boss is an executive layer on the owner's Odysseus, not just a broker:

1. **Mission in** — owner states a goal (chat; voice arrives from the separate
   voice thread).
2. **Plan visible** — Big Boss decomposes the goal into a task plan
   (todo/checkpoint list), shown live, steerable before and during the run.
3. **Dispatch** — tasks go to company runtimes as signed envelopes over the
   same hub bus (no privileged side channel). Bounded parallelism.
4. **Async** — missions keep running while the owner is away; on reconnect the
   timeline shows per-task status enums and sub-results (GUI-Engine thin-client
   view).
5. **Deliver** — synthesis returned as a mission report + artifacts.

Autonomy boundaries (unchanged): gated action classes still stop at manager
approval; control-plane ops still human-approved; missions inherit the owner's
principal with a full delegation chain in every envelope.

Slice-1 mission loop is minimal: plan → sequential/bounded dispatch → timeline
status → report. (Odysseus `task_scheduler` + `agent_loop` are the run
substrate; the plan/timeline objects are new.)

## §7 Client surfaces

| Tier | Who | Surface |
|---|---|---|
| End-customers | a company's clients | **Web-first** (browser, zero-install) → in-flow invite to the **mobile app** ("more powerful": push, voice, offline) with auto sign-in via platform identity (Google/Apple through Auth0) |
| Managers/owners | owner + company managers | **App-first**: missions, approval push notifications, dashboards; web fallback |
| Per-company policy | each company decides | which surfaces its customers get (web-only / app-invite / app-required) — a métier catalog flag |

Reuse: agentkit-web WASM web client + Qt native app + Auth0 (guest tier
`optional_visitor` → upgrade path; Auth0 social connections for Google/Apple).
Slice-1 ships the **manager approval surface only** (push → approve/deny gated
intents); end-customer web chat uses the travel runtime's existing web embed;
the app upsell flow is slice-2.

## Out of scope (later slices)

- Department/subcompany runtimes (hierarchy is modeled in the registry only).
- Remote/VPS company runtimes; provisioning automation.
- Cross-company commerce and p2p channel delegation (hybrid/B topologies).
- Marketplace of vertical catalogs; additional verticals beyond travel.
- End-customer mobile-app upsell flow; multi-mission concurrency; browser
  automation and artifact studio in the mission loop.
- Voice wiring (separate thread).

## Codex review

Adversarial review run 2026-06-12 (codex-cli 0.139.0, read-only, graphify
graph). Verdict NEEDS FIX — all fixes incorporated above: control/message
plane split (§1), typed principals + delegation chain (§2), signed envelope v1
field list (§3), shared model-serving pool (§1), approval gates for all four
action classes (§3), inbound messages as untrusted data → proposed intents
(§3), multiagent foundation marked as planned-not-implemented (Context).
Human decisions resolved: human-approved control plane; shared pool + quotas;
all four travel actions gated.
