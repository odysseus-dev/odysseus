---
name: multimodal-evidence
description: Extract and verify evidence from images, documents, and video without redundant inspection
version: 1.0.1
category: media
tags: [image, video, document, evidence, ocr]
status: published
confidence: 1.0
source: builtin
owner: ""
created: "2026-08-30T00:00:00Z"
---

## When to Use

Use when the answer or requested artifact depends on visual, temporal, tabular, or textual evidence contained in images, documents, or video.

## Procedure

1. Identify the evidence required: objects, text, values, ordering, timestamps, labels, or visual relationships.
2. Inspect the whole input or a broad representative sample first to establish structure and likely evidence locations.
3. Narrow to relevant pages, frames, regions, or time intervals and record observations with their locations.
4. Use the format's native parser for exact text and numbers: for example `python-docx` or ZIP/XML inspection for DOCX, `pdftotext` or a PDF library for PDF, spreadsheet readers for XLSX, and OCR only when the source is image-based. Do not search binary office files with plain `grep` or `cat`.
5. Resolve conflicts with one targeted reinspection at better scale or a nearby frame rather than repeating the same crop.
6. Build the answer or artifact from the evidence ledger and perform a final coverage check against every requested item.

## Pitfalls

- Do not infer unseen content from filenames, surrounding text, or a single thumbnail.
- Do not repeatedly inspect nearly identical regions without a new hypothesis.
- Do not trust OCR blindly for small labels, punctuation, or numeric values.
- Do not finalize before checking that every requested item has supporting evidence.

## Verification

- Each factual output can be traced to a page, frame, region, or timestamp.
- Exact labels and numbers were visually checked after extraction.
- The final response or artifact covers all requested evidence categories.
