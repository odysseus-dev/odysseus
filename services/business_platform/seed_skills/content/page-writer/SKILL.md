---
name: page-writer
description: Publish or update a sanitized Markdown page on the website via the governed gateway endpoint.
version: 1.0.0
category: content
tags: [content, publishing, pages]
status: published
confidence: 0.9
source: imported
created: 2026-06-13T01:30:00Z
---

> Source: agentkit-web skills/page-writer

# page-writer

Publish or update a sanitized Markdown page on the website via the governed agentkit-web gateway endpoint.

## When to use

Invoke this skill when the visitor asks the agent to create or update a page (including blog posts, guides, or any Markdown-based content) on the site.

## Inputs

- **slug** — lowercase alphanumeric + hyphens; max 64 characters. Used in the page URL: `/pages/{slug}`.
- **title** — page title; max 200 bytes.
- **markdown** — page content in Markdown; max 100,000 bytes.

## How it works

Runs `scripts/write_page.py <slug> <title> <markdown>`, which:

1. Reads `AGENTKIT_GATEWAY_URL`, `AGENTKIT_TENANT_ID` (defaults to `"default"`), and `AGENTKIT_PAGE_WRITER_TOKEN` from environment.
2. Sends a `PUT` request to `${AGENTKIT_GATEWAY_URL}/pages/{slug}` with:
   - Headers: `X-Tenant-Id: {tenant}`, `X-Service-Token: {token}`, `Content-Type: application/json`
   - Body: JSON `{"title": "...", "markdown": "..."}`
3. The gateway validates the service token, strips raw HTML and dangerous URIs from the Markdown, caps sizes, and persists to CouchDB.

Returns `{"status": <http-code>, "body": <gateway-response>}`.

## Security

Default-deny: without `AGENTKIT_PAGE_WRITER_TOKEN` set and matching the tenant's configured token, all write requests are rejected (403). Never embed raw HTML or scripts—they are stripped server-side by the sanitizer. The page is accessible only to authenticated users of the same tenant.
