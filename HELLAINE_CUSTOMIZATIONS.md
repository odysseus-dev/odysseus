# Hellaine branding and upstream-merge guide

This repository tracks upstream Odysseus while presenting the visible product as
**Hellaine's Jade Palace**. Upstream implementation changes take priority; the
branding layer below is reapplied only to user-visible text, assets, and colors.

## Protected identity

- Product/browser name: `Hellaine's Jade Palace`
- Short name and assistant label: `Hellaine`
- Slogan: `Intelligence without compromise`
- Composer placeholder: `Consult Hellaine...`
- Login action: `Prove Your Authorization`
- Primary logo: `/static/hellaine-logo.svg`
- Theme: dark jade (`#06120d`, `#0b1d14`) with gold (`#d4af37`)
- PWA icons: the files in `static/icons/` listed below

## Hellaine-specific files and changes

### Documentation and recovery

- `README.md`: Hellaine identity, privacy/security text, upstream recognition,
  educational note, and AGPL/acknowledgments links.
- `HELLAINE_CUSTOMIZATIONS.md`: this inventory and merge procedure.
- `apply-hellaine-postfix.sh`: conservative visible-branding repair/check tool.
- `tools/generate-favicons.sh`: favicon generation helper.
- `docs/hellaine-logo.svg`, `docs/odysseus.jpg`, `docs/index.html`: README/demo
  branding and the Hellaine screenshot.

### Browser, PWA, and theme

- `static/hellaine-logo.svg`
- `static/icons/favicon.ico`
- `static/icons/favicon-16x16.png`
- `static/icons/favicon-32x32.png`
- `static/icons/favicon-48x48.png`
- `static/icons/apple-touch-icon.png`
- `static/icons/android-chrome-192x192.png`
- `static/icons/android-chrome-512x512.png`
- `static/icons/icon-192.png`
- `static/icons/icon-512.png`
- `static/icons/icon-maskable-512.png`
- `static/index.html`: title, logo, favicon/manifest lock, visible labels, and
  default composer text.
- `static/login.html`: login title, logo, jade/gold styling, and login action.
- `static/manifest.json`: Hellaine PWA name, colors, and icon references.
- `static/style.css`: jade/gold tokens and logo/layout overrides at the end of
  the upstream stylesheet.
- `static/sw.js`: Hellaine cache name while retaining upstream cache logic.
- `static/js/theme.js`: stable Hellaine favicon handling, Jade Palace theme
  label, and `Hellaine Insignia` setting.

### Visible application labels

- `static/app.js`: Hellaine session fallback and responsive composer text.
- `static/js/models.js`: welcome slogan.
- `static/js/sessions.js`: default session/header name.
- `static/js/chat.js`, `static/js/chatRenderer.js`: visible assistant roles.
- `static/js/slashCommands.js`: assistant roles, tours, settings guidance, and
  task/help text.
- `static/js/keyboard-shortcuts.js`: new-chat header.
- `static/js/document.js`: user-facing attachment errors.
- `static/js/settings.js`: reminder, provider, integration, and agent-tool help.
- `static/js/emailLibrary.js`: visible Hellaine reminder-email controls.
- `static/js/cookbook.js`, `static/js/cookbook-diagnosis.js`,
  `static/js/cookbook-hwfit.js`, `static/js/cookbookRunning.js`, and
  `static/js/cookbookServe.js`: visible Cookbook labels and reports.
- `static/js/research/panel.js`: neutral research example instead of product
  branding in the prompt.
- `static/js/notes.js`, `static/js/tasks.js`, `static/js/settings.js`, and
  `static/js/calendar/reminders.js`: notification icon paths under
  `/static/icons/`.
- `routes/auth_routes.py`, `routes/email_helpers.py`,
  `routes/email_pollers.py`, `routes/mcp_routes.py`, `routes/model_routes.py`,
  `routes/cookbook_routes.py`, `routes/cookbook_helpers.py`, and
  `routes/shell_routes.py`: user-visible backend messages, email links, and OAuth
  browser branding only.
- `tests/test_readme_ascii_fenced.py` and `tests/test_shell_routes.py`: forked
  assertions for the protected Hellaine wordmark and visible status text.

## Internal Odysseus names deliberately preserved

Do not mass-replace `odysseus`. The following names are compatibility-sensitive
and intentionally remain:

- Docker service/container names and commands such as `docker compose logs odysseus`;
- Python/JavaScript function, class, module, and route names;
- `/api/.../odysseus/...` paths and `odysseus-attachments.zip`;
- `X-Odysseus-*` API/email headers and `ODYSSEUS_*` environment variables;
- `odysseus:*` DOM events, CSS classes, element IDs, and localStorage keys;
- database values, scheduled-task/persona IDs, and the mythological Odysseus
  preset/quote;
- `Reminder (Odysseus):` and related legacy mail search markers;
- iCalendar `PRODID` and other protocol identifiers already used by clients.

Changing these names can break stored preferences, existing mail searches,
events, integrations, scripts, or API compatibility without improving visible
branding.

## Safe check and repair

From the repository root:

```bash
./apply-hellaine-postfix.sh --check
./apply-hellaine-postfix.sh
./apply-hellaine-postfix.sh --check
git diff --check
```

`--check` never writes. Apply mode performs only exact, allow-listed replacements
of visible upstream strings and normalizes the PWA manifest. It never reads or
writes `.env`, `data/`, `logs/`, databases, uploads, models, keys, technical
identifiers, or user data. The script is idempotent.

In the production Docker image, where `.dockerignore` intentionally omits both
`README.md` and `docs/`, the check validates the complete runtime branding set
and skips only those two source-only artifacts. In a source checkout, both are
required; finding only one is treated as an incomplete checkout and fails.

The script deliberately does not reconstruct changed HTML/SVG/CSS structures.
If a required logo reference, icon, jade/gold token, or protected text is absent,
it exits non-zero and names the missing marker. Resolve that file against the
current upstream structure and use the backup refs for comparison; do not copy a
whole old UI file over new upstream code.

## Future upstream merge procedure

1. Record the current Hellaine SHA and create/push a dated backup branch and
   annotated tag.
2. Fetch both remotes and branch from updated local `dev`.
3. Merge `upstream/dev` with a normal mergecommit.
4. Resolve conflicts with upstream code/architecture as the base, then restore
   only the protected visible branding above.
5. Compare each conflicted file with both merge stages. Never use wholesale
   `ours` or `theirs` for HTML, CSS, or JavaScript.
6. Run `./apply-hellaine-postfix.sh`, followed by `--check`.
7. Search for the protected phrases, `hellaine-logo`, favicon/manifest paths,
   and newly introduced user-visible `Odysseus` strings.
8. Run syntax, project, Docker, browser, and screenshot validation before
   publishing a draft PR.

The pre-sync refs for the 2026-07-17 merge are:

- Branch: `codex/backup-pre-upstream-2026-07-17`
- Annotated tag: `hellaine-pre-upstream-2026-07-17`
- Commit: `fbda5d7ff3ed6297f71bc9a3799253a88f905d8c`

Use these refs for targeted comparison or recovery, not as a wholesale checkout
over a newer upstream tree.
