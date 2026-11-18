# Generation-budget contract

## Context allocation

A configured positive output limit is a ceiling, not a session-setting mutation.
The request budget subtracts estimated prompt tokens, tool-schema tokens, visual
patch reservations and tokenizer/template headroom from the observed model
context. Provider-observed input tokens can replace the text estimate when
available; tool schemas still count. These are estimates, not a guarantee of an
exact tokenizer count. Existing provider context-error recovery remains active.

Small contexts reserve one eighth of the context (at least32 tokens). At8K and
above, the previous large-window margin is retained: at least1024 and at most
2048 tokens, scaling with context. Image reserves and tool-schema accounting are
not scaled down. Thus an otherwise-fitting32-token output is not reduced to1
merely because the selected context is512.

If some output fits but the requested allowance does not, the request is bounded
and an actionable warning reports both values and suggests a larger context or
shorter prompt. The configured session allowance is unchanged. If no safe output
space remains, native non-streaming chat returns HTTP400 before transport;
streaming chat emits a terminal400 error with fallback disabled. It does not
send a1-token or unbounded request to paper over prompt overflow.

## Additive backend fields (Sonnet owns visual presentation)

For a positive limit and available context metadata, `/api/chat` returns
`generation_budget` and stores it in assistant-message metadata. Chat SSE emits
`type: generation_budget`, also carrying `candidate_index` and `model`, before
that candidate's content. It is metadata, not a text delta or proof that a
candidate succeeded. A later fallback candidate has its own notice. Unbounded
or unknown-context paths retain their previous behavior; absence of a report
must not be described as verified fit.

Fields:

- `requested_max_tokens`: the configured allowance supplied to budgeting.
- `effective_max_tokens`: the context-bounded allowance;0 in a rejected no-space report.
- `context_length`: observed context used for calculation.
- `estimated_input_tokens`: prompt plus applicable image/tool reservations.
- `safety_margin_tokens`: tokenizer/template headroom.
- `available_output_tokens`: context minus prompt requirements and headroom; may be negative.
- `safe_to_generate`: whether any safe output allowance remains.
- `warning`: null when the request fits, otherwise actionable explanation.

HTTP400 `detail` includes `message` and `generation_budget`; SSE error data
includes `status`, `text`, `fallback_eligible:false` and `generation_budget`.
Backend warnings expose values without logging prompt contents or credentials.
The internal integer helper retains its legacy minimum1 for existing context-
recovery consumers; native transports inspect the detailed no-space result and
refuse dispatch. This does not enlarge image budgets or remove overflow guards.

## Evidence boundaries

Focused regressions exercise512,2048 and4096, requested32, constrained output,
no-space refusal, structured warnings, fake HTTP payloads, native route metadata,
and retained large-context/image/tool/context-recovery checks. They use metadata
and mock transports only, not model inference.

The accepted Gate C remains historical real evidence on567d1653: Qwen CPU load,
managed readiness, native `ready` response, and identity-verified stop all passed;
the real response's effective limit was1 due to the former reservation logic.
The full32-token allowance is now corrected in code and focused tests, **not
revalidated by another real model request**. No inference replay is authorized.
