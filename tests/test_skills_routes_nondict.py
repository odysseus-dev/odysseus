"""Regression: skill helpers must tolerate a non-dict skill.

_skill_test_task did `skill.get(...)` and _should_check_retrieval_precision did
`skill.get("tags")`; a skill row that loaded as a bare string/None raised
AttributeError. They now treat a non-dict as empty / not-applicable.
"""
from routes.skills_routes import _skill_test_task, _should_check_retrieval_precision


def test_non_dict_skill_does_not_crash():
    assert isinstance(_skill_test_task("not a dict"), str)
    assert isinstance(_skill_test_task(None), str)
    assert _should_check_retrieval_precision("x") is False
    assert _should_check_retrieval_precision(None) is False
