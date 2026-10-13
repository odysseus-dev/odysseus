"""USD cost tracking: pricing estimator, metrics plumbing, persistence.

Holds module objects (never string patch targets) — test_fenced_inline_args
pops/reimports src modules at collection time, so string-based patches on
src.* / routes.* can bind to stale module objects in full runs.
"""

from __future__ import annotations

import math
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import src.model_pricing as model_pricing
import src.agent_loop as agent_loop
import routes.chat_helpers as chat_helpers
from core.database import Base, Session as DbSession


# --- pricing table / matching ---

def test_match_model_key_prefers_longest_substring():
    # "gpt-4o-mini" must not bill at "gpt-4o" rates (~16x).
    assert model_pricing.match_model_key("openai/gpt-4o-mini", model_pricing.MODEL_PRICING) == "gpt-4o-mini"
    assert model_pricing.match_model_key("GPT-4O-MINI", model_pricing.MODEL_PRICING) == "gpt-4o-mini"
    assert model_pricing.match_model_key("gpt-4o", model_pricing.MODEL_PRICING) == "gpt-4o"


def test_estimate_cost_usd_computes_per_million_rates():
    # gpt-5: $2/$8 per 1M → 1000 in + 1000 out = $0.01
    assert model_pricing.estimate_cost_usd("openai/gpt-5", 1000, 1000, "https://openrouter.ai/api/v1") == pytest.approx(0.01)


def test_estimate_cost_usd_unknown_model_returns_none():
    assert model_pricing.estimate_cost_usd("totally-unknown-model", 1000, 1000, "https://openrouter.ai/api/v1") is None
    assert model_pricing.estimate_cost_usd("", 1000, 1000) is None
    assert model_pricing.estimate_cost_usd(None, 1000, 1000) is None


@pytest.mark.parametrize("url", [
    None,
    "",
    "http://localhost:11434/v1",
    "http://127.0.0.1:8000/v1",
    "http://192.168.1.21:8000/v1",
    "http://10.1.2.3/v1",
    "http://172.20.0.5:8000/v1",
    "http://100.101.5.7:8000/v1",       # Tailscale CGNAT
    "http://vllm.local/v1",
    "http://host.docker.internal/v1",
    "http://nim-nano/v1",               # single-label docker service
    "not a url",
])
def test_local_or_missing_endpoints_are_free(url):
    assert model_pricing.is_local_endpoint(url) is True
    # qwen would match the pricing table by substring — must NOT be billed.
    assert model_pricing.estimate_cost_usd("qwen3-coder", 1000, 1000, url) is None


@pytest.mark.parametrize("url", [
    "http://100.63.0.1/v1",    # below CGNAT range → public-ish, tracked
    "http://example.com/v1",
    "https://openrouter.ai/api/v1",
])
def test_public_endpoints_are_cost_tracked(url):
    assert model_pricing.is_local_endpoint(url) is False
    assert model_pricing.is_cost_tracked_endpoint(url) is True


def test_subscription_endpoints_are_not_billed_per_token():
    assert model_pricing.is_subscription_endpoint("https://chatgpt.com/backend-api/codex") is True
    assert model_pricing.is_subscription_endpoint("https://chatgpt.com/backend-api/codex/responses") is True
    assert model_pricing.is_subscription_endpoint("https://chatgpt.com/api") is False
    assert model_pricing.is_cost_tracked_endpoint("https://chatgpt.com/backend-api/codex") is False


# --- _compute_final_metrics cost fields ---

def _metrics(**kw):
    base = dict(
        messages=[],
        full_response="hi",
        total_duration=1.0,
        time_to_first_token=0.1,
        context_length=100000,
        real_input_tokens=1000,
        real_output_tokens=1000,
        has_real_usage=True,
        tool_events=[],
        round_texts=[],
        model="openai/gpt-5",
    )
    base.update(kw)
    return agent_loop._compute_final_metrics(**base)


def test_final_metrics_prefer_reported_cost():
    m = _metrics(real_cost_usd=0.012345, endpoint_url="https://openrouter.ai/api/v1")
    assert m["cost_usd"] == pytest.approx(0.012345)
    assert m["cost_source"] == "reported"


def test_final_metrics_estimate_when_not_reported():
    m = _metrics(real_cost_usd=0.0, endpoint_url="https://openrouter.ai/api/v1")
    assert m["cost_usd"] == pytest.approx(0.01)
    assert m["cost_source"] == "estimated"


def test_final_metrics_omit_cost_for_unknown_model():
    m = _metrics(model="mystery-model", endpoint_url="https://openrouter.ai/api/v1")
    assert "cost_usd" not in m
    assert "cost_source" not in m


def test_final_metrics_omit_cost_for_local_endpoint():
    m = _metrics(model="qwen3", endpoint_url="http://localhost:11434/v1")
    assert "cost_usd" not in m
    assert "cost_source" not in m


def test_final_metrics_omit_cost_without_endpoint_url():
    # Unknown endpoint → bias to not over-bill (matches webui isLocalEndpoint).
    m = _metrics(real_cost_usd=0.0, endpoint_url=None)
    assert "cost_usd" not in m
    assert "cost_source" not in m


# --- persistence ---

def _setup_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'cost.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()
    try:
        db.add(DbSession(id="s1", name="Cost", endpoint_url="https://openrouter.ai/api/v1", model="openai/gpt-5", owner="pewds"))
        db.commit()
    finally:
        db.close()
    return factory


def test_accumulate_token_usage_persists_cost(tmp_path, monkeypatch):
    factory = _setup_db(tmp_path)
    monkeypatch.setattr(chat_helpers, "SessionLocal", factory)

    chat_helpers.accumulate_token_usage("s1", {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.0123})
    chat_helpers.accumulate_token_usage("s1", {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001, "cost_source": "estimated"})

    db = factory()
    try:
        row = db.query(DbSession).filter(DbSession.id == "s1").first()
        assert row.total_input_tokens == 110
        assert row.total_output_tokens == 55
        assert row.total_cost_usd == pytest.approx(0.0133)
    finally:
        db.close()


def test_accumulate_token_usage_ignores_bad_cost_values(tmp_path, monkeypatch):
    factory = _setup_db(tmp_path)
    monkeypatch.setattr(chat_helpers, "SessionLocal", factory)

    chat_helpers.accumulate_token_usage("s1", {"input_tokens": 1, "output_tokens": 1, "cost_usd": "garbage"})
    chat_helpers.accumulate_token_usage("s1", {"input_tokens": 1, "output_tokens": 1, "cost_usd": float("nan")})
    chat_helpers.accumulate_token_usage("s1", {"input_tokens": 1, "output_tokens": 1, "cost_usd": -5.0})

    db = factory()
    try:
        row = db.query(DbSession).filter(DbSession.id == "s1").first()
        assert row.total_cost_usd == 0.0
        assert row.total_input_tokens == 3
    finally:
        db.close()


def test_session_to_dict_includes_total_cost_usd(tmp_path):
    factory = _setup_db(tmp_path)
    db = factory()
    try:
        db.query(DbSession).filter(DbSession.id == "s1").first().total_cost_usd = 0.5
        db.commit()
        d = db.query(DbSession).filter(DbSession.id == "s1").first().to_dict()
        assert d["total_cost_usd"] == 0.5
    finally:
        db.close()


def test_migration_adds_total_cost_usd_idempotently(tmp_path, monkeypatch):
    import core.database as core_db

    db_file = tmp_path / "app.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(core_db, "DATABASE_URL", f"sqlite:///{db_file}")

    core_db._migrate_add_total_cost_usd()
    core_db._migrate_add_total_cost_usd()  # idempotent

    conn = sqlite3.connect(db_file)
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()]
    finally:
        conn.close()
    assert "total_cost_usd" in cols


def test_sessions_list_route_exposes_total_cost_usd():
    from pathlib import Path
    source = Path(__file__).parents[1].joinpath("routes/session_routes.py").read_text()
    assert '"total_cost_usd": s.total_cost_usd or 0.0' in source


def test_llm_core_captures_openrouter_usage_cost():
    from pathlib import Path
    source = Path(__file__).parents[1].joinpath("src/llm_core.py").read_text()
    assert 'u.get("cost")' in source
    assert '"cost_usd"' in source


def test_compact_response_includes_context_tokens_estimate():
    from pathlib import Path
    source = Path(__file__).parents[1].joinpath("routes/session_routes.py").read_text()
    assert '"context_tokens_estimate": context_tokens_estimate' in source
    assert "len(_message_text(m) or \"\") // 4 + 8" in source
