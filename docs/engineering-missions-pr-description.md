# PR: Engineering Missions PR Review Cockpit

## Summary

Adds an Engineering Missions cockpit to Odysseus: an authenticated workflow for
running GitHub PR review missions, generating deterministic diff intelligence,
persisting an auditable report, exporting Markdown/JSON, and publishing a
revocable public report page.

## Why This Matters

This feature turns Odysseus from a general AI workspace into a developer tool
that can produce shareable engineering artifacts. It demonstrates the kind of
fullstack systems work expected in production applications:

- Product workflow: cockpit, history, replay pages, exports, and public reports.
- Backend workflow orchestration: GitHub API fetch, Go worker execution,
  optional LLM synthesis, persistence, and audit logs.
- Security and ownership: authenticated private routes, owner-scoped missions,
  tokenized public links, and revoke support.
- Polyglot stack: Python/FastAPI, SQLAlchemy, Go, JavaScript, TypeScript
  contracts, HTML/CSS, and GitHub Actions.

## Implementation

- Added `EngineeringMission` persistence with publish/share metadata.
- Added startup migration for existing installs.
- Added mission APIs:
  - `GET /api/engineering-missions`
  - `GET /api/engineering-missions/{mission_id}`
  - `POST /api/engineering-missions/pr-review`
  - `POST /api/engineering-missions/{mission_id}/share`
  - `POST /api/engineering-missions/{mission_id}/share/revoke`
  - Markdown/JSON export endpoints.
- Added public report APIs and route:
  - `GET /engineering/reports/{share_token}`
  - `GET /api/engineering-missions/public/{share_token}`
- Added Go diff analyzer worker under `tools/github-diff-analyzer`.
- Added cockpit UI, private replay support, public report page, and TypeScript
  contracts.
- Added focused Python, Go, and JS CI checks.

## Screenshots

![Engineering Missions cockpit](engineering-missions-cockpit.png)

![Published Engineering Mission report](engineering-missions-public-report.png)

## Verification

```bash
python -m py_compile routes/engineering_mission_routes.py core/database.py app.py
pytest tests/test_engineering_missions.py
node --check static/js/engineeringMissions.js
node --check static/js/engineeringReportPage.js
cd tools/github-diff-analyzer && go test ./...
git diff --check
```

Manual verification:

- Ran a real mission against `pewdiepie-archdaemon/odysseus#2415`.
- Verified private replay at `/engineering/missions/{mission_id}`.
- Published and opened a tokenized public report at
  `/engineering/reports/{share_token}`.
- Verified Markdown and JSON export downloads.
- Checked browser console for errors on both private and public pages.

## Follow-Ups

- GitHub webhook mode for automatic PR mission creation.
- Live mission progress over SSE or WebSocket.
- PDF export for public reports.
- One-command Docker demo seed with a sample mission.
