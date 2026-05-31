# Contributing to Odysseus

Thanks for helping out. This ship is moving fast and feedback, bug reports, and
focused fixes are all genuinely appreciated.

## Where to start

The best entry points are listed in [ROADMAP.md](ROADMAP.md). In short:

- Fresh-install testing (Docker and manual) on Linux, macOS, and Windows.
- Provider and integration setup bugs.
- Mobile and editor polish.
- Docs, especially self-host troubleshooting notes.
- Small, focused refactors.

If you are unsure whether something is wanted, open an issue first and ask
before writing a large change.

## Reporting bugs

Open an issue with:

- What you did, what you expected, and what actually happened.
- Your platform (OS, Python version, Docker vs manual install).
- Relevant logs. Scrub secrets first (API keys, tokens, password hashes).

Do not paste live `.env` values, database contents, or anything from `data/`.

## Development setup

Use the manual install from the [README](README.md#option-2-manual-install--linux--macos):

```bash
python3 -m venv venv
source venv/bin/activate        # venv\Scripts\Activate.ps1 on Windows
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

Bind to `127.0.0.1` for local development. Only use `0.0.0.0` when you
intentionally want LAN or reverse-proxy access.

## Running tests

Tests live in `tests/` and run with pytest:

```bash
pip install pytest pytest-asyncio
pytest
```

Please run the suite before opening a pull request, and add tests for new
behavior or regressions you fix where it is practical.

## Pull requests

- Branch off the default branch and keep each PR focused on one thing.
- Write a clear description: what changed, why, and how you tested it.
- Link the issue it addresses (for example, `Closes #123`).
- Keep diffs minimal. Avoid drive-by reformatting of unrelated code.
- Do not commit anything from `data/`, `.env`, logs, uploads, generated media,
  local databases, backups, or secrets. See [SECURITY.md](SECURITY.md) for the
  pre-publish checks.

### Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/) prefixes so
history stays readable:

- `feat:` a new feature
- `fix:` a bug fix
- `docs:` documentation only
- `refactor:` code change that neither fixes a bug nor adds a feature
- `test:` adding or fixing tests
- `chore:` tooling, deps, or housekeeping

Example: `fix: make landing page footer reachable under scroll-snap`.

## Code style

- Match the style of the surrounding code rather than introducing a new one.
- Frontend lives in `static/` (`index.html`, `app.js`, `style.css`, `js/`).
  Backend is FastAPI in `app.py`, `core/`, `src/`, `routes/`, and `services/`.
- Watch out for mobile `@media` overrides of the same selector. A lot of
  "my CSS did not apply" bugs come from a paired mobile rule.

## Security

If you find a vulnerability, follow [SECURITY.md](SECURITY.md). Please do not
disclose exploit details in a public issue.

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
