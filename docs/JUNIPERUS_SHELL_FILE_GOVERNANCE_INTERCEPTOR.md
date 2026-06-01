# JUNIPERUS040 — Shell/File Governance Interceptor

## Purpose

This pass converts Juniperus power from raw admin capability into governed operator capability.

Before this pass, Juniperus inherited Juniperus-style direct authority surfaces:

- browser shell routes
- agent `bash`
- agent `read_file`
- agent `write_file`
- MCP-backed shell/filesystem tools with direct fallback

After this pass, high-risk operations are intercepted and queued for human decision before execution.

## Authority model

```text
Read first.
Plan second.
Approval before mutation.
Execution only after explicit future gate.
Receipt always.
```

## Intercepted surfaces

```text
routes/shell_routes.py
- /api/shell/exec
- /api/shell/stream

src/tool_execution.py
- bash
- read_file
- write_file
- MCP path before mcp.call_tool
- direct fallback path
```

## Decision behavior

Allowed without approval:

```text
Read-only shell inspection commands such as dir, ls, pwd, git status, git diff, git log, type, cat, Get-ChildItem, Select-String.
Non-sensitive reads inside the workspace root.
```

Blocked and queued for approval:

```text
write_file
shell commands that mutate files, git state, package state, processes, services, network state, or execution state
sensitive reads such as .env, auth.json, app.db, token/key/secret files, .ssh, AppData, vault data
paths outside C:\Users\iamcy\CymaticsDev unless specifically approved in a future gate
```

## What this package does not do

It does not execute approved operations. It only creates the interceptor and approval objects. Runtime execution remains locked until a later package adds an approved execution worker.

## Next package

`JUNIPERUS050 — Diff-First Code Editing Gate`


## v0.1.1 VERIFYFIX PATCHSAFE
Repairs partial v0.1.0 installations where payload files installed but shell_exec, MCP, or direct fallback guard markers were not inserted because exact string patch markers did not match the local Juniperus source shape.
