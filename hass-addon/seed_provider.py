#!/usr/bin/env python3
"""Seed a default model endpoint from the Home Assistant add-on options.

Reads ODYSSEUS_SEED_PROVIDER / ODYSSEUS_SEED_API_KEY / ODYSSEUS_SEED_MODEL and
creates (or updates) a shared model endpoint for the selected provider. The
endpoint's api_key is stored through the app's encrypted column type, so the
plaintext key only ever exists in this process and the add-on options file.

Run from run.sh after the database has been initialised. No-op when no provider
or key is configured.
"""

import os
import sys
import uuid

sys.path.insert(0, "/app")

# Provider -> OpenAI-compatible (or natively detected) base URL. Anthropic is
# recognised by host in the app (endpoint_resolver/_detect_provider), the rest
# are standard OpenAI-compatible /v1 roots.
PROVIDERS = {
    "openai": ("OpenAI", "https://api.openai.com/v1"),
    "anthropic": ("Anthropic", "https://api.anthropic.com/v1"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1"),
    "groq": ("Groq", "https://api.groq.com/openai/v1"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com/v1"),
    "google": ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
    "mistral": ("Mistral", "https://api.mistral.ai/v1"),
}


def main() -> int:
    provider = (os.environ.get("ODYSSEUS_SEED_PROVIDER") or "").strip().lower()
    api_key = (os.environ.get("ODYSSEUS_SEED_API_KEY") or "").strip()
    model = (os.environ.get("ODYSSEUS_SEED_MODEL") or "").strip()

    if provider in ("", "none") or not api_key:
        return 0

    entry = PROVIDERS.get(provider)
    if entry is None:
        print(f"[odysseus] unknown llm_provider {provider!r}; skipping seed", file=sys.stderr)
        return 0

    label, base_url = entry
    name = f"{label} (add-on)"

    import json

    from core.database import ModelEndpoint, get_db_session

    pinned = json.dumps([model]) if model else None

    with get_db_session() as db:
        ep = db.query(ModelEndpoint).filter(ModelEndpoint.name == name).first()
        if ep is None:
            ep = ModelEndpoint(
                id=uuid.uuid4().hex[:12],
                name=name,
                base_url=base_url,
                api_key=api_key,
                is_enabled=True,
                endpoint_kind="api",
                model_type="llm",
                owner=None,
                pinned_models=pinned,
            )
            db.add(ep)
        else:
            ep.base_url = base_url
            ep.api_key = api_key
            ep.is_enabled = True
            if pinned:
                ep.pinned_models = pinned

    print(f"[odysseus] seeded model endpoint '{name}' ({base_url})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())