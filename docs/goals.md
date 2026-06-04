# Agent Goals

Odysseus goals are persistent, per-session agent objectives inspired by the Codex CLI goal system. They are stored in `agent_goals` and scoped through the owning chat session.

## Architecture

- State lives in `core.database.AgentGoal`; `src.agent_goals` owns validation, serialization, create/update/clear, token accounting, and continuation eligibility.
- Browser/user controls use `/api/goals/{session_id}` routes. They may create, replace, pause, resume, complete, block, budget, clear, and request continuation.
- Model-visible tools are stricter: `get_goal`, `create_goal`, and `update_goal`. The model may only mark a goal `complete` or `blocked`.
- Active goals are injected into agent runs as untrusted contextual user data. Paused, blocked, usage-limited, budget-limited, and complete goals are not injected.
- Agent turns that started with an active goal account `input_tokens + output_tokens` plus wall time after final metrics. Budget crossings move the goal to `budget_limited`.
- Goal updates stream to the browser as `data: {"type":"goal_update", ...}` so the composer pill can refresh without named SSE events.

## Continuation

Continuation is guarded by `src.agent_goals.can_continue_goal` and executed by `src.goal_runner` through the existing detached `src.agent_runs` stream manager:

- The Goal popover and `/goal continue` call `POST /api/goals/{session_id}/continue`.
- If eligible, the backend starts a detached continuation run and the browser watches that session like any other background stream.
- After every goal-accounted agent turn, `src.agent_loop` asks `src.goal_runner` to enqueue the next continuation once the current run is idle.
- Continuation stops when status changes away from `active`, another run is active, the session disappears, or a token budget is exhausted.

## Statuses

- `active`: injected into agent runs and eligible for continuation.
- `paused`: user-controlled stop without clearing the objective.
- `blocked`: model or user says the same blocker prevents progress.
- `usage_limited`: reserved for system-level usage limits.
- `budget_limited`: token budget exhausted.
- `complete`: objective verified as done.
