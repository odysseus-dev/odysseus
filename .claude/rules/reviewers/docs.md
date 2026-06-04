# Reviewer — Documentation

**Scope:** `**/*.md`, `docs/**`, `LICENSE`, `licenses/**`.

Apply [`../review-checks-common.md`](../review-checks-common.md) first.
Documentation review is light-touch — prefer RECOMMENDED over REQUIRED
unless a doc actively misleads users.

## Check

- **Accuracy** — commands, flags, file paths, and env vars in docs match
  the real code. Verify a referenced file/dir exists (Glob) and a
  referenced command is real before trusting it. Unverified references in
  user-facing docs are REQUIRED to fix.
- **Setup steps** — install/run instructions in `README.md` /
  `CONTRIBUTING.md` actually work with the current code (e.g. the right
  Python version, the right `docker compose` invocation).
- **Planned vs shipped** — features not yet implemented are marked
  `[PLANNED]` so users don't try to use them.
- **Secrets & PII** — no real keys, tokens, private logs, personal data,
  or public IPs in examples (per [`CONTRIBUTING.md`](../../../CONTRIBUTING.md)
  and [`SECURITY.md`](../../../SECURITY.md)). Use obvious placeholders.
- **Links** — internal links resolve; no dead relative paths after a file
  move.
- **Clarity (RECOMMENDED)** — headings, code fences with a language, and
  consistent terminology ("TireTutor", project names spelled correctly).

## Common false-positives — do NOT flag

- Prose style, tone, or wording preferences.
- Line length / formatting a Markdown linter would own.
- Intentional brevity in a stub or placeholder doc.

## Extend

Add checks here as doc conventions emerge (e.g. a required section in
release notes). Cross-cutting rules go in
[`../review-checks-common.md`](../review-checks-common.md).
