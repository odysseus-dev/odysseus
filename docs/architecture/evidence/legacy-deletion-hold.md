# Task 18 deletion hold

Date: 2026-09-08

`src/agent_loop.py` and `execute_tool_block` remain. The disposition ledger marks them `residual-until-acceptance`.

Reasons:

- `python3 scripts/openhands_probe.py acceptance --json` reports `live_stack` only when the pinned compose services are actually running. This host did not complete a green live Docker/Tailscale acceptance run in this session.
- Compatibility tests still call `stream_agent_loop` and `execute_tool_block` directly.
- Plan: delete only after Task 17 live evidence and a clean ledger.

Production callers in chat, skills, teacher, bg_monitor, and task_scheduler no longer invoke `stream_agent_loop`.
