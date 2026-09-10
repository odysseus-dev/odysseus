# Task 18 deletion hold

Date: 2026-09-10

`src/agent_loop.py` and `execute_tool_block` remain. The disposition ledger
marks them `residual-until-acceptance`.

Reasons:

- Gate 10 live Docker/Tailscale acceptance is now green
  (`python3 scripts/openhands_probe.py acceptance --json` → `live_stack: true`).
  That does **not** authorize deletion.
- Compatibility tests still call `stream_agent_loop` and `execute_tool_block`
  directly.
- Coordinated cutover still requires backup/restore, one switch off the old
  loop, a clean disposition ledger, and acceptance with the old runtime
  disabled.

Production callers in chat, skills, teacher, bg_monitor, and task_scheduler
invoke `stream_governed_agent`, not `stream_agent_loop`.
