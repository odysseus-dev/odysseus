"""Provider specifications — data-only descriptors.

A `ProviderSpec` names a provider, the transport that speaks its wire format, how
to recognize its endpoints, and how it authenticates. It holds NO behavior beyond
URL matching; request shaping lives in the transport, credential resolution in
`providers.auth`.

`auth_type` is `"api_key"` for the Anthropic and OpenAI builtins, and `"oauth"`
for the ChatGPT-subscription / codex provider.
"""
from dataclasses import dataclass
from typing import Tuple

# Curated Anthropic model list (no public /models endpoint to probe).
ANTHROPIC_MODELS = [
    "claude-opus-4-20250514", "claude-opus-4",
    "claude-sonnet-4-20250514", "claude-sonnet-4", "claude-sonnet-4-5-20250929", "claude-sonnet-4-5",
    "claude-haiku-4-20250514", "claude-haiku-4", "claude-haiku-3-5-20241022", "claude-haiku-3-5",
]


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    transport: str            # transport id, resolved via registry.get_transport
    auth_type: str            # "api_key" | "oauth"
    url_matchers: Tuple[str, ...]
    default_chat_path: str
    model_list_mode: str      # "static" | "probe"

    def matches(self, url: str) -> bool:
        u = url or ""
        return any(m in u for m in self.url_matchers)


ANTHROPIC_SPEC = ProviderSpec(
    id="anthropic",
    label="Anthropic",
    transport="anthropic_messages",
    auth_type="api_key",
    url_matchers=("anthropic.com",),
    default_chat_path="/v1/messages",
    model_list_mode="static",
)

# Curated codex model list (ChatGPT-subscription; no public /models probe to ask).
# Maintained by hand against the backend's current lineup.
CODEX_MODELS = ["gpt-5.5", "gpt-5.4", "gpt-5.3-codex"]

# ChatGPT-subscription provider: the codex Responses backend behind an OAuth
# session. URL-matched on the distinctive backend path; never the default.
OPENAI_CODEX_SPEC = ProviderSpec(
    id="openai-codex",
    label="ChatGPT (Codex)",
    transport="codex_responses",
    auth_type="oauth",
    url_matchers=("chatgpt.com/backend-api/codex",),
    default_chat_path="/responses",
    model_list_mode="static",
)

# The default/fallthrough provider: any OpenAI-compatible host (OpenAI, xAI, Groq,
# OpenRouter, local Ollama/vLLM, …). Empty matchers → only selected as the default.
OPENAI_SPEC = ProviderSpec(
    id="openai",
    label="OpenAI",
    transport="openai_chat",
    auth_type="api_key",
    url_matchers=(),
    default_chat_path="/chat/completions",
    model_list_mode="probe",
)

# Order matters: specific matchers first, the empty-matcher default last.
BUILTIN_SPECS = (ANTHROPIC_SPEC, OPENAI_CODEX_SPEC, OPENAI_SPEC)
