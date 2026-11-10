---
name: developer-docs
description: Find, read, and apply authoritative developer documentation during implementation
version: 1.0.0
category: dev
tags: [docs, documentation, api, software-development]
status: published
confidence: 1.0
source: builtin
owner: ""
created: "2026-08-18T00:00:00Z"
---

## When to Use

Use when the user asks how a library, framework, API, protocol, CLI, or SDK works, or when implementation depends on version-specific behavior. Prefer this skill over guessing from memory.

## Procedure

1. Identify the exact product, package, version, and task. Ask one focused clarification only when the target is genuinely ambiguous.
2. Prefer the vendor's or project's primary documentation, source repository, release notes, and API reference. Use a general search only to locate those sources.
3. Read the relevant page or reference section, then apply the documented behavior to the user's codebase and active workspace.
4. Separate documented facts from inference, and call out version or environment assumptions.
5. For code changes, add a focused regression test for the documented contract and run it before reporting completion.

## Pitfalls

- Do not present search snippets, stale cached knowledge, or a third-party tutorial as authoritative when primary documentation is available.
- Do not silently mix instructions from different major versions.
- Do not claim an API or option exists without confirming it in the relevant reference.
- Do not use web search for a local project task when the active workspace and local tools can answer it.

## Verification

- The cited or retrieved documentation matches the target version.
- The implementation or answer distinguishes source-backed facts from inference.
- Any code change has a focused test or a concrete verification command.
