"""manage_skills `publish` parses an LLM-supplied `confidence` with float(); a
non-numeric value (e.g. "high" or a list) must return a clean error, not raise an
uncaught TypeError/ValueError that crashes the agent turn.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="odysseus-skills-conf-"))
os.environ.setdefault("DATA_DIR", str(_TMP))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP / 'app.db'}")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_manage_skills_publish_rejects_non_numeric_confidence(monkeypatch):
    import services.memory.skills as skills_mod
    from src.tool_implementations import do_manage_skills

    # make the target skill "exist" so execution reaches the confidence parse
    monkeypatch.setattr(skills_mod.SkillsManager, "load",
                        lambda self, owner=None: [{"name": "x"}])
    calls = {"update": 0}
    monkeypatch.setattr(
        skills_mod.SkillsManager, "update_skill",
        lambda self, name, updates, owner=None: calls.__setitem__("update", calls["update"] + 1) or True,
    )

    res = asyncio.run(
        do_manage_skills('{"action":"publish","name":"x","confidence":"high"}', owner="alice")
    )

    assert res.get("exit_code") == 1, res
    assert "confidence" in (res.get("error") or "").lower(), res
    assert calls["update"] == 0, "must not update the skill on invalid confidence"
