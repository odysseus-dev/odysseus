# Agent Loop Guardrails

## Overview

The Odysseus agent loop includes multiple layers of protection against infinite loops, runaway execution, and resource exhaustion. These guardrails ensure the agent remains responsive and efficient even when dealing with complex multi-step tasks.

## Existing Guardrails

### 1. Round Limit (`MAX_AGENT_ROUNDS`)

**Location**: `src/agent_tools.py`
**Value**: `20` rounds

The agent loop terminates after a maximum of 20 rounds (iterations), regardless of whether tools are being called. This is the ultimate backstop against infinite loops.

```python
for round_num in range(1, max_rounds + 1):
    # Agent logic here
```

**Rationale**: Most tasks complete within 5-10 rounds. 20 rounds provides ample room for complex workflows while preventing runaway execution.

**Configuration**: Can be overridden via the `max_rounds` parameter in `stream_agent_loop()`.

---

### 2. Loop-Breaker (Stall Detector)

**Location**: `src/agent_loop.py` (lines 1951-2004)

A sophisticated stall detector that identifies when the agent is circling without making progress.

**Mechanism**:

1. **Call signature tracking** — Recent tool calls are tracked in a deque (maxlen=6)
2. **Stuck round counter** — Increments when a round repeats a recent call AND produces no text
3. **Runaway detection** — Any tool called 15+ times triggers an immediate break

**Trigger conditions**:
- `_stuck_rounds >= 4` — Four consecutive rounds with repeated calls and no progress text
- `_runaway` — Any single tool type called 15+ times

**On trigger**:
- Sets `_force_answer = True` for the next round
- Sends a system message instructing the model to stop calling tools and write the final answer
- The model gets one tool-free round to synthesize an answer or declare what's blocking it

```python
_sig = "|".join(sorted(f"{b.tool_type}:{(b.content or '').strip()[:120]}" for b in tool_blocks))
_is_repeat = _sig in _recent_call_sigs
_real_text = _THINK_RE.sub("", cleaned_round).strip()

if _is_repeat and not _real_text:
    _stuck_rounds += 1
else:
    _stuck_rounds = 0
```

**Why this works**: Genuine exploration (new distinct calls) is never punished. Only identical retrials with no progress text trigger the breaker, preserving the agent's ability to try different approaches.

---

### 3. Tool Budget (`max_tool_calls`)

**Location**: `src/agent_loop.py` (lines 2042-2046)

An optional limit on the total number of tool executions per agent turn.

```python
if max_tool_calls > 0 and total_tool_calls >= max_tool_calls:
    yield f'data: {json.dumps({"type": "budget_exceeded", "limit": max_tool_calls, "used": total_tool_calls})}\n\n'
    budget_hit = True
    break
```

**Use case**: Task scheduler uses this to prevent runaway scheduled tasks.

**Default**: `0` (unlimited) for normal chat sessions.

---

### 4. Context Budget Enforcement

**Location**: `src/agent_loop.py` (lines 1522-1566)

Token budget limits prevent context explosion and control costs.

**Soft budget**: `agent_input_token_budget` setting (default: 6000 tokens)
- Triggers context trimming when exceeded
- Scales with model context window for long-context models

**Hard budget**: `agent_input_token_hard_max` setting (default: from `DEFAULT_HARD_MAX`)
- Absolute ceiling for the input token budget
- Prevents misconfiguration from zeroing the budget

```python
effective_budget = compute_input_token_budget(
    soft_budget,
    context_length,
    is_setting_overridden("agent_input_token_budget"),
    hard_max=hard_max,
)
trimmed_messages = trim_for_context(messages, effective_budget, reserve_tokens=reserve_tokens)
```

**Why two limits**: Soft budget gives the model room to work; hard budget prevents runaway costs even with misconfigured soft budget.

---

### 5. Wall-Clock Deadline

**Location**: `src/agent_loop.py` (lines 1660-1673, 1683-1685)

Per-round timeout that complements the inactivity timeout in `stream_llm`.

**Per-round deadline**: `max(agent_stream_timeout * 4, 1200)` seconds
- Default: 1200 seconds (20 minutes) per round
- Kills streams that trickle bytes forever (bypassing inactivity timeout)

```python
_round_deadline = time.time() + max(agent_stream_timeout * 4, 1200)
async for chunk in stream_llm_with_fallback(...):
    if time.time() > _round_deadline:
        logger.warning(f"[agent] round {round_num} stream exceeded wall-clock deadline; cutting off")
        break
```

**Inactivity timeout**: `agent_stream_timeout_seconds` setting (default: 300 seconds)
- Applied per-read in `stream_llm`
- Kills wedged/silent endpoints

**Why both**: Inactivity catches stalled connections; wall-clock catches infinite trickles.

---

### 6. Completion Verifier (Optional)

**Location**: `src/agent_loop.py` (lines 1907-1948)

Opt-in subagent verification that independently checks agentic work before accepting "done".

**Configuration**: `agent_verifier_subagent` setting (default: `False`)

**Behavior**:
- Fires only on effectful turns (tools that produce checkable artifacts)
- Capped at `_VERIFIER_MAX_ROUNDS = 2` per turn
- Requires fresh effectful work before re-verifying (prevents verification loops)

**On failure**:
- Injects system message with specific issues
- Forces the model to fix problems with tools
- Increments `_verifier_rounds` counter

**Why opt-in**: Weak local models can't judge from action snapshots (no doc body) and false-reject, adding costly extra rounds. Strong models benefit from the quality check.

---

## Guardrail Interactions

The guardrails work together as a defense-in-depth system:

```
┌─────────────────────────────────────────────────────────────────┐
│                    AGENT LOOP START                             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Round 1..20 (MAX_AGENT_ROUNDS)                                 │
│  ├─ Context trimmed to budget                                 │
│  ├─ Wall-clock deadline set                                    │
│  └─ Tools sent (unless _force_answer)                          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Stream response (with inactivity + wall-clock timeout)       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Parse tool blocks                                               │
│  ├─ Loop-breaker: stuck? → force_answer next round             │
│  ├─ Tool budget: hit? → exceed event                           │
│  └─ Execute tools                                               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  No tools?                                                       │
│  ├─ Effectful turn? → Completion verifier (opt-in)             │
│  └─ Done                                                        │
└─────────────────────────────────────────────────────────────────┘
```

## Configuration Summary

| Setting | Default | Purpose |
|---------|---------|---------|
| `MAX_AGENT_ROUNDS` | 20 | Maximum iterations |
| `agent_input_token_budget` | 6000 | Soft token limit |
| `agent_input_token_hard_max` | (computed) | Absolute token ceiling |
| `agent_stream_timeout_seconds` | 300 | Inactivity timeout |
| `max_tool_calls` | 0 (unlimited) | Tool execution budget |
| `agent_verifier_subagent` | False | Enable completion verifier |

## Best Practices

1. **For scheduled tasks**: Set `max_tool_calls` to a reasonable value (10-50) to prevent runaway background jobs.

2. **For weak local models**: Keep `agent_verifier_subagent = False` to avoid costly false rejections.

3. **For long-context models**: Let the soft budget scale automatically — don't set a fixed `agent_input_token_budget` unless you have a specific reason.

4. **Monitoring**: Watch for `loop-breaker tripped` or `budget_exceeded` events in logs — these indicate the guardrails are working as intended.

## Testing

The guardrails are tested via:
- `test_agent_loop.py` — Core loop behavior
- `test_context_budget.py` — Token budget enforcement
- `test_scheduler_scheduled_time_validation.py` — Tool budget in scheduled tasks

## See Also

- `src/agent_loop.py` — Main loop implementation
- `src/agent_tools.py` — Constants including `MAX_AGENT_ROUNDS`
- `src/context_budget.py` — Token budget computation
- `docs/agent-notes-workflows.md` — Notes-based workflow examples
