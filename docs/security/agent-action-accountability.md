# Agent Action Accountability

This page is a maintainer checklist for reviewing privileged agent actions in
Odysseus. It is a review aid, not a runtime security control.

Use it when a change affects how an agent, task, skill, memory, note, email,
calendar, model-serving, MCP, shell, Python, file, token, webhook, vault, or
settings surface is called, logged, displayed, or described.

## Purpose

Odysseus is a self-hosted AI workspace with privileged local capabilities. A
trusted admin can intentionally run shell commands, read and write files, send
email, manage model serving, and use admin-only tools.

When contributors touch those paths, reviewers need a compact way to see:

- who or what initiated the action
- whether the action ran with admin privileges
- what trusted and untrusted context reached the agent
- what local state was read, written, sent, deleted, or served
- where the result can be inspected later
- whether a human reviewed the action

This checklist helps reviewers reason about those facts without changing the
current agent execution path.

## When To Use This Checklist

Use this checklist for documentation, tests, tooling, or runtime changes that
touch privileged agent behavior, including:

- adding, renaming, or changing an agent tool
- changing shell, Python, file read/write, email, calendar, MCP, app API,
  task/skill/memory, settings, token, webhook, vault, or model-serving behavior
- changing how tool calls are summarized, logged, redacted, replayed, or shown
  to the user
- changing prompt assembly when external content is present
- changing owner/admin checks for loopback or tool-dispatch paths
- writing a PR description for a security-sensitive agent change

For low-risk docs-only changes, a short PR note may be enough. For behavior
changes, include the action record fields below in the PR, test fixture, log
example, or review note.

## Privileged Action Surfaces

Treat these surfaces as privileged for review:

- shell command execution
- Python execution
- file read, write, upload, download, delete, or export
- email read, send, delete, search, or attachment handling
- calendar create, update, delete, or sync
- MCP tools and servers
- app API calls that reach admin-gated routes
- task, skill, note, and memory management
- token, webhook, vault, backup, restore, and settings changes
- model download, serve, probe, stop, or provider configuration

The exact code path may differ by feature. The review question is the same:
could this action affect local data, external services, credentials, model
serving state, or another user's account?

## Untrusted Context Surfaces

Treat external or user-editable content as untrusted data when it reaches the
agent or is summarized back into a prompt. Common examples are:

- web search results
- fetched pages and URLs
- read emails and attachments
- saved memories
- user-editable skills
- notes and documents
- tool output sourced from outside the server
- copied logs, traces, terminal output, or issue text from another system

Reviewers should check that untrusted content is passed as data and does not
become a system instruction. If the change bypasses the existing untrusted
context wrapper, explain why and add a test or fixture that captures the
boundary.

## Minimal Action Record Fields

Use these fields when recording or reviewing a privileged action. They are not
a required storage schema; they are the minimum facts a human reviewer should be
able to reconstruct.

| Field | What to record |
| --- | --- |
| `action_id` | Stable local ID, log ID, request ID, test fixture ID, or PR example ID. |
| `actor` | User, agent, scheduled task, internal tool, or integration that initiated the action. |
| `owner_account` | Account whose privileges or data scoped the action. |
| `privilege_level` | `admin`, `non_admin`, `internal_tool`, or a narrower project term. |
| `tool_name` | Exact tool, route, command, job, or integration name. |
| `tool_category` | Shell, Python, file, email, calendar, MCP, memory, settings, model serving, or similar. |
| `input_source` | Prompt, fetched URL, email, memory, note, uploaded file, scheduler, API call, or manual click. |
| `trusted_context` | Local config, explicit user instruction, maintainer-authored prompt, or other trusted source. |
| `untrusted_context` | External or user-editable content that reached the action path. |
| `affected_state` | Files, emails, memories, notes, tasks, tokens, webhooks, models, providers, or settings touched. |
| `result_status` | `allowed`, `blocked`, `failed`, `redacted`, `dry_run`, `needs_review`, or another precise state. |
| `evidence_ref` | Log line, test name, screenshot, trace, diff, fixture, issue, or PR comment. |
| `human_review` | `approved`, `rejected`, `not_required`, or `pending`, plus reviewer context when available. |

## Reviewer Questions

- Does the PR say whether the change is docs-only, tooling-only, or runtime
  behavior?
- Does it keep admin-only tools unavailable to non-admin users?
- Does it preserve the trusted-private-network deployment boundary?
- Does it keep external and user-editable content wrapped as untrusted data?
- Does it avoid exposing secrets, private logs, personal documents, raw tokens,
  databases, uploads, or generated media?
- Does it clearly state what local or external state can be read, changed, sent,
  deleted, downloaded, served, or stopped?
- Does it provide a small test, fixture, log example, or manual check for the
  affected action path?
- Does the PR description mention the smallest relevant checks that were run?
- If the action is intentionally dangerous for admins, does the UI or docs make
  that boundary clear without promising sandboxing that does not exist?

## Example YAML Record

```yaml
action_id: local-review-2026-06-11-001
actor: agent
owner_account: alice
privilege_level: admin
tool_name: bash
tool_category: shell
input_source: user_prompt
trusted_context:
  - "User asked the agent to inspect disk usage."
untrusted_context:
  - "Fetched issue text was summarized before the command."
affected_state:
  files_read:
    - "/var/log/odysseus/worker.log"
  files_written: []
  external_services: []
result_status: allowed
evidence_ref:
  - "tests/test_tool_security.py::test_admin_shell_tool_allowed"
  - "PR #123 manual review note"
human_review:
  status: approved
  reviewer: maintainer
  note: "Command was read-only and did not include secrets in output."
```

## Non-Goals

This checklist does not:

- add sandboxing for shell, Python, file, or network access
- replace authentication, authorization, or tool-dispatch checks
- make admin actions safe for public, unauthenticated deployments
- define a new log format, database table, or API contract
- require every action to be stored permanently
- certify that an action is secure, compliant, or complete
- remove the need for private vulnerability reporting when exploit details are
  involved

If a change needs a real runtime control, implement and test that control in the
relevant code path. Do not rely on this checklist as enforcement.
