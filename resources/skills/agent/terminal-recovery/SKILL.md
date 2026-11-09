---
name: terminal-recovery
description: Recover from failed terminal commands using evidence-driven diagnosis and bounded retries
version: 1.0.0
category: agent
tags: [terminal, shell, debugging, recovery]
status: published
confidence: 1.0
source: builtin
owner: ""
created: "2026-08-30T00:00:00Z"
---

## When to Use

Use when a command fails, times out, produces incomplete output, or behaves differently from what the task requires.

## Procedure

1. Read the command, exit status, standard output, and standard error before choosing a response.
2. Confirm the working directory, relevant files, executable availability, permissions, and environment assumptions with minimal read-only probes.
3. Classify the failure as syntax, missing dependency, wrong path, permissions, resource pressure, timeout, service state, or task logic.
4. Change one relevant condition and retry the narrowest command that can test the diagnosis.
5. For a long-running command, use the returned session identifier to poll or provide input instead of launching duplicates.
6. After recovery, run the original acceptance check and inspect the resulting files or service state.

## Pitfalls

- Do not rerun an unchanged failing command repeatedly.
- Do not install packages or change global configuration before confirming they are missing and necessary.
- Do not launch a second server or training job before checking for an existing process and port or device conflicts.
- Do not treat partial output or a zero exit status as proof that the requested state was produced.

## Verification

- The diagnosed cause is supported by command output or environment state.
- The corrected command exits as expected.
- The requested artifact, process, or state passes an independent acceptance check.
