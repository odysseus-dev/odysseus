# Misumi passive memory

Misumi Phase A stores local, passive memory under `DATA_DIR/misumi/memory`. It reduces working-memory burden without changing the canonical household repository or initiating external actions.

## Capsule model

A capsule preserves required `raw_text` verbatim and records an extractive `summary`, deterministic `type`, confidence, source, up to two persona owners, entities, optional next action, status, human-confirmation state, and metadata. Timestamps are UTC. Deterministic summaries use the first sentence or a 140-character trim and never exceed 0.6 confidence.

Types are `observation`, `decision`, `inventory`, `blocker`, `preference`, `open_loop`, `experiment_result`, and `note`. Status is `open`, `confirmed`, `routed`, or `closed`.

## Storage

The append-only stores are:

- `capsules.jsonl`
- `open_loops.jsonl`
- `handoffs.jsonl`

Each state change appends a complete record with the same ID and a new `updated` timestamp. Readers fold by ID, latest record wins. Malformed lines are skipped and counted. Directories are created only on the first local write.

## Open-loop detection

Blockers and `open_loop` capsules create loops. The following phrases also create a loop: `all wired up, now for implementation`, `still need to`, `doesn't work`, `blocked`, `I bought`, `remember`, `this worked but`, and `we decided`. Open loops older than `MISUMI_MEMORY_STALE_HOURS` are stale; the default is 72 hours.

## Deterministic routing

Ownership uses keyword hits and returns at most a primary and secondary persona. Kurisu is the default and wins applicable ties.

| Persona | Scope |
| --- | --- |
| kurisu | raw capture, uncertainty, evidence |
| aoteru | routing, coherence, meta-tasks |
| lelouch | implementation, deployment, code, shipping |
| ichigo | hardware, soldering, wiring, safety, repair, maintenance |
| ginko | sensors, plants, humidity, damp, environment |
| sanji | food, recipes, shopping, cooking, ingredients |
| l | budgets, receipts, subscriptions, costs, bank-adjacent words |
| jin | music, records, gigs, vinyl |
| misato | routines, rotas, cleaning, capacity |
| giorno | bounded experiments, pilots, trials |
| erwin | priorities, risk, deadlines, cost of delay |

MPU6050-style part mentions route primarily to Ichigo. Model refinement is optional and off by default; any refined owners remain constrained to the same allowlist.

## Safety posture

Memory is local, append-only, and separate from the household repository. Household access remains read-only. Handoffs are local records and reject outbound side-effect language. There is no email, calendar, notification, webhook, payment, purchase, transfer, message, post, call, or bank action. `writes_allowed` remains false in status and glance responses.

The only autonomy addition is the disabled-by-default, manual-only `memory-digest` pilot. It verifies that the household snapshot is unchanged before writing a local digest.

## Plan consultation

A successful Aoteru model response may synchronously consult at most two relevant personas within the same user-initiated request. The route records one local capsule with source `consultation`, then creates one linked handoff for each persona that returned a genuine contribution. A failed or timed-out persona is logged and receives no fabricated contribution or handoff. Handoff actions use the contribution's first sentence, capped at 200 characters; forbidden outbound-action wording is replaced by the neutral local action `review the plan and contribute next steps`.

Consultation remains Phase A local coordination. It creates no background loop, grants no execution authority, performs no external action, and never writes to the household repository. `MISUMI_CONSULT=false` disables the flow; the default is enabled.

## Completion standard

| Question | Answer surface |
| --- | --- |
| What changed? | `GET /misumi/memory/recent` |
| What did I ask Misumi to remember? | `GET /misumi/memory/inbox` and confirmed note capsules |
| What is unresolved? | `GET /misumi/memory/open-loops` |
| Who owns the next tiny action? | `GET /misumi/glance` → `responsible_persona` |
| What should I not keep in my head? | `GET /misumi/glance` and the local memory digest |
