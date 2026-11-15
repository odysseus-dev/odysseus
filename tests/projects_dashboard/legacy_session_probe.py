"""The legacy-session probe: a stored conversation with a persisted provider reading and NO live agent, asked through the gateway's own
``snapshot_usage``. argv[1] = Hermes source (baseline or patched), argv[2] = scratch HERMES_HOME."""
import json, os, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1]); os.environ["HERMES_HOME"] = sys.argv[2]
from unittest import mock
from hermes_state import SessionDB
from agent.usage_anchor import capture_usage_anchor
from agent import model_metadata as mm
from gateway import session_usage as su
db = SessionDB(Path(sys.argv[2]) / ("legacy_" + str(os.getpid())) / "state.db")
db.create_session("old", "local", model="meituan/longcat-2.5:free")
for r, c in (("user", "hello"), ("assistant", "hi"), ("user", "continue")):
    db.append_message("old", r, c)
db.patch_session_model_config("old", {"_usage_anchor": capture_usage_anchor(16_586, 46, db.get_messages_as_conversation("old"))})
authority = SimpleNamespace(db=db, sessions={}, agent=lambda ref: None)
with mock.patch.object(mm, "get_model_context_length", lambda *a, **k: 1_000_000):
    usage = su.snapshot_usage(authority, SimpleNamespace(session_id="old"), None)
print("@@" + json.dumps({k: usage.get(k) for k in ("context_used", "context_max", "context_percent", "context_source", "context_estimated", "input", "output")}))
