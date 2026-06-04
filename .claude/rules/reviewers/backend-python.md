# Reviewer — Backend (Python)

**Scope:** `app.py`, `routes/**/*.py`, `src/**/*.py`, `services/**/*.py`,
`mcp_servers/**/*.py`, `tests/**/*.py`, and any other `**/*.py`.

Odysseus is a Flask/uvicorn app: HTTP routes in `routes/`, business logic
and integrations in `src/`. Apply [`../review-checks-common.md`](../review-checks-common.md)
first, then the domain checks below.

## Check

- **Route safety** — new/changed routes validate input before use,
  enforce auth where the sibling routes do, and never trust client data
  for file paths, shell commands, or model/endpoint selection.
- **Injection** — SQL built by string concatenation, shell commands from
  user input, prompt injection into model calls, unsafe deserialization
  (`pickle`, `yaml.load` without `SafeLoader`), path traversal in file
  handlers/uploads.
- **Secrets & data** — credentials read from config/env, not literals.
  No logging of secrets, tokens, full prompts containing user data, or
  PII. Honour the privacy-first posture in [`SECURITY.md`](../../../SECURITY.md).
- **Error handling** — fail loud with context; no bare `except:` or
  `except Exception: pass` that hides failures; DB writes are wrapped in
  a transaction where partial writes would corrupt state.
- **Blocking I/O** — long/network/subprocess calls on the request path
  should be async or offloaded; flag obvious blocking in hot routes.
- **Resource hygiene** — files/connections/clients closed (context
  managers); no obvious leaks in loops over user input.
- **Style** — type hints on new defs; clear names over comments; no
  f-strings without placeholders (F541); no unused imports.

## Tests (`tests/**`)

- New behavior has a test; bug fixes add a regression test.
- Tests assert on real outcomes, not just "did not raise".
- No live network/model calls in unit tests — they should be stubbed.

## Common false-positives — do NOT flag

- A bare `raise` after a side effect (the caller still gets the error).
- Patterns already used 5+ times elsewhere (established convention).
- Missing async on a genuinely cheap, non-blocking call.
- Test-only helpers that look "unused" but are referenced by fixtures.

## Extend

Add checks here as backend conventions emerge. Cross-cutting rules go in
[`../review-checks-common.md`](../review-checks-common.md) instead.
