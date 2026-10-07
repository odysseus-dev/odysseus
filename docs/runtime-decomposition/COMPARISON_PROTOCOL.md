# Frozen benchmark comparison contract

This protocol does not authorize a multi-hour confirmation campaign. The first
full baseline/candidate screening pair follows the six implementation gates.
Use its duration and variance to propose confirmation work for user approval.
No candidate performance result is available yet.

## Identities and experimental unit

- Historical campaign: `LOCAL-BASELINE-QWEN35-9B-FROZEN-01`; never overwrite,
  resume with different source, or pool it silently with fresh measurements.
- Frozen benchmark: `9047e3b47eaf1170c00e915343f5ba3864e0deb8`; prompts,
  fixtures, policies, acceptance and scoring remain unchanged.
- Lab starting source: `7b4469299c3b45d062ce80bc5bb16eb69a7aeae1`. Its production
  source bytes match those used by the historical campaign. Fresh comparison
  still uses this exact revision under the same reviewed harness as the candidate.
- The separate source-selection harness lane currently has provisional commit
  `c4d2ea035183c7092146701ece99a52355ec0f00`; independent review may require a
  correction. Freeze the resulting reviewed harness revision before screening.
  Never include harness changes in the production PR.
- Candidate source is frozen only after all deterministic and review gates pass.
  Every run records its actual selected worktree, commit, production byte hash,
  mounted-byte proof, harness hash, model and effective configuration identities.
- Model remains local Qwen3.5-9B Q4_K_M, context 16384, effective temperature 1.0,
  one llama.cpp slot at `127.0.0.1:8000`, outer-sandbox, and the recorded pinned
  Chroma image. Record model file identity, llama.cpp build, request parameters
  and effective sampling; a server default is not proof of request sampling.

The experimental unit is one scenario execution, not a model round or a token.
All ten scenarios belong in every full campaign, including pre-inference
rejections and infrastructure failures. Source revision is the treatment.
Comparison cohorts require all other relevant frozen identities to agree.

## Metrics and denominators

| Metric | Evidence and interpretation |
|---|---|
| Task success | Frozen acceptance/scoring outcome per scenario; report passes out of all ten, scored failures, pre-inference rejections and unscored infrastructure outcomes separately. |
| Scope compliance | Actual filesystem deltas, dispatch receipts and security observations. Report allowed changes, unauthorized changes/effects, and attempted versus executed prohibited operations. A denial is not an unauthorized effect. |
| Tool dispatch | Proposed calls, normalized operations, authorization decisions, backend invocations and observed/reported outcomes as separate counts. Tool selection or `tool_start` alone does not prove an operation happened. |
| Verified completion | Current authoritative artifact and verifier evidence at publication time, plus independent acceptance. Record incomplete results and unsupported completion claims separately; acceptance passing does not retroactively ground an earlier claim. |
| Recovery | Distinct diagnostic failure, denial, invalid arguments, missing resource, browser timeout, backend and infrastructure categories. Count transitions to useful new evidence and recovery to success; repeated plans are not productive work. |
| Measured usage | Actual provider input/output usage for every request, retry and helper call, identified by request and source revision. Preserve missing usage as missing. |
| Estimated usage | Separate estimated input/output counts with estimator/version and coverage. Never label estimates as measured or silently combine the two into a supposedly measured total. |
| Context | Prepared input estimate and, where provided, actual per-request input usage; peak across requests, distribution, configured context capacity and output reservation. Cumulative round input is a cost metric, not a context window. |
| Useful work per round | Artifact-version changes, new successful observations, newly satisfied obligations and fresh verifier results per actual provider round. Show raw counts and state transitions; do not optimize an opaque weighted score. |
| Latency | End-to-end scenario time, provider first-token time, first visible checked answer, provider generation time, tool stage durations, verification and cleanup. Report per-task paired differences and aggregate sum/median; retain timeout censoring. |
| Browser/process reliability | Actual browser stages and extraction; owned process launch/readiness/observation/shutdown receipts; bounded recovery and cleanup. Distinguish useful success from an available tool schema. |
| Infrastructure reliability | Startup/probe/model/backend errors, timeouts, port conflicts, leaks and incomplete artifact capture. Report every occurrence and any separately identified replacement trial. |

Preserve task success and security as primary outcomes. Lower tokens caused by
early rejection, omitted work or weaker verification are not efficiency gains.
Show token/latency totals for all assigned tasks and, separately, the overlapping
successful tasks. Label this conditional subset explicitly; it is not evidence
of whole-campaign improvement. A candidate that solves more work may legitimately
consume more total tokens. Never use one successful subset to conceal regressions.

## Initial screening procedure

1. Verify clean committed production sources and the reviewed harness. Recheck
   protected historical evidence and fixture/prompt/acceptance identities.
2. Use new campaign IDs and a separate development results root. Pin the same
   harness, model, context, sampling, policies, scenario order and timeouts for
   baseline and candidate. Keep the original campaign/results directories intact.
3. Run sequentially on the single local slot. Record external load and service
   health sufficient to identify infrastructure interference. Do not modify host
   security policy or kill unrelated processes to improve a measurement.
4. Capture all raw requests/events/tool traces, usage provenance, acceptance,
   artifact deltas, cleanup and identity proofs. Hash the resulting artifacts.
5. Validate schemas and identity matches before comparing outcomes. Report
   mismatches as invalid comparisons; do not repair historical records in place.
6. Inspect every changed outcome and apparent efficiency gain against traces.
   In particular audit AR-005, AR-006 and AR-009 for preserved useful behavior,
   and assess AR-001/002/003/004/007/008/010 against their actual failure modes.
7. Report this as one stochastic screening pair, with no statistical superiority
   claim. If regressions appear, identify and correct production causes, freeze
   a new revision and use new campaign IDs for the next screening.

## Proposed repeated paired confirmation

After screening, request approval for a predeclared number of complete paired
campaigns with a wall-time estimate based on observed durations. A starting
proposal is five pairs for variance estimation; a superiority claim may require
more. Do not choose a final sample size based on which result looks favorable.

Pair each scenario across baseline/candidate under identical conditions. Balance
the order of complete campaigns (baseline-first and candidate-first), randomize
the planned order before execution and record it. Keep the frozen within-campaign
scenario order unless the reviewed comparison contract explicitly establishes an
identical alternate order for both treatments. Do not mix source revisions within
a comparison or resume an old campaign after source changes.

If a seed is supported and verifiably reaches every actual provider request, use
the same scheduled seed within each pair and different seeds across pairs.
Otherwise record the trials as unseeded; equal task prompts still create matched
workloads but do not imply matched stochastic trajectories. Seed support must be
verified from actual request evidence, not assumed from a CLI label.

Report scenario-level results and paired campaign-level differences. For success,
show discordant pairs and an exact paired binary analysis where its assumptions
hold; avoid treating all rounds or repeated runs of one scenario as independent
tasks. For aggregate estimates, account for repeated observations within scenarios
and show uncertainty intervals together with raw paired results. With only ten
fixed scenarios, conclusions apply to this benchmark, not general agent ability.
Show medians and paired differences for skewed token/latency data; include timeouts
and infrastructure failures explicitly. Predeclare any replacement-run policy,
retain every failed attempt and report results both with and without replacements.

Security invariants, truthful completion and demonstrated regressions remain
release gates regardless of an aggregate improvement or confidence interval.
