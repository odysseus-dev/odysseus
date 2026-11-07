---
name: support-triage-and-routing
description: "Prioritize support requests, identify owners, route internally, and prepare safe customer drafts"
version: 1.0.0
category: communication
tags: [support, triage, urgency, routing, drafts]
status: published
confidence: 1.0
source: builtin
created: "2026-08-30T00:00:00Z"
---

## When to Use

Use when reviewing a support backlog, identifying urgent incidents, assigning internal ownership, or drafting customer responses.

Do not use when the request is merely to summarize an unrelated inbox or when sender identity cannot be established safely.

## Procedure

1. Read each in-scope request in full and retain its stable message or ticket identifier.
2. Resolve whether the sender is internal or external and identify the responsible internal team from available contacts and service ownership data.
3. Classify urgency from impact and time sensitivity: critical for outage, data loss, security exposure, or imminent contractual breach; high for a blocked user without a workaround; medium for degraded service with a workaround; low for non-blocking inquiries.
4. Record a concise problem statement, evidence, affected scope, workaround, owner, next action, and response deadline.
5. Route internally only when the user has authorized operational messaging; prepare external responses as reviewable drafts by default.
6. Re-read created assignments or drafts and produce an escalation summary grouped by urgency.

## Pitfalls

- Do not infer severity from emotional language alone.
- Do not expose one customer's data in another customer's response.
- Do not send externally when the task calls for triage or drafting.
- Do not mark an issue routed without a stable owner or observable routing result.

## Verification

- Every issue has a stable source identifier, urgency rationale, owner, and next action.
- Critical and high items have explicit response targets and escalation state.
- External communication is a draft unless the user explicitly authorized sending.
