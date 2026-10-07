# SFT Expansion Launch Ledger

## Immutable Inputs

- Approved seed manifest: `tmp/sft_expansion_20260830/frozen_v1/seed_manifest.json`
- Approved seed turns: `tmp/sft_expansion_20260830/frozen_v1/approved_trace.jsonl`
- Environment inventory: `tmp/sft_expansion_20260830/environment_inventories.json`
- Seed baseline: 561 sessions, 1,372 approved turns
- Expansion selection: `tmp/sft_expansion_20260830/selected_200_manifest.json`
- Selection size: 50 owner-bound families, projected to 200 environment cases

## Acceptance Policy

1. Generate only from inventory facts and marker-scoped reversible mutations.
2. Reject unsupported tools, temporal contradictions, vague calendar mutation times, and compound mutation-plus-verification prompts.
3. Run each turn through the real 7011 agent surface with Kimi K3.
4. Require the expected tool and manager action, successful tool output, a nonempty answer, and no internal narration or false-unavailability claim.
5. Restore owner state after every case and delete mechanically failed sessions.
6. Review every mechanically passing session with DeepSeek.
7. Retain only `keep` verdicts scoring at least 80. Delete all other sessions and trace rows; repair by regenerating and replaying, never by inventing missing tool evidence.
8. Preserve the source seed family's train/validation/test split.

## Pilot Record

- Harness regression fixed: contextual calendar entries no longer route to `manage_tasks`.
- Harness regression fixed: contextual calendar reads require fresh `manage_calendar` evidence.
- Runner regression fixed: a manager tool name alone no longer passes; create/list/update/delete actions are checked.
- Generator regression fixed: calendar mutations require an exact time or `ask_user`.
- Unsafe global skill-directory rollback replaced with marker-scoped cleanup.
- Omar calendar lifecycle: deterministic pass, DeepSeek `keep`, 96/100, retained.
- Ambiguous Omar predecessor: DeepSeek `repair`, 60/100, deleted from app and trace.
- Maya ambiguous-date lifecycle: deterministic reject, deleted before review.

## Current Corpus

- Build: `tmp/sft_expansion_20260830/corpus_v2`
- Train: 994 turns
- Validation: 157 turns
- Test: 135 turns
- Sessions: 562
- Retained expansion sessions: 1
- Seed-family leakage: 0

## Clean Source Baseline

- Immutable source remains unchanged: `tmp/sft_expansion_20260830/frozen_v1/approved_trace.jsonl`
- Deterministic hygiene input: 561 sessions, 1,372 turns
- Deterministic hygiene result: 421 sessions, 859 turns retained; 140 sessions rejected
- Full DeepSeek semantic review: 336 keep, 77 repair, 8 delete
- Kimi repair result: 73 validated repaired sessions; 4 additional sessions excluded
- Clean derivative: `tmp/sft_expansion_20260830/clean_v2/approved_trace.jsonl`
- Clean derivative size: 409 sessions, 834 turns
- Post-repair deterministic recheck: 409/409 sessions pass
- Style contract: `docs/sft-style-contract.md`
- Assembly report: `tmp/sft_expansion_20260830/clean_v2/assembly_report.json`
- Semantic verdicts: `tmp/sft_expansion_20260830/clean_v1/deepseek_verdicts.jsonl`

The clean source does not yet support the intended balanced expansion by itself.
Notes has one clean source session, tasks has two, and session-management tools
are absent. Add and review natural live workflows for those domains before the
200-case cross-environment run.

## Commands

```bash
.venv/bin/python scripts/generate_sft_environment_expansion.py \
  --seed-manifest tmp/sft_expansion_20260830/selected_200_manifest.json \
  --inventories tmp/sft_expansion_20260830/environment_inventories.json \
  --out tmp/sft_expansion_20260830/generated_200_cases.json \
  --workers 4 --timeout 180 --retries 1

.venv/bin/python scripts/run_sft_environment_expansion.py \
  --cases tmp/sft_expansion_20260830/generated_200_cases.json \
  --out tmp/sft_expansion_20260830/generated_200_run.json

.venv/bin/python scripts/review_sft_environment_expansion.py \
  --run tmp/sft_expansion_20260830/generated_200_run.json \
  --out tmp/sft_expansion_20260830/generated_200_review.json

.venv/bin/python scripts/build_sft_expansion_splits.py \
  --manifest tmp/sft_expansion_20260830/frozen_v1/seed_manifest.json \
  --approved-trace tmp/sft_expansion_20260830/frozen_v1/approved_trace.jsonl \
  --review tmp/sft_expansion_20260830/pilot_atomic_exact_v1.review.json \
  --review tmp/sft_expansion_20260830/generated_200_review.json \
  --out-dir tmp/sft_expansion_20260830/corpus_expanded
```
# All-Tools No-Thinking Validation Update

## Matched-control finding

The previously reported email LoRA speed advantage does not reproduce when the
raw base model and LoRA are served in the same vLLM process with the same GPU,
tool payload, prompt, cache settings, and `enable_thinking=false`.

- Original 24-case email suite, 14 compact tools:
  - raw Qwen3.5-9B base: 23/24, 0.914s average
  - all-tools iteration-2 LoRA: 23/24, 0.874s average
- Earlier base result of 9.655s was therefore confounded by serving conditions.
- The validated latency win is **thinking off + compact schemas**. LoRA has not
  yet shown a material independent speed gain.

## All-tools iterations

- Iteration 1: 365 train sessions, 40 validation sessions, 92 steps, LR 5e-7,
  one epoch. Eval loss 0.8364.
- Iteration 2: same corpus, 184 steps, LR 2e-6, two epochs. Eval loss 0.7433.
- Matched 40-case held-out routing result for base, v67, and iteration 2 was
  effectively identical: 85% exact tool selection, 85% required-field
  presence, 70% constrained required-argument accuracy, and no reasoning
  leakage.
- Iteration 2 adapter weights differ from v67 (relative L2 delta 1.02%), so the
  tie is not caused by an unchanged adapter file.

## Decision

Do not promote iteration 1 or 2 as a speed improvement. Before another train:

1. Build a larger, balanced, genuinely unseen eval across sparse tool families.
2. Expand sparse training families with multi-turn, context-dependent traces.
3. Evaluate whether LoRA preserves accuracy under more aggressive schema
   pruning than base; that is the plausible route to an indirect latency win.
4. Keep no-thinking routing and compact schema selection as harness features,
   independent of model promotion.

## Critical Qwen3.5 LoRA Serving Failure

### Symptom

vLLM 0.22 accepted `--enable-lora`, listed each adapter under `/v1/models`, and
logged `Loaded new LoRA adapter`, but Qwen3.5 text-tool adapters were not applied
to inference. Base, v67, iteration 3, and iteration 4 produced byte-identical tool
calls across 177 cases. A direct log-probability probe also returned numerically
identical values for base and every dynamic adapter.

Do **not** treat model registration, successful HTTP responses, different adapter
files, or latency differences as evidence that a LoRA is active.

### Minimal activation check

Before any benchmark, send the same deterministic request to base and adapter
models with `temperature=0`, `logprobs=true`, and thinking disabled. Compare the
token log-probabilities, not only generated text. Identical probabilities across
several prompts mean the adapter path is inactive.

Observed inactive result:

```text
base:       cob -0.0484729, alt -0.0000684
v67:        cob -0.0484729, alt -0.0000684
iteration4: cob -0.0484729, alt -0.0000684
```

Disabling prefix caching and enabling eager execution did not fix this.

### Merge trap

The tool adapters were trained with `AutoModelForCausalLM`, which gives Qwen3.5
text keys under:

```text
base_model.model.model.layers.*
```

The old merge script auto-selected `AutoModelForImageTextToText`, whose language
tower expects:

```text
base_model.model.model.language_model.layers.*
```

PEFT warned about missing adapter keys, then still wrote a model. That output was
effectively unmodified. A successful `save_pretrained` is therefore not proof of
a successful merge. Treat any missing-adapter-key warning as a hard failure.

Merging with the causal loader applied the adapter, but produced a
`Qwen3_5TextConfig` model that vLLM's production Qwen3.5 multimodal loader refused.

### Working merge path

`scripts/merge_hf_lora.py` now supports the production-safe bridge:

1. Load the full Qwen3.5 model with the image/multimodal loader.
2. Remap causal adapter keys from `model.layers` to
   `model.language_model.layers`.
3. Exclude the visual tower from PEFT target matching.
4. Merge and save the full model plus tokenizer and processor metadata.

```bash
python scripts/merge_hf_lora.py \
  --model-loader image \
  --remap-qwen35-causal-adapter \
  --base /mnt/HADES/models/Qwen3.5-9B \
  --adapter /path/to/final_adapter \
  --output /path/to/merged-full-bf16
```

Verify there are no missing adapter keys, serve the merged model as a standalone
model, and repeat the log-probability activation check.

Observed active merged result:

```text
v67 merged:        cob -0.471937001
iteration4 merged: cob -0.319356978
```

### Corrected valid benchmark

The first valid merged-model all-tools benchmark showed:

- v67 merged: 96.05% tool accuracy, 93.79% constrained argument accuracy,
  2.669s average latency, zero reasoning rows.
- iteration 4 merged: 96.05% tool accuracy, 93.79% constrained argument
  accuracy, 2.390s average latency, zero reasoning rows.
- Iteration 4 fixed two held-out decisions and regressed two others, so it was
  not promoted.

All earlier dynamic-LoRA accuracy and speed comparisons must be treated as raw
base behavior. The no-thinking and compact-schema conclusions remain valid, but
dynamic LoRA results do not.
