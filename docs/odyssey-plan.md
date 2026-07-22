# Odyssey — Home OS layer on Odysseus

**What this is:** a personal life-management "home OS" for you and your wife, built as a
thin layer on top of the Odysseus self-hosted AI workspace you already develop. Odysseus
provides the primitives (agent, models, calendar, tasks, memory, documents, mobile PWA,
auth); **Odyssey** is the data + skills + templates that turn those primitives into a tool
that manages routines, recipes, the weekly plan, and a budget — digital *and* printed.

Naming: **Odysseus** = the platform. **Odyssey** = the life-OS layer on top.

---

## Architecture decisions (settled)

### 1. Layer on top — not a fork, not new core features
Life-management lives as **data + skills + templates** riding on existing Odysseus
primitives. Core Odysseus stays untouched until a rough edge proves something must go
deeper. Cheapest, most reversible foundation; keeps us upstream-compatible.

```
data/life/
  routines/night.md      recipes/tacos.md
  weekly/2026-W30.md      registry.json   <- agent reads first: "what exists"
templates/  recipe.html routine.html weekly.html daily.html
styles/     design.css (@media screen)  print.css (@media print, B&W)
skills/     log-expense/ update-recipe/ weekly-review/ daily-review/ print-artifact/
```

### 2. Data substrate — files canonical, indexed for retrieval
Human-readable files under `data/life/` are the **one source of truth**. A `registry.json`
manifest lists every domain + artifact so the agent always knows *what exists*. On save,
each file is auto-indexed into the existing Chroma/RAG so vector + keyword search finds it
months later. Structured/tabular data (expenses, metrics) lives in SQLite / Actual Budget.
Files = truth; vectors = derived index. `[LAW:one-source-of-truth]`

### 3. Two-tier agent — Operator vs Builder `[LAW:one-way-deps]`
- **Operator** (ongoing, hot path): the routed cheap agent. *Operates* the life OS —
  logs expenses, updates recipes, builds weekly reviews, prints. **No *unsupervised* code
  changes.** It MAY make small, low-risk self-improvement hacks, but only after a
  **verify-with-me gate**: it proposes the change and asks whether to (a) do it itself now,
  or (b) delegate to a Builder. Anything non-trivial always goes to the Builder + review.
  The invariant preserved is "no code changes the human didn't approve," not "the Operator
  never touches code."
- **Builder** (occasional, off hot path): Claude Code / OpenCode + **Grok 4.5** (existing
  subscriptions). *Improves & hardens* the system's own code via the git worktree ->
  draft PR -> review loop. Human-gated merge.
- **Trigger model — both:** you can explicitly tell the Operator to dispatch a Builder now,
  **or** gaps accumulate as a **system-improvement backlog** worked later in batches.
  Either way it ships as a draft PR you approve. Half-wired already via
  `integrations/claude` and `integrations/codex`.
- **Operator self-improvement gate:** for small hacks the Operator itself can do, it always
  asks first and offers do-it-now vs delegate-to-Builder; the backlog is the delegation sink.

### 4. Model brain — privacy-aware router via LiteLLM
Sensitivity is a **policy tag, not an ML classification**. The agent knows when it touches
finance/private data because it loaded that context; it tags the request and LiteLLM pins
routing by config. **Default-deny: unknown -> local**, so nothing leaks. `[LAW:single-enforcer]`
- **Local (sensitive + everyday):** on-device model via Cookbook on the M4 Max / 48 GB
  (Hermes-3 is a strong function-calling candidate; confirm fit + latency first).
- **Cloud (hard, non-sensitive):** **Gemini Flash** primary (cheap, contractually doesn't
  train on API data), GPT-mini fallback. Well under **$10/mo**.
- **Avoid:** DeepSeek + OpenRouter free tiers (train/log data); RouteLLM / Not Diamond /
  OpenRouter-Auto (route on *difficulty*, and by sending prompts to the cloud — wrong axis
  for privacy). OpenHands & Open Interpreter both already use LiteLLM — well-worn path.

### 5. Phone interface — PWA + Telegram + ntfy
- **PWA** (installable, existing) over **Tailscale** for chat, dashboards, anything you want
  fully local. **ntfy** for push reminders.
- **Telegram** for frictionless quick capture from anywhere, incl. **receipt photos**.
  Accepted tradeoff: a receipt is low-sensitivity (no card numbers) and transiting Telegram
  is fine; parse it with cloud vision (Gemini Flash) for good OCR.
- **Privacy boundary that stays honest:** individual receipts may transit cloud, but the
  **aggregate financial picture** (totals, "how am I doing this month") is computed
  **locally** against the Actual ledger — the whole-life view never leaves the Mac.

### 6. Finance — integrate Actual Budget
**Actual Budget** (open-source, local-first envelope budgeting, own dashboard + API) is the
ledger. Odysseus is the **capture layer**: message / photo -> local (or cloud-vision for
photos) parse + categorize -> write to Actual via API. Great dashboard for ~zero build;
aggregate analysis stays local.

### 7. Surfaces — template library + one design system
A small set of canonical HTML templates (recipe, routine, weekly-plan, daily-review) the
agent fills from a data model, plus **one shared stylesheet with screen AND print (B&W)
styles**. Recurring artifacts reuse a liked template (cheap on tokens — fill a slot, don't
redesign); novel one-offs are free-form HTML pulling the same stylesheet. Print-ready by
construction.

### 8. Printing — direct access, PDF-first until hardware is up
Agent prints directly via macOS CUPS (`lp`) to the default printer — supports both
**scheduled** (Sunday binder run: weekly plan + recipes + routines) and **on-demand**.
Render path: template + `print.css` -> PDF (headless Chrome) -> `lp`.
**Printer not connected yet** (post-move): build the pipeline to output **PDFs now**
(fully verifiable), activate the final `lp` step once the printer is on the new network.

### 9. Always-on — MacBook, keep-awake
Run Odysseus as a launchd background service on this MacBook; disable sleep on power so it
runs lid-closed while plugged in. $0, no extra hardware. Because it's service-ready + reached
over Tailscale, a future move to a dedicated always-on box (Mac mini) would be a config move,
not a rewrite.

### 10. Calendar — Google Calendar (shared household)
Chosen for zero-effort sharing with your wife and native phone access; Odysseus syncs via
CalDAV/API and derives the weekly-review printout from it. Note: this is the one non-local
piece — keep genuinely private notes out of event bodies; a dedicated "household" calendar
keeps scheduling separate from anything sensitive.

---

## Suggested build order (phased)

1. **Substrate:** `data/life/` + `registry.json` convention + auto-index-on-save into Chroma.
2. **Design system:** `styles/design.css` + `print.css`; one template end-to-end (recipe)
   rendered to PDF. Verifiable without a printer.
3. **Operator skills:** `update-recipe`, `weekly-review` (from calendar), `daily-review`.
4. **Router:** LiteLLM config — `local-sensitive`, `local-default`, `cloud-hard` groups +
   tag routing + budget cap. Cookbook-serve the local model.
5. **Finance:** stand up Actual Budget; `log-expense` capture skill (text + receipt photo via
   Telegram -> cloud vision -> Actual API).
6. **Interfaces:** Telegram bot listener; Tailscale; ntfy reminders; launchd keep-awake service.
7. **Printing hardware:** wire `lp` once the printer is on the new network; scheduled binder run.
8. **Builder loop:** system-improvement backlog + explicit-dispatch path -> draft PRs.

## Open items to confirm later
- Exact local model (Hermes-3 vs alternative) after Cookbook fit test on the M4 Max.
- Whether the weekly-review printout format wants a first draft before templating it.
- Backup strategy for `data/life/` + Actual (deferred by choice).
