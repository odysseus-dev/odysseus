"""Pin turn pricing used by the compare view and dotted Claude model names.

Runs the real pricing table and functions from static/js/chatRenderer.js
(extracted by source, since the module itself needs a DOM) plus the real
static/js/model/matchKey.js under node. Skips when `node` is not installed.

Regressions covered:
- The compare view priced a multi-round agent turn as one prompt (summed input,
  no cache split), so the cache discount never applied and the Haiku 5.5
  long-prompt tier could trigger on the sum of several small rounds.
- OpenRouter-style dotted names ("anthropic/claude-opus-4.5") fell back to an
  older key ("claude-opus-4") and were billed at the wrong rates.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SOURCE = (_REPO / "static" / "js" / "chatRenderer.js").read_text(encoding="utf-8")
_MATCH_KEY = (_REPO / "static" / "js" / "model" / "matchKey.js").as_uri()
_HAS_NODE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def _block(pattern):
    match = re.search(pattern, _SOURCE, re.MULTILINE | re.DOTALL)
    assert match, f"{pattern} not found"
    return match.group(0).replace("export function", "function", 1)


def _function_source(name):
    return _block(rf"^(?:export )?function {name}\(.*?^\}}")


def _pricing_prelude():
    return "\n".join([
        f"import {{ matchModelKey }} from '{_MATCH_KEY}';",
        _block(r"^const MODEL_INFO = \{.*?^\};"),
        "const MODEL_PRICING = MODEL_INFO;",
        _block(r"^const _CLAUDE_CACHE_WRITE_MULT = .*?;"),
        _block(r"^const _CLAUDE_CACHE_READ_MULT = \{.*?^\};"),
        _block(r"^const _LONG_PROMPT_PRICING = .*?;"),
        _function_source("getModelCost"),
        _function_source("getMetricsModelCost"),
        _function_source("_backendTurnCost"),
        # The session-cost path, with the endpoint lookups stubbed: only the
        # Anthropic URL is a billed route, anything else is local/free.
        "function _currentEndpointUrl() { return null; }",
        "function isCostTrackedEndpoint(url) { return url === 'https://api.anthropic.com/v1'; }",
        _function_source("_isBillableRoute"),
        _function_source("_billableCost"),
        _function_source("_metricsBillableCost"),
    ])


def _run(body):
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=_pricing_prelude() + "\n" + body,
        capture_output=True, text=True, timeout=30, cwd=str(_REPO),
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_multi_round_turn_is_priced_per_round_with_its_cache_split():
    out = _run("""
const metrics = {model: 'claude-sonnet-4-5', input_tokens: 41000, output_tokens: 20,
  usage_buckets: [
    {model: 'claude-sonnet-4-5', input_tokens: 20500, output_tokens: 10, cache_creation_input_tokens: 20000},
    {model: 'claude-sonnet-4-5', input_tokens: 20500, output_tokens: 10, cache_read_input_tokens: 20000},
  ]};
console.log(JSON.stringify({cost: getMetricsModelCost(metrics, 'ignored')}));
""")
    expected = (
        3.00 * (500 + 20_000 * 1.25) + 15.00 * 10
        + 3.00 * (500 + 20_000 * 0.10) + 15.00 * 10
    ) / 1_000_000
    assert out["cost"] == pytest.approx(expected)


def test_long_prompt_tier_is_not_applied_to_the_round_sum():
    out = _run("""
const metrics = {model: 'claude-haiku-5-5', input_tokens: 120000, output_tokens: 0,
  usage_buckets: [
    {model: 'claude-haiku-5-5', input_tokens: 60000, output_tokens: 0},
    {model: 'claude-haiku-5-5', input_tokens: 60000, output_tokens: 0},
  ]};
console.log(JSON.stringify({cost: getMetricsModelCost(metrics, '')}));
""")
    assert out["cost"] == pytest.approx(0.10 * 120_000 / 1_000_000)


def test_single_request_uses_top_level_cache_split():
    out = _run("""
const metrics = {model: 'claude-opus-4-6', input_tokens: 40300, output_tokens: 0,
  cache_read_input_tokens: 40000};
console.log(JSON.stringify({cost: getMetricsModelCost(metrics, '')}));
""")
    assert out["cost"] == pytest.approx(5.00 * (300 + 40_000 * 0.10) / 1_000_000)


def test_unpriceable_turn_returns_null():
    out = _run("""
console.log(JSON.stringify({
  none: getMetricsModelCost(null, 'claude-opus-4-6'),
  unknown: getMetricsModelCost({model: 'mystery-model', input_tokens: 10, output_tokens: 1}, ''),
  allUnknown: getMetricsModelCost({usage_buckets: [{model: 'mystery-model', input_tokens: 10, output_tokens: 1}]}, ''),
}));
""")
    assert out == {"none": None, "unknown": None, "allUnknown": None}


@pytest.mark.parametrize("name,key", [
    ("anthropic/claude-opus-4.5", "claude-opus-4-5"),
    ("anthropic/claude-haiku-4.5", "claude-haiku-4-5"),
    ("anthropic/claude-sonnet-5.5", "claude-sonnet-5-5"),
    ("claude-opus-4-5-20251101", "claude-opus-4-5"),
    ("qwen2.5-coder-32b-claude-distill", "qwen2.5"),
    ("meta-llama/llama-3.1-8b-claude-3.5-sonnet-distill", "llama-3.1"),
    ("gpt-4.1-claude", "gpt-4.1"),
    ("gemini-2.5-pro-claude-style", "gemini-2.5-pro"),
    ("gpt-4.1-mini", "gpt-4.1-mini"),
])
def test_dotted_claude_names_match_their_dashed_key(name, key):
    out = _run(f"""
console.log(JSON.stringify({{key: matchModelKey({json.dumps(name)}, Object.keys(MODEL_PRICING))}}));
""")
    assert out["key"] == key


def test_dotted_openrouter_opus_4_5_billed_at_opus_4_5_rates():
    out = _run("""
console.log(JSON.stringify({cost: getModelCost('anthropic/claude-opus-4.5', 1000000, 0)}));
""")
    assert out["cost"] == pytest.approx(5.00)


# platform.claude.com "Context windows": these models have a 1M-token window
# by default (no beta header); other Claude models, Sonnet 4.5 included, 200k.
_ONE_MILLION_CONTEXT = [
    "claude-fable-5-1", "claude-fable-5", "claude-mythos-5-1", "claude-mythos-5",
    "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-sonnet-5",
    "claude-haiku-5-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-4-6",
]


def test_claude_context_windows_match_anthropic_docs():
    out = _run("""
console.log(JSON.stringify(Object.fromEntries(
  Object.entries(MODEL_INFO).map(([key, info]) => [key, info.ctx ?? null]))));
""")
    assert {key: out[key] for key in _ONE_MILLION_CONTEXT} == dict.fromkeys(_ONE_MILLION_CONTEXT, 1_000_000)
    for key in ("claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"):
        assert out[key] == 200_000, key


# A billed round on a model missing from MODEL_INFO can't be priced in the
# browser. The backend still sends its cost for the turn (cost_usd, estimated
# or provider-reported); using it keeps the browser from silently dropping
# that round and showing a lower cost than the one stored for the turn.
_KNOWN = "{model: 'claude-sonnet-4-5', input_tokens: 10000, output_tokens: 100}"
_UNKNOWN = "{model: 'mystery-model', input_tokens: 10000, output_tokens: 100}"
_KNOWN_COST = (3.00 * 10_000 + 15.00 * 100) / 1_000_000


def test_compare_cost_uses_backend_cost_when_a_round_is_unpriced():
    out = _run(f"""
console.log(JSON.stringify({{
  mixed: getMetricsModelCost({{usage_buckets: [{_KNOWN}, {_UNKNOWN}], cost_usd: 0.5}}, ''),
  noBackend: getMetricsModelCost({{usage_buckets: [{_KNOWN}, {_UNKNOWN}]}}, ''),
  nullBackend: getMetricsModelCost({{usage_buckets: [{_UNKNOWN}], cost_usd: null}}, ''),
  single: getMetricsModelCost({{model: 'mystery-model', input_tokens: 5, output_tokens: 1, cost_usd: 0.2}}, ''),
  allKnown: getMetricsModelCost({{usage_buckets: [{_KNOWN}], cost_usd: 9}}, ''),
}}));
""")
    assert out["mixed"] == pytest.approx(0.5)
    assert out["noBackend"] == pytest.approx(_KNOWN_COST)  # nothing better to show
    assert out["nullBackend"] is None  # a missing backend cost is not $0
    assert out["single"] == pytest.approx(0.2)
    assert out["allKnown"] == pytest.approx(_KNOWN_COST)  # fully priced: unchanged


def test_session_cost_uses_backend_cost_only_for_billed_unpriced_rounds():
    paid = "endpoint_cost_tracked: true"
    free = "endpoint_cost_tracked: false"
    out = _run(f"""
const known = {_KNOWN}, unknown = {_UNKNOWN};
const at = (b, flag) => ({{...b, ...flag}});
console.log(JSON.stringify({{
  billedUnknown: _metricsBillableCost(
    {{usage_buckets: [at(known, {{{paid}}}), at(unknown, {{{paid}}})], cost_usd: 0.5}}, '', 0, 0, null),
  freeUnknown: _metricsBillableCost(
    {{usage_buckets: [at(known, {{{paid}}}), at(unknown, {{{free}}})], cost_usd: 0.5}}, '', 0, 0, null),
  allFree: _metricsBillableCost(
    {{usage_buckets: [at(unknown, {{{free}}})], cost_usd: 0.5}}, '', 0, 0, null),
  singleBilledUnknown: _metricsBillableCost(
    {{model: 'mystery-model', {paid}, cost_usd: 0.3}}, 'mystery-model', 5, 1, null),
  singleFree: _metricsBillableCost(
    {{model: 'mystery-model', {free}, cost_usd: 0.3}}, 'mystery-model', 5, 1, null),
}}));
""")
    assert out["billedUnknown"] == pytest.approx(0.5)
    # A free (local/subscription) round costs nothing; it is not "unpriced".
    assert out["freeUnknown"] == pytest.approx(_KNOWN_COST)
    assert out["allFree"] is None
    assert out["singleBilledUnknown"] == pytest.approx(0.3)
    assert out["singleFree"] is None


def _js_tables():
    return _run("""
console.log(JSON.stringify({
  prices: Object.fromEntries(Object.entries(MODEL_INFO).map(([key, info]) => [key, [info.input, info.output]])),
  ctx: Object.fromEntries(Object.entries(MODEL_INFO).map(([key, info]) => [key, info.ctx ?? null])),
  cacheWrite: _CLAUDE_CACHE_WRITE_MULT,
  cacheRead: _CLAUDE_CACHE_READ_MULT,
  longPrompt: _LONG_PROMPT_PRICING,
}));
""")


def test_price_tables_match_src_model_pricing():
    # The webui keeps its own copy of the price table (chatRenderer.js has no
    # backend round trip for session cost); both copies are edited by hand, so
    # a price changed in one must fail here until the other matches.
    from src import model_pricing

    out = _js_tables()
    assert out["prices"] == {key: list(price) for key, price in model_pricing.MODEL_PRICING.items()}
    assert out["cacheWrite"] == model_pricing._ANTHROPIC_CACHE_WRITE_MULT
    assert out["cacheRead"] == model_pricing._ANTHROPIC_CACHE_READ_MULT
    assert out["longPrompt"] == {
        key: [threshold, price_in, price_out]
        for key, (threshold, (price_in, price_out)) in model_pricing._LONG_PROMPT_PRICING.items()
    }


def test_claude_context_windows_match_src_model_context():
    # The model info popup's Claude windows must agree with the server's
    # known-windows lookup, which context % is computed from.
    from src.model_context import _lookup_known

    out = _js_tables()
    claude = {key: ctx for key, ctx in out["ctx"].items() if key.startswith("claude-")}
    assert claude, "no Claude rows found in MODEL_INFO"
    assert claude == {key: _lookup_known(key) for key in claude}
