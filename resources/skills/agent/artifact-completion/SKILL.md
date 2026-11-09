---
name: artifact-completion
description: Create requested artifacts early, iterate from concrete output, and verify final deliverables
version: 1.0.0
category: agent
tags: [artifacts, files, verification, workflow]
status: published
confidence: 1.0
source: builtin
owner: ""
created: "2026-08-30T00:00:00Z"
---

## When to Use

Use when the task requires a file, patch, report, document, image, archive, configuration, or other persistent deliverable rather than only a text answer.

## Procedure

1. Extract the required deliverable path, format, content constraints, and acceptance criteria.
2. Inspect the source material and existing target without delaying the first valid artifact.
3. Create a minimal complete version at the required location, then iterate from that concrete output.
4. Use the format's native parser, renderer, compiler, or test tool to inspect the artifact.
5. Repair specific validation, content, or presentation failures while preserving correct portions.
6. Confirm the final path, file type, required content, and usability before reporting completion.

## Pitfalls

- Do not spend the full task budget inspecting without creating the requested output.
- Do not place the artifact at a convenient path when the task specifies another location.
- Do not use a filename extension as proof that the file is valid in that format.
- Do not report completion while placeholders, missing sections, parse errors, or failed checks remain.

## Verification

- The artifact exists at the required path and opens or parses successfully.
- Required sections, fields, labels, or visual elements are present.
- Relevant tests, render checks, or validators pass.
