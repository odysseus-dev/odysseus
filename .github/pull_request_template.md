## Summary

<!-- One paragraph: what changed and why. "Fixed bug" and "Added feature" are not summaries. -->

## Target branch

- [ ] This PR targets **`dev`**, not `main`. All PRs land in `dev`; `main` is curated by the maintainer at each release. If your PR is on `main` by accident, click "Edit" on this PR and change the base.

## Linked Issue

<!-- Every PR should be linked to an issue.
     Use one of:  Fixes #NNN  |  Part of #NNN  |  Closes #NNN  -->

Fixes #

## Type of Change

- [ ] Bug fix (non-breaking — fixes a confirmed issue)
- [ ] New feature (non-breaking — adds new behaviour)
- [ ] Breaking change (changes or removes existing behaviour)
- [ ] Refactor / cleanup (behaviour unchanged)
- [ ] Documentation only
- [ ] CI / tooling / configuration

## Checklist

- [ ] I searched [open issues](https://github.com/pewdiepie-archdaemon/odysseus/issues) and [open PRs](https://github.com/pewdiepie-archdaemon/odysseus/pulls) — this is not a duplicate.
- [ ] This PR targets `dev`
- [ ] My changes are limited to the scope described above — no unrelated refactors or whitespace changes mixed in.
- [ ] I actually ran the app (`docker compose up` or `uvicorn app:app`) and verified the change works end-to-end. Type-checks and unit tests are not enough.

## How to Test

<!-- Step-by-step instructions a reviewer can follow to verify this works.
     Do not leave this empty — a PR without test steps will be sent back. -->

1.
2.
3.

## Visual / UI changes — REQUIRED if you touched anything that renders

**Anything that changes what the UI looks like — buttons, icons, padding, colors, fonts, spacing, layout, CSS, HTML, SVG, or any `static/js/` module that draws to the DOM — needs all of the following. PRs that change rendering without these WILL be closed.**

- [ ] **Screenshot or short clip** of the change in the running app, attached below. Mobile screenshot too if the change affects mobile.
- [ ] **Style match**: the change uses Odysseus's existing visual language. Specifically:
  - Reuse existing CSS variables (`--red`, `--fg`, `--bg`, `--card`, `--border`, etc.) — do not introduce new color values, font sizes, or spacing units.
  - Reuse existing button/input/card/border classes. Don't invent parallel styling.
  - **No Unicode emoji in UI or code.** Use inline SVG (matching the monochrome icon style already in `static/index.html`) or plain text.
  - Monospaced font (`Fira Code`) for primary UI text. Don't override.
  - Dark theme is the default; any light-mode work must be wired through the existing theme system, not hard-coded.
- [ ] **No new component patterns.** If a similar widget already exists in the app, extend it instead of writing a parallel one.
- [ ] **I am not an LLM agent submitting a bulk PR.** If you are, please open an issue describing the problem first — bulk auto-generated PRs that don't match the project's visual style are closed on sight, even when the underlying fix is correct.

### Screenshots / clips

<!-- Drag and drop images or a screen recording here. Required for any UI/visual change. -->
