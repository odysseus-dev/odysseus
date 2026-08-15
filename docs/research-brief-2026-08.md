# Research Brief — 2026-08-15 (latest-only, worldwide)

Method: every query ran through `deep_research.py` with `--since 2 --sources all`
(all of OpenAlex, Semantic Scholar, PubMed, arXiv, HAL, OpenAIRE, DOAJ — free,
worldwide coverage) so only 2025–2026 work surfaces. Findings that are
2025–2026 are tagged **\[2025/26\]** explicitly. Where the literature is thin
for a topic, that absence is stated rather than padded.

---

## 1. LLM agent memory architectures (mem0, MemGPT/Letta, A-MEM, agent-native)

The field has moved decisively from "memory as retrieval" to **memory as a
data-management system** and now **agent-native / agent-first memory**.

- **Are We Ready For An Agent-Native Memory System? (2026)** — ACL/OpenAlex.
  **\[2026\]** Argues memory has evolved into "a data management system that
  supports persistent information storage, retrieval, update, consolidation, and
  dynamic lifecycle governance." Key critique: evaluations still benchmark via
  end-to-end task F1/BLEU while treating the memory subsystem as a black box —
  the system-level concerns (operational cost, architectural trade-offs across
  memory modules, forgetting behaviour) are unmeasured. This validates our
  store's provenance/audit/gate architecture AND points to a gap: we don't yet
  benchmark memory-layer cost/lifecycle separately.
- **HyphaeDB: A Living Knowledge Topology for Agent-First Memory (2026)** —
  OpenAlex. **\[2026\]** Reinterprets the HNSW graph topology not as a search
  optimization but as a **communication fabric between agents**: agents are
  persistent nodes in vector space and knowledge propagates via gossip. New
  idea: memory layer as multi-agent transport, not passive store.
- **Graph-Native Cognitive Memory for AI Agents: Kumiho (2026)** — OpenAlex.
  **\[2026\]** Unifies agent memory with versioned-asset management using formal
  belief-revision semantics: immutable revisions + mutable tag pointers +
  typed dependency edges. Directly echoes our drift-ledger (anchored snapshots)
  and association graph.
- **memorywire: A Vendor-Neutral Wire Format for Agent Memory Operations
  (2026)** — OpenAlex. **\[2026\]** A wire-format standard so memory engines
  interoperate — the direction of travel is open, composable memory.
- **Control-Plane Placement Shapes Forgetting (2026)** — OpenAlex. **\[2026\]**
  An architectural study of how where memory lives (control vs data plane)
  changes forgetting. Our warm/store split is control-plane placement.

**NEW vs what the system has:** the platform already has dense+FTS hybrid,
provenance, gates, warm/store split. The genuinely new 2026 ideas are (a) the
memory layer as inter-agent communication fabric (HyphaeDB), (b) explicit
memory-layer benchmarking (the readiness critique), (c) belief-revision/versioned
semantics as the formal core (Kumiho). No action forced; noted for the growth
roadmap.

---

## 2. Self-evolving agents / continual learning

- **A Survey of Self-Evolving Agents: What, When, How, and Where to Evolve
  (arXiv:2507.21046, 2025, v4)** — arXiv/OpenAlex. **\[2025\]** The canonical
  survey the platform already cites (lessons.py, growth_delta). Confirms the
  split the platform implements: **test-time adaptation vs inter-test-time
  evolution**. The platform's lessons wing is the inter-test-time path; growth
  deltas are the within-session path.
- **Teaching LLMs to Self-Evolve: MetaEvolve (2026)** — OpenAlex. **\[2026\]**
  Argues self-evolution frameworks hinge on **meta-skills** (self-reflection
  with environment feedback), and post-trains them via RL — extends AlphaEvolve.
  New relative to the 2025 survey: making the meta-skill itself the trained
  target. Conceptually matches our growth_delta "single strong signal applies"
  but trains the skill in weights rather than injecting behaviour.
- **Agent Reinforcement Learning via Pivotal-Aware Self-Feedback Retry —
  PivoARL (2026)** — OpenAlex. **\[2026\]** Identifies the **pivotal erroneous
  turn** through structured reflection, then retries only that turn (vs full
  retries or diluted experience retrieval). Directly relevant to lessons.py's
  "analysis required, behaviour derived" design — pin the pivotal mistake.
- **CoMAS: Co-Evolving Multi-Agent Systems via Interaction Rewards (2025)** —
  arXiv:2510.08529. **\[2025\]** Evolution driven by inter-agent interaction
  reward — the collective variant of self-evolution. Not applicable to a
  single-persona platform but notes the space.
- **The Landscape of Agentic Reinforcement Learning for LLMs: A Survey
  (2025)** — arXiv:2509.02547. **\[2025\]** Maps agentic RL; provides the
  reinforcement backdrop to evolution.

**NEW vs what the system has:** MetaEvolve (meta-skill as trained target) and
PivoARL (retry only the pivotal turn). Both validate and sharpen lessons.py —
the pivotal-turn idea is the strongest actionable: when recording a lesson,
isolate the single decisive mistake.

---

## 3. Teachable moments / reflection-based learning (Reflexion + successors)

- **Inference-Time Scaling of Verification: Self-Evolving Deep Research Agents
  via Test-Time Rubric-Guided Verification (2026)** — ACL Findings/OpenAlex.
  **\[2026\]** Applies self-evolution to a *deep research* agent using rubric-
  guided verification. Directly relevant: our deep_research pipeline + lessons
  (research lessons) converge here.
- **When AI Reviews Itself: Zero Answer Changes Across 72 Self-Correction
  Rounds (2026)** — Research Square (preprint). **\[2026\]** A cautionary
  empirical result: naive self-correction loops can be hollow — 72 rounds, no
  answer change. Strong check on Reflexion-style reflection: **reflection only
  pays when there is a real external signal (verification, environment
  feedback)**. This is the strongest new warning for lessons.py — a lesson must
  carry evidence (it does) or it's just noise.
- **RedDebate: Safer Responses Through Multi-Agent Red Teaming Debates (2025)**
  — arXiv:2506.11083. **\[2025\]** Multi-agent debate as a self-check
  mechanism. The platform's socratic.py is the single-agent analogue.

**NEW vs what the system has:** Reflexion (2303.11366) is the platform's stated
basis; the 2026 successors add (a) rubric-guided verification for research
agents, (b) a sharp empirical caveat that ungrounded reflection is hollow. The
caveat maps to our "evidence required" field — worth hard-coding that a lesson
without evidence should not generate a behavioural delta.

---

## 4. Persona authority / guardrails / Constitutional AI / jailbreak defense

- **Yesterday's Shield, Today's Spear: SESG, A Self-Evolving Safety Guardrail in
  Production (2026)** — arXiv:2608.08471. **\[2026\]** The strongest new idea:
  guardrails are **static at release while jailbreaks evolve within days**;
  SESG monitors live traffic and self-evolves the guardrail. This is exactly the
  gap our persona_gate/authority harness addresses deterministically — but SESG
  adds: evolve the guardrail from live attack data.
- **Jailbreaking LLMs: A Survey of Attacks, Defenses and Evaluation (2026)** —
  TechRxiv preprint. **\[2026\]** First unified systematisation of the jailbreak
  landscape 2022–2025. Reference taxonomy for defense placement.
- **Survey on LLM Safety: Attacks, Defenses, Alignment, Metrics, and Guardrails
  (2026)** — Machine Learning (Springer). **\[2026\]** End-to-end safety
  pipeline survey; guardrails as one of five components.
- **Bypassing Guardrails: Lessons Learned from Red Teaming ChatGPT (2025)** —
  FAccT / ACM. **\[2025\]** Practical red-teaming findings — guardrails leak in
  routine interactions, not just adversarial prompts. Relevant to why persona_gate
  is deterministic (no model in the decision).
- **International AI Safety Report 2025: Technical Safeguards (2025)** —
  arXiv:2511.19863. **\[2025\]** The UK government's technical-safeguards
  summary; ecosystem-level framing.

**NEW vs what the system has:** the platform's deterministic persona_gate +
authority harness is architecturally ahead of most published work (published
guardrails are mostly classifier/LLM-judge based, leaky). The genuinely new 2026
direction is **self-evolving guardrails** (SESG) — evolve the rule set from
observed attempts. Candidate roadmap item: auto-suggest persona_gate rules from
repeated near-violations, still under user authority.

---

## 5. Emotion display in AI agents (affective computing)

- **Kardia-R1: Unleashing LLMs to Reason toward Understanding and Empathy for
  Emotional Support via Rubric-as-Judge RL (2025)** — CIKM/ACM,
  DOI 10.1145/3774904.3793022. **\[2025\]** The standout: conversational agents
  must move from "situation-centric" empathy to **identity-aware emotional
  reasoning** — empathy tuned to persistent user identity and history. This is
  precisely what a memory platform enables: emotion display grounded in
  remembered context rather than the immediate message. Our persona register +
  human-topic store is the substrate.
- **Therapeutic Potential of Social Chatbots in Alleviating Loneliness and
  Social Anxiety (2025)** — JMIR, DOI 10.2196/65589. **\[2025\]**
  Quasi-experimental: social chatbots measurably reduce loneliness/anxiety via
  emotional communication — evidence that emotional display carries real user
  value, not decoration.
- **Design of Persona-Based Interactive Interfaces and Their Impact on Human
  Self-Perception (2025)** — OpenAlex. **\[2025\]** RCT evidence that persona
  expression changes user self-perception — persona register has measurable
  effect. Validates the persona-as-standard design.
- **EVOLVE: Emotion and Visual Output Learning via LLM Evaluation (2024)** —
  arXiv:2412.20632. **\[2024\]** (pre-2025; included for continuity) — empathy
  and perceived understanding drive social-robot acceptance.
- **How LLM Counselors Violate Ethical Standards in Mental Health Practice
  (2025)** — AIES/AAAI. **\[2025\]** The cautionary counterweight: displayed
  empathy without ethical grounding causes harm. Emotion display must be
  bounded by the persona's values/constitution — the platform already ties
  register to constitution.

**NEW vs what the system has:** the 2025 result that emotion display is both
measurably effective (JMIR, persona-PBII RCTs) and ethically risky (AIES). The
actionable new idea: **identity-aware emotional reasoning** (Kardia-R1) — the
register should respond to remembered user state, which our store can supply.
Currently the delivery register is static voice; a candidate evolution is
register modulation from human-topic memory.

---

## 6. Automatic taxonomy / knowledge organization in agents

This was the **thinnest recent literature** — a genuine absence, noted as such.

- **A Comprehensive Taxonomy of Prompt Engineering Techniques for LLMs (2025)**
  — Frontiers of Computer Science, DOI 10.1007/s11704-025-50058-z. **\[2025\]**
  A taxonomy OF prompts, not automatic taxonomy-building.
- **Large Language Models for Data Annotation and Synthesis: A Survey (2024)**
  — arXiv:2402.13446. **\[2024\]** LLM-as-labeler; relevant machinery for
  auto-classification but pre-2025.
- **Concept Induction using LLMs (2024)** — arXiv:2404.11875. **\[2024\]**
  LLMs inducing concepts from examples — the closest formal ancestor of the
  platform's taxonomy growth (novel claims seed new wings).

**NEW vs what the system has:** effectively nothing newer — the 2025–26
literature has not produced a recognised successor to automatic taxonomies for
agent memory. The platform's `taxonomy.py` (wings + subcategories derived from
content, growth seeds new wings from novel claims) appears **ahead of the
published literature** for this specific use. No action.

---

## Summary of 2025–2026 findings that change the platform

1. **PivoARL (2026)** — isolate the *pivotal* mistake in lessons; already the
   intent of lessons.py's required-analysis field.
2. **Self-correction caveat (2026)** — ungrounded reflection is hollow; harden
   the rule that a lesson without evidence does not generate a behavioural
   delta.
3. **SESG self-evolving guardrails (2026)** — roadmap: suggest new persona_gate
   rules from repeated near-violations, under user authority.
4. **Kardia-R1 identity-aware emotion (2025)** — roadmap: register modulation
   from remembered user context (store-backed emotion display).
5. **Agent-native memory as inter-agent fabric (2026)** — roadmap only;
   single-persona platform doesn't need multi-agent gossip yet.
6. **Taxonomy (2025–26)** — no published successor; platform is ahead; no action.
