"""Monthly Claude spend caps per payment channel (src/claude_budget.py) and the llm_core wiring."""
import asyncio
import json

import pytest
from fastapi import HTTPException

from src import claude_budget, llm_core

ANTH = "https://anthropic-proxy.whisper-reiki.ru:4443/v1/messages"
TW = "https://api.timeweb.ai/v1"
MSG = [{"role": "user", "content": "x"}]


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "claude_spend.json"
    monkeypatch.setattr(claude_budget, "_ledger_path", lambda: str(path))
    monkeypatch.setattr(claude_budget, "limit_usd", lambda chan="anthropic": 7.0)
    return path


def _exhaust(ledger, chan="anthropic"):
    ledger.write_text(json.dumps({claude_budget._month(): {chan: {"usd": 7.0}}}))


def test_prices_at_list_rates():
    assert claude_budget.cost_usd("claude-sonnet-5", 1_000_000, 1_000_000) == pytest.approx(12.0)
    assert claude_budget.cost_usd("claude-opus-5-5", 1_000_000) == pytest.approx(4.0)
    assert claude_budget.cost_usd("claude-opus-4-6", 1_000_000) == pytest.approx(5.0)
    assert claude_budget.cost_usd("claude-haiku-4-5", cache_write_tokens=1_000_000,
                                  cache_read_tokens=1_000_000) == pytest.approx(1.35)
    assert claude_budget.cost_usd("claude-something-new", 1_000_000) == pytest.approx(10.0)
    assert claude_budget.cost_usd("anthropic/claude-sonnet-5", 1_000_000) == pytest.approx(2.0)


def test_channel_by_host():
    assert claude_budget.channel(TW) == "timeweb"
    assert claude_budget.channel("https://api.timeweb.ai./v1") == "timeweb"
    assert claude_budget.channel(ANTH) == "anthropic"
    assert claude_budget.channel("https://api.anthropic.com") == "anthropic"
    assert claude_budget.channel("https://timeweb.ai.evil.example/v1") == "anthropic"
    assert claude_budget.channel(None) == "anthropic"


def test_non_claude_models_are_never_counted_or_blocked(ledger):
    assert claude_budget.record("deepseek/deepseek-v4.1-flash", 10**9, 10**9) == 0.0
    assert not ledger.exists()
    _exhaust(ledger)
    assert claude_budget.block_reason("deepseek/deepseek-v4.1-flash", MSG) is None


def test_records_accumulate_per_channel(ledger):
    claude_budget.record("claude-sonnet-5", input_tokens=500_000, url=ANTH)  # $1
    claude_budget.record("claude-sonnet-5", output_tokens=100_000, url=ANTH)  # $1
    claude_budget.record("claude-sonnet-5", output_tokens=300_000, url=TW)  # $3
    assert claude_budget.spent_usd("anthropic") == pytest.approx(2.0)
    assert claude_budget.spent_usd("timeweb") == pytest.approx(3.0)
    entry = json.loads(ledger.read_text())[claude_budget._month()]
    assert entry["anthropic"]["calls"] == 2
    assert entry["timeweb"]["by_model"]["claude-sonnet-5"] == pytest.approx(3.0)


def test_channels_are_capped_independently(ledger):
    _exhaust(ledger, "timeweb")
    assert "(timeweb)" in claude_budget.block_reason("claude-sonnet-5", MSG, TW)
    assert claude_budget.block_reason("claude-sonnet-5", MSG, ANTH) is None
    _exhaust(ledger, "anthropic")
    assert "(anthropic)" in claude_budget.block_reason("claude-sonnet-5", MSG, ANTH)
    assert claude_budget.block_reason("claude-sonnet-5", MSG, TW) is None


def test_blocks_at_limit_and_counts_projected_input(ledger):
    claude_budget.record("claude-sonnet-5", output_tokens=690_000, url=ANTH)  # $6.90
    assert claude_budget.block_reason("claude-sonnet-5", MSG, ANTH) is None
    big = [{"role": "user", "content": "x" * 400_000}]  # ≈100k tokens ≈ $0.20
    assert "Monthly Claude budget reached" in claude_budget.block_reason("claude-sonnet-5", big, ANTH)
    claude_budget.record("claude-sonnet-5", output_tokens=10_000, url=ANTH)  # $7.00
    with pytest.raises(HTTPException) as exc:
        claude_budget.check_or_raise("claude-sonnet-5", MSG, ANTH)
    assert exc.value.status_code == 402


def test_previous_month_does_not_count(ledger):
    ledger.write_text(json.dumps({"2000-01": {"anthropic": {"usd": 100.0}}}))
    assert claude_budget.spent_usd() == 0.0
    assert claude_budget.block_reason("claude-sonnet-5", MSG, ANTH) is None


def test_zero_limit_disables_cap(ledger, monkeypatch):
    monkeypatch.setattr(claude_budget, "limit_usd", lambda chan="anthropic": 0.0)
    _exhaust(ledger)
    assert claude_budget.block_reason("claude-sonnet-5", MSG, ANTH) is None


def test_limit_reads_channel_setting(monkeypatch):
    import src.settings as settings
    vals = {"claude_monthly_budget_usd": "12.5", "claude_timeweb_monthly_budget_usd": 3}
    monkeypatch.setattr(settings, "get_setting", lambda k, d=None: vals.get(k, d))
    assert claude_budget.limit_usd("anthropic") == 12.5
    assert claude_budget.limit_usd("timeweb") == 3.0
    monkeypatch.setattr(settings, "get_setting", lambda k, d=None: d)
    assert claude_budget.limit_usd("timeweb") == claude_budget.DEFAULT_MONTHLY_LIMIT_USD


def test_both_caps_default_to_7():
    from src.settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["claude_monthly_budget_usd"] == 7.0
    assert DEFAULT_SETTINGS["claude_timeweb_monthly_budget_usd"] == 7.0


def test_llm_call_async_refuses_claude_without_network(ledger, monkeypatch):
    _exhaust(ledger)

    def boom(*a, **k):
        raise AssertionError("network must not be touched when over budget")
    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", boom)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(llm_core.llm_call_async(ANTH, "claude-sonnet-5",
                                            [{"role": "user", "content": "budget-test-unique-1"}]))
    assert exc.value.status_code == 402


def test_sync_llm_call_refuses_timeweb_claude(ledger, monkeypatch):
    _exhaust(ledger, "timeweb")
    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    with pytest.raises(HTTPException) as exc:
        llm_core.llm_call(TW, "claude-opus-4-6", [{"role": "user", "content": "budget-test-unique-2"}])
    assert exc.value.status_code == 402


class _Resp:
    is_success = True
    status_code = 200
    text = ""

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def test_llm_call_async_records_anthropic_usage(ledger, monkeypatch):
    data = {"content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 1_000_000, "output_tokens": 0,
                      "cache_read_input_tokens": 1_000_000}}

    async def fake_post(*a, **k):
        return _Resp(data)
    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    out = asyncio.run(llm_core.llm_call_async(ANTH, "claude-sonnet-5",
                                              [{"role": "user", "content": "budget-test-unique-3"}]))
    assert out == "ok"
    assert claude_budget.spent_usd("anthropic") == pytest.approx(2.2)  # $2 input + $0.2 cache read
    assert claude_budget.spent_usd("timeweb") == 0.0


def test_llm_call_async_records_timeweb_usage(ledger, monkeypatch):
    data = {"choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 100_000}}

    async def fake_post(*a, **k):
        return _Resp(data)
    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    asyncio.run(llm_core.llm_call_async(TW, "claude-sonnet-5",
                                        [{"role": "user", "content": "budget-test-unique-4"}]))
    assert claude_budget.spent_usd("timeweb") == pytest.approx(1.0)


def _collect(agen):
    async def run():
        return [c async for c in agen]
    return asyncio.run(run())


def test_stream_blocked_yields_error_event(ledger):
    _exhaust(ledger)
    chunks = _collect(llm_core.stream_llm(ANTH, "claude-sonnet-5", MSG))
    assert len(chunks) == 1 and chunks[0].startswith("event: error")
    assert '"status": 402' in chunks[0]


def test_stream_records_usage_event(ledger, monkeypatch):
    async def fake_inner(url, model, messages, **kw):
        yield 'data: {"delta": "hello"}\n\n'
        yield 'data: ' + json.dumps({"type": "usage", "data": {"input_tokens": 0, "output_tokens": 100_000}}) + '\n\n'
        yield "data: [DONE]\n\n"
    monkeypatch.setattr(llm_core, "_stream_llm_inner", fake_inner)
    chunks = _collect(llm_core.stream_llm(TW, "claude-sonnet-5", MSG))
    assert chunks[-1] == "data: [DONE]\n\n"
    assert claude_budget.spent_usd("timeweb") == pytest.approx(1.0)


def test_stream_without_usage_is_estimated(ledger, monkeypatch):
    async def fake_inner(url, model, messages, **kw):
        yield 'data: {"delta": "' + "y" * 4000 + '"}\n\n'
    monkeypatch.setattr(llm_core, "_stream_llm_inner", fake_inner)
    _collect(llm_core.stream_llm(ANTH, "claude-sonnet-5", MSG))
    entry = json.loads(ledger.read_text())[claude_budget._month()]["anthropic"]
    assert entry["estimated_calls"] == 1
    assert entry["usd"] > 0


def test_stream_non_claude_untouched(ledger, monkeypatch):
    _exhaust(ledger)

    async def fake_inner(url, model, messages, **kw):
        yield 'data: {"delta": "hi"}\n\n'
    monkeypatch.setattr(llm_core, "_stream_llm_inner", fake_inner)
    chunks = _collect(llm_core.stream_llm("https://api.novita.ai/openai/v1", "deepseek/deepseek-v4.1-flash", MSG))
    assert chunks == ['data: {"delta": "hi"}\n\n']


def test_fallback_chain_moves_past_blocked_claude(ledger, monkeypatch):
    _exhaust(ledger)

    async def fake_inner(url, model, messages, **kw):
        yield 'data: {"delta": "from fallback"}\n\n'
        yield "data: [DONE]\n\n"
    monkeypatch.setattr(llm_core, "_stream_llm_inner", fake_inner)
    chunks = _collect(llm_core.stream_llm_with_fallback(
        [(ANTH, "claude-opus-4-6", None),
         ("https://api.novita.ai/openai/v1", "deepseek/deepseek-v4.1-flash", None)], MSG))
    joined = "".join(chunks)
    assert "from fallback" in joined
    assert '"type": "fallback"' in joined


@pytest.mark.parametrize("key", ["claude_monthly_budget_usd", "claude_timeweb_monthly_budget_usd"])
def test_agent_cannot_raise_the_caps_from_chat(monkeypatch, key):
    import src.settings as settings_mod
    from src.agent_tools.admin_tools import do_manage_settings
    store = {}
    monkeypatch.setattr(settings_mod, "load_settings", lambda: dict(store))
    monkeypatch.setattr(settings_mod, "save_settings", lambda s: store.update(s))
    result = asyncio.run(do_manage_settings(json.dumps({"action": "set", "key": key, "value": 1000})))
    assert "spend limit" in result.get("response", ""), result
    assert key not in store
    # control: an ordinary numeric setting is still writable
    asyncio.run(do_manage_settings(json.dumps({"action": "set", "key": "agent_max_rounds", "value": 30})))
    assert store.get("agent_max_rounds") == 30
