# Hellaine branding package

Applied visible branding changes for **Hellaine's Jade Palace**.

## Chosen identity
- App name / browser title: `Hellaine's Jade Palace`
- Main title: `Hellaine`
- Welcome subtitle: `Intelligence without compromise`
- Input placeholder: `Consult Hellaine...`
- Login button: `Prove Your Authorization`
- Logo: `/static/hellaine-logo.svg`
- Theme: dark jade green with gold accents

## Files changed
- `static/index.html`
- `static/login.html`
- `static/landing.html` if present
- `static/manifest.json`
- `static/style.css`
- `static/app.js`
- `static/js/models.js`
- `static/js/sessions.js`
- `static/js/chatRenderer.js`
- `static/js/theme.js`

## Internal names deliberately preserved
Some technical keys/classes may still contain `odysseus`, such as localStorage keys, CSS classes, event names, and internal function names. These are not visible branding and changing them would risk breaking stored settings, themes, and event handlers. Elegant vandalism is one thing; detonating state management is another.

## Additional visible strings to check if you export the full repo
Your earlier grep showed extra visible text in files that were not included in this export, especially:
- `static/js/slashCommands.js`: tour text like `Welcome to Odysseus`, `Odysseus is yours to explore`, role labels.
- `static/js/research/panel.js`: placeholder about tracing Odysseus's journey.
- `static/js/emailLibrary.js`, `static/js/settings.js`, `routes/email_*`: reminder/email labels like `Open in Odysseus`.
- `static/js/tasks.js`: notification icons using `/static/favicon.ico`.
- Backend headers like `X-Odysseus-*` should normally stay unchanged unless you are ready to refactor backend code too.

## Build reminder
After copying these files into your repo:

```bash
cd /mnt/user/appdata/dockge/stacks/hellaine
docker build --no-cache -t hellaine-odysseus-custom .
```

Then in Dockge: `Stop → Start`, not `Update`.
