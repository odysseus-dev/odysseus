from routes.chat_helpers import _skill_run_is_complex


def test_normal_inspect_edit_verify_run_is_not_auto_skill_candidate():
    assert not _skill_run_is_complex(4, 3)


def test_long_multi_tool_run_is_auto_skill_candidate():
    assert _skill_run_is_complex(3, 4)
    assert _skill_run_is_complex(5, 3)


def test_short_run_does_not_trigger_skill_extraction():
    assert not _skill_run_is_complex(2, 2)
