# Source map

Odysseus is a FastAPI app with a plain JavaScript frontend.

- `app.py` creates the app, middleware, and route registrations.
- `routes/` contains the HTTP endpoints. Start here for API behavior.
- `core/` contains auth, database, session, and platform compatibility code.
- `src/` contains most application logic used by the routes.
- `services/` contains feature-specific integrations such as search, memory, speech, and hardware fitting.
- `static/index.html`, `static/app.js`, and `static/js/` contain the browser UI.
- `companion/` contains the companion API.
- `mcp_servers/` contains the bundled MCP servers.
- `config/`, `docker/`, and `docker-compose.yml` contain deployment config.
- `tests/` contains the regression tests. Add a focused test with behavior changes when practical.

## Agent skills

### Issue tracker

Issues live in the repo's GitHub Issues, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Five triage labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`) mapped to matching GitHub labels. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context repo — one `CONTEXT.md` + `docs/adr/` at the root (created lazily). See `docs/agents/domain.md`.
