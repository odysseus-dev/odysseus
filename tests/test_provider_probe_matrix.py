"""Provider-probe contract matrix (ROADMAP "Provider setup/probing audit").

Behavior-first: the provider routing invariants are driven directly through
`routes.model_routes`, so a rename or refactor that breaks routing fails here.

The single source-text assertion in this module (the `/setup` provider list) is
a deliberate narrow exception, per the behavioral-first policy in
tests/TESTING_STANDARD.md: that list lives in browser-side JavaScript
(`SETUP_PROVIDER_NAMES` in `static/js/slashCommands.js`) and no pytest runtime
path renders that UI, so the file is the only place the invariant can be pinned.
The reason is repeated in that test's docstring.
"""
import re
from pathlib import Path

import routes.model_routes as mr

REPO = Path(__file__).resolve().parent.parent
SLASH_COMMANDS = REPO / "static/js/slashCommands.js"

# The seven providers named in ROADMAP.md's "Provider setup/probing audit".
PROVIDER_BASES = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "groq": "https://api.groq.com/openai/v1",
    "xai": "https://api.x.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com/v1",
}


def test_every_roadmap_provider_resolves_to_a_models_url():
    for name, base in PROVIDER_BASES.items():
        url = mr._safe_build_models_url(base)
        assert url.endswith("/models"), (name, url)
        assert " " not in url, (name, url)


def test_anthropic_and_google_route_to_their_native_branches():
    # Anthropic speaks its own /models API and Gemini uses the Google native
    # path; _probe_endpoint special-cases both before the OpenAI-compatible
    # branch. Pin the branch selection; the probing itself is covered
    # behaviorally in tests/test_endpoint_probing.py with mocked HTTP.
    assert mr._safe_detect_provider(PROVIDER_BASES["anthropic"]) == "anthropic"
    assert mr._is_google_api_base(PROVIDER_BASES["gemini"]) is True
    for name in ("openai", "groq", "xai", "openrouter", "deepseek"):
        base = PROVIDER_BASES[name]
        assert mr._is_google_api_base(base) is False, name
        assert mr._safe_detect_provider(base) != "anthropic", name


def test_setup_surface_covers_all_roadmap_providers():
    """Source-text exception: this list is browser-side JavaScript.

    Why (required by the behavioral-first policy): `SETUP_PROVIDER_NAMES` is
    consumed by the /setup slash-command chips in `static/js/slashCommands.js`.
    Nothing in a pytest process renders that UI, so there is no runtime call
    that can observe the list; reading the file is the only practical pin.
    """
    text = SLASH_COMMANDS.read_text(encoding="utf-8")
    m = re.search(r"SETUP_PROVIDER_NAMES\s*=\s*\[([^\]]*)\]", text)
    assert m, "SETUP_PROVIDER_NAMES array not found in slashCommands.js"
    slugs = {s.strip("'\", ") for s in m.group(1).split(",") if s.strip()}
    missing = set(PROVIDER_BASES) - slugs
    assert not missing, f"providers missing from /setup surface: {sorted(missing)}"