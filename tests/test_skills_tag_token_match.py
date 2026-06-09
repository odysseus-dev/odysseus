"""Regression: skill retrieval must match tags as whole tokens, not substrings."""
import sys
from unittest.mock import MagicMock

# Stub heavy deps so importing the skills manager doesn't pull DB / FastAPI.
for _mod in ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.ext", "sqlalchemy.ext.declarative"):
    if _mod not in sys.modules:
        try:
            __import__(_mod)
        except ImportError:
            sys.modules[_mod] = MagicMock()

from services.memory.skills import SkillsManager  # noqa: E402


def _skill(name, description, tags):
    # status must be published/draft or get_relevant_skills filters the skill
    # out before the tag-scoring path runs.
    return {"name": name, "description": description, "when_to_use": "",
            "tags": tags, "procedure": [], "status": "published"}


def test_tag_substring_does_not_boost(tmp_path):
    sm = SkillsManager(str(tmp_path))
    skills = [_skill("ml-helper", "machine learning helper", ["ai"])]
    # "ai" appears only as a substring of "email", not as a whole token, so it
    # must not boost this unrelated skill into the results.
    out = sm.get_relevant_skills("send me an email about lunch tomorrow", skills=skills)
    assert out == []


def test_tag_whole_token_still_boosts(tmp_path):
    sm = SkillsManager(str(tmp_path))
    skills = [_skill("git-helper", "version control stuff", ["git"])]
    out = sm.get_relevant_skills("help me with git rebase", skills=skills)
    assert any(s["name"] == "git-helper" for s in out)
