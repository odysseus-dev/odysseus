"""Seed the staging gateway's state.db with synthetic conversations (no real data). argv[1] = patched Hermes source, argv[2] = lab HERMES_HOME.
Needs LAB_USER_ID and LAB_RECEIPT_TEMPLATE (private files, not in the repository)."""
import hashlib, json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1]); os.environ["HERMES_HOME"] = sys.argv[2]
from hermes_state import SessionDB
from agent.usage_anchor import capture_usage_anchor
db = SessionDB(Path(sys.argv[2]) / "state.db")
now = time.time()
M = [("user", "Please review the billing module."), ("assistant", "I reviewed billing.py: rounding is half-up."), ("user", "Now check the cache TTL.")]


USER = os.environ["LAB_USER_ID"]                       # the staging operator's own id: private, kept outside the repository
CWD = os.path.normpath(os.path.join(sys.argv[2], "..", "work", "cwd"))


TEMPLATE = json.load(open(os.environ["LAB_RECEIPT_TEMPLATE"]))       # captured from a staged gateway; contains the operator id, so it is never committed


def adopt(sid, model, request_id):
    """Give the row the gateway's local-session receipt and routing entries (what ``commit_local_session`` writes), cloned from one the staged
    gateway itself created, so the session resumes like any conversation the gateway made earlier."""
    old = TEMPLATE["sid"]
    swap = lambda text: text.replace(old, sid).replace(TEMPLATE["request_id"], request_id).replace('"model":"stub-model"', json.dumps({"model": model})[1:-1].replace(" ", ""))
    from hermes_state_local import POLICY_PREFIX

    def write(conn):
        conn.execute("INSERT OR REPLACE INTO state_meta(key, value) VALUES(?, ?)", (POLICY_PREFIX + sid, swap(TEMPLATE["receipt"])))
        for scope, route, entry, updated in TEMPLATE["routing"]:
            conn.execute("INSERT OR REPLACE INTO gateway_routing(scope, session_key, entry_json, updated_at) VALUES(?,?,?,?)", (scope, swap(route), swap(entry), updated))
    db._execute_write(write)


def make(name, title, model, provider, anchor=None, extra=0, summaries=0, usage=(), err=None, age_days=2):
    from hermes_state_keys import local_identity
    request_id = "00000000-0000-4000-8000-" + hashlib.sha256(name.encode()).hexdigest()[:12]
    sid = local_identity(sys.argv[2], USER, request_id)
    db.create_session(sid, "local", model=model)
    origin = json.dumps({"platform": "local", "chat_id": sid, "chat_name": None, "chat_name": None, "chat_type": "dm", "user_id": USER, "user_name": None, "thread_id": None, "chat_topic": None})
    db._write_sql("UPDATE sessions SET source='local', created_source='desktop', user_id=?, session_key=?, chat_id=?, chat_type='dm', origin_json=?, profile_name='default', cwd=?, runtime_revision=1, runtime_generation=1, billing_provider=? WHERE id=?",
                  (USER, "agent:main:local:dm:" + sid, sid, origin, CWD, provider, sid))
    adopt(sid, model, request_id)
    db.patch_session_model_config(sid, {"gateway_runtime": {"provider": provider, "base_url": "", "api_mode": "chat_completions", "fallback_active": False}})
    for r, c in M:
        db.append_message(sid, r, c)
    for i in range(extra):
        db.append_message(sid, "assistant" if i % 2 else "user", "follow-up %d " % i + "x" * 300)
    if anchor is not None:
        db.patch_session_model_config(sid, {"_usage_anchor": capture_usage_anchor(anchor, 46, db.get_messages_as_conversation(sid))})
    for i in range(summaries):
        db.append_message(sid, "user", "[CONTEXT SUMMARY %d] Objective: ship the billing fix. Current task: cache TTL. Project rule: no rounding-mode change without owner approval." % i, _compressed_summary=True)
    db.set_session_title(sid, title) if hasattr(db, "set_session_title") else None
    if err:
        db._write_sql("UPDATE sessions SET compression_failure_error = ?, compression_failure_cooldown_until = ? WHERE id = ?", (err, now + 900, sid))
    for (m, prov, mode, task, calls, tin, tout, cr, cw, est, act, status, source, first, last) in usage:
        db._write_sql("INSERT INTO session_model_usage (session_id, model, billing_provider, billing_base_url, billing_mode, task, api_call_count, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, estimated_cost_usd, actual_cost_usd, cost_status, cost_source, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (sid, m, prov, "", mode, task, calls, tin, tout, cr, cw, 0, est, act, status, source, now - first * 86400, now - last * 86400))
    db._write_sql("UPDATE sessions SET started_at = ? WHERE id = ?", (now - age_days * 86400, sid))


make("legacy-reported", "Legacy: provider reading", "gpt-4o", "openai", anchor=16586,
     usage=[("gpt-4o", "openai", "", "", 12, 90000, 9000, 40000, 0, 0.4821, 0.0, "estimated", "official_docs_snapshot", 3, 3)])
make("legacy-estimate", "Legacy: no stored reading", "gpt-4o", "openai", extra=6)
make("legacy-unknown-window", "Legacy: model window unknown", "mystery-model-x", "custom", anchor=5000)
make("legacy-compacted", "Legacy: compacted twice, cooldown", "gpt-4o", "openai", anchor=61000, summaries=2, err="summary model unavailable (HTTP 503)")
make("cost-subscription", "Claude subscription session", "claude-sonnet-5-5[1m]", "claude-subscription-directsdk-experimental", anchor=40000,
     usage=[("claude-sonnet-5-5[1m]", "claude-subscription-directsdk-experimental", "", "", 65, 550, 40054, 5110122, 0, 2.105, 0.0, "estimated", "provider_cost_api", 1, 0),
            ("claude-sonnet-5-5[1m]", "claude-subscription-directsdk-experimental", "", "title_generation", 1, 300, 12, 0, 0, 0.0, 0.0, None, None, 1, 0)])
make("cost-api", "DeepSeek API session", "deepseek-chat", "deepseek", anchor=12000,
     usage=[("deepseek-chat", "deepseek", "", "", 3, 12000, 400, 0, 0, 0.0008, 0.0, "estimated", "official_docs_snapshot", 5, 5),
            ("deepseek-reasoner", "deepseek", "", "", 1, 500, 20, 0, 0, 0.0001, 0.0002, "actual", "provider_generation_api", 25, 20)])
make("cost-free", "Longcat 2.5:Free session", "meituan/longcat-2.5:free", "nous", anchor=16586,
     usage=[("meituan/longcat-2.5:free", "nous", "", "", 13, 97563, 9738, 603008, 0, 0.0, 0.0, "unknown", "provider_models_api", 0, 0),
            ("gpt-6.1-sol", "openai-codex", "subscription_included", "", 2, 800, 40, 0, 0, 0.0, 0.0, "included", "none", 0, 0)])
print("seeded", [r["id"] for r in db._read_all("SELECT id FROM sessions")])
