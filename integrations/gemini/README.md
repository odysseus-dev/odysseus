# Odysseus Gemini CLI Integration

This directory contains the Gemini CLI integration for Odysseus.

Gemini CLI connects to Odysseus in two complementary ways:

- **MCP servers** — direct tool access for memory, RAG, email, and image generation.
- **REST API** — full Odysseus feature set (todos, calendar, documents, email) gated by a scoped API token.

## Requirements

- [Gemini CLI](https://github.com/google-gemini/gemini-cli) installed (`npm install -g @google/gemini-cli`)
- Odysseus running locally (`python -m uvicorn app:app --port 7000`)
- The Odysseus Python venv set up (`python integrations/gemini/scripts/setup.py` handles this check)

## Quick Start

### 1. Wire the MCP servers

Run from the Odysseus project root:

```bash
python integrations/gemini/scripts/setup.py
```

This writes (or updates) `.gemini/settings.json` with the four built-in Odysseus MCP servers,
using the correct absolute paths for your machine. Safe to re-run.

Verify:

```bash
gemini mcp list
```

All four servers should show **Connected**:

| Server | Provides |
|---|---|
| `odysseus-memory` | Read / write persistent memory |
| `odysseus-rag` | Manage RAG document collections |
| `odysseus-email` | Read and draft emails |
| `odysseus-imagegen` | Generate images |

### 2. Create an API token (optional — for todos, calendar, documents)

1. Open Odysseus at `http://localhost:7000`.
2. Go to **Settings > Integrations > Add Integration > Gemini Agent**.
3. Choose the scopes you want to grant (todos, calendar, documents, email, memory).
4. Copy the generated token.
5. Expose the variables in your terminal session:

```bash
export ODYSSEUS_URL=http://127.0.0.1:7000
export ODYSSEUS_API_TOKEN=ody_your_token_here
```

On Windows (PowerShell):

```powershell
$env:ODYSSEUS_URL = "http://127.0.0.1:7000"
$env:ODYSSEUS_API_TOKEN = "ody_your_token_here"
```

### 3. Start Gemini CLI

```bash
gemini
```

Gemini now has access to your Odysseus memory, RAG documents, and (with a token) your todos,
calendar, and documents.

## Using the API helper

`scripts/odysseus_api.py` is a zero-dependency helper for the scoped REST API:

```bash
python integrations/gemini/scripts/odysseus_api.py capabilities
python integrations/gemini/scripts/odysseus_api.py todos list
python integrations/gemini/scripts/odysseus_api.py todos add "Review pull request"
python integrations/gemini/scripts/odysseus_api.py emails list 5
python integrations/gemini/scripts/odysseus_api.py emails read UID
python integrations/gemini/scripts/odysseus_api.py GET /api/codex/memory
python integrations/gemini/scripts/odysseus_api.py POST /api/codex/memory '{"text":"User prefers dark mode","category":"preference"}'
```

## Scope enforcement

The API token is scope-gated server-side. Every endpoint checks the token scopes before
touching user data. A `403` means the relevant toggle is off in
**Settings > Integrations > Gemini Agent** — enable it there, not by working around it.

## Security

- Never commit `.env`, API tokens, or `.gemini/settings.json` (it is gitignored).
- All data access goes through `/api/codex/*`. Do not use SSH, direct Python imports,
  SQLite queries, or MCP internals to bypass the token scope check.
- Never call `emails/send` unless the user explicitly asked to send.
