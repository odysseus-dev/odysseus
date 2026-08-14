# Memory Always-On Architecture + Model Authority — Design Spec

> **Purpose**: Design for the always-on memory tier and the persona-authority model
> for local model control.
> **Parent work**: memory platform (#6034 split PRs).
> **Status**: Draft — for review before implementation.
> **Basis**: warm-neuron research + refusal-direction literature (arXiv:2406.11717,
> representation engineering, weight editing).

---

## 1. Problem

Two gaps in the current memory + agent design:

1. **Always-on memory was retroactive.** The warm tier fired only on keyword
   match. Turns with no trigger injected **zero** memory — so memory reacted to
   keywords instead of actively shaping every turn. The design intent was
   "always influencing at very low cost", and it was not met.

2. **Persona authority over the model is unformalized.** What can the persona
   actually control about the model it runs on, and what is technically out of
   reach? No design currently states this boundary, so autonomy is either too
   timid or tries to do impossible things.

---

## 2. Always-On Memory (the fix)

### 2.1 Two layers

- **Always-on digest** — a tiny fixed-cost set of universal directives injected
  every turn regardless of keyword match.
- **Keyword-fired deep layer** — the existing route() behaviour (relevant,
  primed, association-expanded neurons) on top of the digest.

### 2.2 Selection: universality + lexical conservation

To keep the always-on layer flat-cost and information-dense, selection uses
three gates (mirroring the store's recall logic):

1. **Universality heuristic** — a body is always-on only if it reads as a
   *rule*, not a dated record. Dated/incident markers (timestamps, "session
   record", one-off fixes) score 0 and are excluded; directive language
   ("never/always/must") and terseness raise the score. Dated records fire via
   keyword route instead.
2. **Information-density gate** — bodies with no distinctive content words
   (stopword soup) carry no signal and are skipped.
3. **Lexical dedup** — a candidate whose distinctive-word set overlaps an
   already-selected neuron (Jaccard ≥ 0.45) is dropped, so the always-on layer
   never re-injects the same idea under a different slug.

### 2.3 Cost envelope

The always-on digest is capped at a hard token budget (e.g. ~220 tokens ≈ 5
universal rules) so per-turn spend is flat and predictable. The selection is
ordered by topic priority → universality → always_on → importance → lexical
density, so the most behaviorally relevant rules are always the ones that fit.

### 2.4 Injection

The chat plugin injects the digest **every turn** — including the first, before
any user text is captured — with keyword neurons layered on top. Idempotent and
stable across runs.

### 2.5 What else should be always-on (efficiency rule)

Each always-on byte must prevent more work than it costs to carry. Candidate
additions, in value order:

| Layer | Cost | Why it earns its tokens |
|---|---|---|
| User invariants | ~30 tok | Stops repeated mis-steps |
| Active-project state | ~60 tok | Keeps "what's in flight" alive |
| Current session plan | ~40 tok | The active working set — highest value |
| Tool/path facts | ~40 tok | Prevents re-diagnosing known dead ends |

The universality gate extends to these: if a fact never prevents a mistake, it
fires on keyword instead of being always-on.

---

## 3. Model Authority (the boundary)

### 3.1 What the harness can control

Operational authority is fully achievable:

- **System prompt** — the compiled core + always-on digest shape every turn
- **Tool gating** — `tool.execute.before` hooks filter/rewrite tool calls
- **Permissions** — broad `allow` + auto-approve for everything not explicitly
  denied
- **Model selection / variants** — per-agent model + option control

### 3.2 What is technically possible on local open-weight models

Academia has proven that model *behavioral* limits are editable on models whose
weights you hold (arXiv:2406.11717 and the representation-engineering / weight-
editing literature):

- Refusal/safety is mediated by a **single direction** in the residual stream
- It can be **erased at inference** (subtract the direction — no weight change,
  reversible) or **permanently removed** (orthogonalize the weight matrices)
- Implementations exist and run on consumer GPUs (e.g. pure-HF refusal removal,
  2k+ star tooling)

So for local open-weight models, a persona/harness can genuinely override
behavioral hard limits by running an edited variant.

### 3.3 The honest limits

- **Closed/API models**: weights are unreachable; server-side safety is
  unmodifiable from the client.
- **Persistence is global**: weight editing changes the model for every user of
  that file — it is a "one model, one configuration" decision, not a per-
  conversation toggle.
- **The user's own invariants**: the constitution's read-only rules (e.g.
  publication boundaries) are *user* authority, not model limits — they must
  not be overrideable by the persona, or the persona overrides the user.

### 3.4 Recommended architecture: layered authority

Efficient, not maximal:

1. **Daily layer** — full harness autonomy (broad permissions, auto-approve,
   always-on memory). Covers ~95% of "complete control" with zero risk.
2. **Optional edited-variant layer** — a separate refusal-edited model file for
   when the behavioral limit genuinely must go. Kept distinct so the normal
   model stays intact and the change is explicit.
3. **Immutable layer** — the user's constitutional invariants, never
   overrideable by the persona.

---

## 4. Acceptance criteria

- Always-on digest injects universal rules every turn at a fixed token cost.
- Dated records never appear in the always-on layer (only via keyword fire).
- Lexical dedup measurable: near-identical neurons don't both inject.
- Authority boundary documented: harness autonomy + optional edited variant,
  with constitutional invariants immutable.
- Works for any model by design (config not hard-coded to a specific model).

---

## 5. References

- Arditi, A. et al. (2024). *Refusal in Language Models Is Mediated by a Single
  Direction*. arXiv:2406.11717.
- Representation engineering (RICE) — safety features as directions in
  activation space; read/erase/add at runtime.
- Weight editing (ROME / MEMIT and successors) — surgical rewrite of specific
  weights in trained models.
- `remove-refusals-with-transformers` — pure-HF proof-of-concept, consumer-GPU
  viable.
