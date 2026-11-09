---
name: action-evidence-synthesis
description: "Turn messages, meeting notes, and documents into sourced decisions, actions, dependencies, and risks"
version: 1.0.0
category: communication
tags: [messages, meetings, actions, status, evidence]
status: published
confidence: 1.0
source: builtin
created: "2026-08-30T00:00:00Z"
---

## When to Use

Use when information is fragmented across messages, meeting notes, transcripts, or documents and the user needs an action list, status summary, feasibility assessment, or executive brief.

Do not use when the source material is unavailable or when the user only wants a verbatim transcript.

## Procedure

1. Identify the requested scope, audience, time window, and decision to support.
2. Gather the relevant records in full and preserve stable source identifiers, authors, and timestamps.
3. Extract explicit decisions, commitments, requests, owners, dates, dependencies, blockers, and changed facts.
4. Reconcile revisions by preferring the newest authoritative record; keep unresolved conflicts visible instead of guessing.
5. Separate observed facts from inferred owners, dates, urgency, feasibility, or recommendations, and label every inference as tentative.
6. Produce the requested format with concise source references beside consequential claims and a final list of open questions.

## Pitfalls

- Do not turn discussion or speculation into a confirmed decision.
- Do not invent owners or deadlines when none were assigned.
- Do not silently discard older records that explain a changed commitment.
- Do not send messages, create tasks, or update calendars unless the user separately authorizes those actions.

## Verification

- Every action has a source, status, and explicit or tentative owner and due date.
- Conflicting values and revisions are resolved or visibly flagged.
- The output covers decisions, actions, dependencies, risks, and open questions relevant to the request.
