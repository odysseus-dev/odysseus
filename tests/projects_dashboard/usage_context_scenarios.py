"""Context restore for stored conversations, against a REAL temporary SessionDB (no mocks of the DB). Only the model-window resolver is
substituted, because it would otherwise consult provider catalogs. argv[1] = patched Hermes source, argv[2] = scratch HERMES_HOME."""
import json, os, sys, time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1]); os.environ["HERMES_HOME"] = sys.argv[2]
from unittest import mock
from hermes_state import SessionDB
from agent.usage_anchor import capture_usage_anchor
from agent import model_metadata as mm
from agent.context_default_record import mark_context_defaulted, records_context_defaults
from gateway import session_usage as su
from tui_gateway.contracts.common import Usage

home = Path(sys.argv[2]) / ("db_" + str(os.getpid()))
db = SessionDB(home / "state.db")
authority = SimpleNamespace(db=db, sessions={}, agent=lambda ref: None)
WINDOWS = {"meituan/longcat-2.5:free": 1_000_000, "claude-sonnet-5-5[1m]": 1_000_000, "tiny-model": 32_768}


@records_context_defaults
def fake_resolver(model, base_url="", api_key="", config_context_length=None, provider="", custom_providers=None):
    if model in WINDOWS:
        return WINDOWS[model]
    mark_context_defaulted()
    return 256_000


def session(sid, model, msgs, anchor_prompt=None, summaries=0, **row):
    db.create_session(sid, "local", model=model)
    for r, c in msgs:
        db.append_message(sid, r, c)
    conv = db.get_messages_as_conversation(sid)
    if anchor_prompt is not None:
        db.patch_session_model_config(sid, {"_usage_anchor": capture_usage_anchor(anchor_prompt, 46, conv)})
    for i in range(summaries):
        db.append_message(sid, "user", "[summary %d]" % i, _compressed_summary=True)
    return conv


def snap(sid):
    su._WINDOW_CACHE.clear()
    return su.snapshot_usage(authority, SimpleNamespace(session_id=sid), None)


M = [("user", "hello there"), ("assistant", "hi"), ("user", "do the thing")]
out = {}
with mock.patch.object(mm, "get_model_context_length", fake_resolver):
    session("new_reported", "meituan/longcat-2.5:free", M, anchor_prompt=16_586)
    out["reported"] = snap("new_reported")
    session("plus_estimate", "meituan/longcat-2.5:free", M, anchor_prompt=16_586)
    db.append_message("plus_estimate", "assistant", "x" * 400)
    db.append_message("plus_estimate", "user", "y" * 400)
    out["plus_estimate"] = snap("plus_estimate")
    session("no_reading", "claude-sonnet-5-5[1m]", M)
    out["no_reading"] = snap("no_reading")
    session("unknown_window", "mystery-model", M, anchor_prompt=5_000)
    out["unknown_window"] = snap("unknown_window")
    session("compacted", "tiny-model", M, anchor_prompt=9_000)
    conv = db.get_messages_as_conversation("compacted")
    db._write_sql("UPDATE messages SET content = ? WHERE session_id = ? AND role = 'assistant'", ("rewritten by a compaction", "compacted"))
    out["stale_anchor"] = snap("compacted")
    db.create_session("empty_one", "local", model="tiny-model")
    out["empty"] = snap("empty_one")
    session("with_summaries", "tiny-model", M, anchor_prompt=4_000, summaries=3)
    db._write_sql("UPDATE sessions SET compression_failure_error = ?, compression_failure_cooldown_until = ? WHERE id = ?",
                  ("summary model unavailable", time.time() + 600, "with_summaries"))
    out["summaries"] = snap("with_summaries")
    out["switch_a"] = snap("new_reported")
    db._write_sql("UPDATE sessions SET model = ? WHERE id = ?", ("tiny-model", "new_reported"))
    out["switch_b"] = snap("new_reported")
    out["gone"] = su.snapshot_usage(authority, SimpleNamespace(session_id="nope"), None)
    for k in ("reported", "plus_estimate", "no_reading", "unknown_window", "stale_anchor", "empty", "summaries", "switch_b"):
        Usage(**out[k])  # the frozen contract still accepts every restored shape

def usage_row(sid, model, provider, task, calls, tin, tout, cr, cw, est, act, status, source, mode=""):
    db._write_sql("INSERT INTO session_model_usage (session_id, model, billing_provider, billing_base_url, billing_mode, task, api_call_count, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, estimated_cost_usd, actual_cost_usd, cost_status, cost_source, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sid, model, provider, "", mode, task, calls, tin, tout, cr, cw, 0, est, act, status, source, 1.0, 2.0))

session("cost_sub", "claude-sonnet-5-5[1m]", M, anchor_prompt=1000)
usage_row("cost_sub", "claude-sonnet-5-5[1m]", "claude-subscription-directsdk-experimental", "", 65, 550, 40054, 5110122, 0, 2.105, 0.0, "estimated", "provider_cost_api")
usage_row("cost_sub", "claude-sonnet-5-5[1m]", "claude-subscription-directsdk-experimental", "title_generation", 1, 300, 12, 0, 0, 0.0, 0.0, None, None)
session("cost_api", "deepseek-chat", M, anchor_prompt=1000)
usage_row("cost_api", "deepseek-chat", "deepseek", "", 3, 12000, 400, 0, 0, 0.0008, 0.0, "estimated", "official_docs_snapshot")
usage_row("cost_api", "deepseek-reasoner", "deepseek", "", 1, 500, 20, 0, 0, 0.0001, 0.0002, "actual", "provider_generation_api")
session("cost_free", "meituan/longcat-2.5:free", M, anchor_prompt=1000)
usage_row("cost_free", "meituan/longcat-2.5:free", "nous", "", 13, 97563, 9738, 603008, 0, 0.0, 0.0, "unknown", "provider_models_api")
usage_row("cost_free", "gpt-6.1-sol", "openai-codex", "", 2, 800, 40, 0, 0, 0.0, 0.0, "included", "none", "subscription_included")
with mock.patch.object(mm, "get_model_context_length", fake_resolver):
    for k in ("cost_sub", "cost_api", "cost_free"):
        su._WINDOW_CACHE.clear(); out[k] = su.snapshot_usage(authority, SimpleNamespace(session_id=k), None)
        Usage(**out[k])
compressor = SimpleNamespace(threshold_tokens=500_000, threshold_percent=0.5, _config_context_length=None, last_prompt_tokens=100, last_real_prompt_tokens=100)
agent = SimpleNamespace(context_compressor=compressor, compression_enabled=True)
extras = su.live_usage_extras(agent)
out["live_extras"] = extras
frame = su.usage_frame({"input_tokens": 1, "output_tokens": 2, "total_tokens": 3, "last_prompt_tokens": 100, "context_length": 1000, "context_source": "provider_usage"})
out["frame_unchanged"] = "compression_threshold_tokens" not in frame["usage"]
out["frame_nested_has_extras"] = extras.get("compression_threshold_tokens")
print("@@" + json.dumps(out))
