"""Collector contract: drop secrets and prompt bodies from span attributes.

Agents: keep this list in sync with deploy/observability/otel-collector.yaml.
"""
DENY_SUBSTRINGS = (
    "authorization",
    "api_key",
    "cookie",
    "x-9r-cli-token",
    "virtual_key",
)
DROP_KEYS = frozenset({"gen_ai.input.messages", "gen_ai.output.messages"})


def redact_span_attributes(attrs: dict[str, object]) -> dict[str, object]:
    kept: dict[str, object] = {}
    for key, value in attrs.items():
        low = str(key).lower()
        if key in DROP_KEYS or any(s in low for s in DENY_SUBSTRINGS):
            continue
        if isinstance(value, str) and value.startswith("enc:"):
            continue
        kept[key] = value
    return kept
