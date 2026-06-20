# Odysseus Antigravity Code Integration

This directory contains the Antigravity Code skill bundle for Odysseus.

## User Flow

1. Open Odysseus Settings > Integrations.
2. Add an Antigravity Agent.
3. Copy the full setup commands shown after the generated token.
4. Toggle the tools Antigravity is allowed to use.
5. Configure the terminal Antigravity session:

```bash
export ODYSSEUS_URL=http://your-odysseus-host:7000
export ODYSSEUS_API_TOKEN=ody_generated_token
mkdir -p ~/.gemini/config/skills/odysseus
curl -fsSL -H "Authorization: Bearer $ODYSSEUS_API_TOKEN" "$ODYSSEUS_URL/api/agy/plugin.zip" -o /tmp/odysseus-agy-skill.zip
python3 -m zipfile -e /tmp/odysseus-agy-skill.zip ~/.gemini/config/skills/
```

Antigravity auto-loads anything under `~/.gemini/config/skills/`, so the `odysseus` skill is available in any session that has `ODYSSEUS_URL` and `ODYSSEUS_API_TOKEN` in its environment.

## What's in the bundle

- `skills/odysseus/SKILL.md` — the skill definition Antigravity reads.
- `skills/odysseus/scripts/odysseus_api.py` — small helper that calls the scoped `/api/codex/*` endpoints.

## Scope enforcement

The token is scope-gated. Every tool surface is checked server-side in Odysseus, so even if Antigravity tries to call a forbidden endpoint, it gets `403` until the user enables the matching toggle in Settings > Integrations > Antigravity Agent.
