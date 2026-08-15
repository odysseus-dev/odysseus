# Phase 3 Feasibility Gate — Operator / Voicebox Split

**Status: SPEC-ONLY. NOT IMPLEMENTED.** This document defines the strict
threshold that must be met before any Phase-3 code is written. The design is
for reference; nothing here is executed.

## What Phase 3 claims

The persona (memory + compiled skills) operates on **procedural memory that runs
without the LLM**; the LLM is invoked only as a *voicebox* for novel steps and
natural-language generation. Goal: growth that **limits the need for LLMs**.

## Why it is gated (the honest assessment)

The supporting research is **pre-2026 prototypes, not production**:
- Memp / LEGOMem / CodeMem / "Managing Procedural Memory in LLM Agents" — 2025–2026,
  mechanism demonstrations (routines stored and re-run), no demonstrated end-to-end
  adaptive self-compilation with reliability guarantees.
- MetaSkill-Evolve — recursive self-improvement, but explicitly warns that
  unbounded recursive improvement needs control; benchmark-only.
- Voyager — skill libraries bypass repeated reasoning, but skills are curated for
  one environment (Minecraft) and still assume the model for novel steps.

**No published system has demonstrated the full operator/voicebox split with
(1) adaptive compilation, (2) reliability, and (3) measured reduction in LLM
dependence.** Therefore Phase 3 does not meet the evidence bar yet.

## The gate criteria (ALL must pass)

1. **Reliability**: the persona operates via compiled skills for >= 3 distinct
   recurring tasks with >= 30 consecutive correct executions, no LLM fallback,
   zero silent failures.
2. **Measured uplift**: hard evidence (logs, not anecdotes) that compiled
   execution reduced LLM calls for those tasks by >= 50% WITHOUT a measurable
   drop in service quality (user-visible quality score held or improved).
3. **Adaptive correctness**: when the environment changes (e.g., tool output
   format changes), the compiled skill must detect the drift and trigger the
   slow-loop re-derivation — not silently fail or run stale procedures.
4. **Graceful fallback**: any failure in compiled execution falls back to the
   LLM-in-the-loop path automatically, with the failure recorded as evidence
   for the slow loop.
5. **Auditability**: every compiled run is journaled (when, which skill, input
   hash, outcome) so "the persona did X without an LLM" is always provable.

## What Phase 1 + 2 already build toward it

- **skill_library.py** implements the trusted -> executable promotion with the
  2-reward gate (Phase 2). This is the *mechanism* Phase 3 would scale, but
  Phase 2 executes compiled skills only when explicitly invoked (tool call),
  not autonomously as the operating layer.
- **growth_delta.py** implements the fast loop (single-signal behavioural
  adaptation). Phase 3 would need the slow loop (improvement-method evolution)
  per MetaSkill-Evolve before autonomous operation is safe.

## Explicit non-goals until gate passes

- No autonomous skill execution on the critical path.
- No compiled skill may bypass an existing gate (worthiness, strength, coherence).
- No reduction in LLM calls is *claimed* until criterion 2 is measured.

## Review cadence

Re-evaluate the gate when either: (a) a published system demonstrates the full
operator/voicebox split with reliability + measured reduction, OR (b) this
deployment accumulates the evidence in criteria 1–5 organically via Phase 2.
Whichever comes first, implementation requires explicit user approval and this
document's gate criteria copied into the new spec with evidence attached.
