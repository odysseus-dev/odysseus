---
name: web-research-fallback
description: Research current web information with source-first search and controlled browser fallback
version: 1.0.0
category: research
tags: [web, search, browser, sources, research]
status: published
confidence: 1.0
source: builtin
owner: ""
created: "2026-08-30T00:00:00Z"
---

## When to Use

Use when a task requires current public information, primary sources, multiple pages, or a site that cannot be reliably read from search results alone.

## Procedure

1. Define the facts needed and the preferred primary source for each fact.
2. Search with a focused query and use result metadata to select likely authoritative pages.
3. Open the source directly and extract the relevant passage, date, and URL rather than relying on a search snippet.
4. Use the private browser when the page requires interaction, client-side rendering, navigation, or visual inspection.
5. If a page fails, try a primary-source alternative or a narrower route before broadening to secondary sources.
6. Cross-check unstable or consequential claims and distinguish source-backed facts from inference.

## Pitfalls

- Do not treat snippets as evidence for claims not visible on the source page.
- Do not browse repeatedly without recording what each page established.
- Do not use a secondary summary when an accessible primary source answers the question.
- Do not claim freshness without checking publication or update dates.

## Verification

- Each important claim maps to a source that directly supports it.
- Time-sensitive facts include an observed date or version.
- Browser interaction produced the needed page state or a documented fallback was used.
