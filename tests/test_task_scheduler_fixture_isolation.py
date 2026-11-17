from src.task_scheduler import _is_sft_fixture_owner


def test_sft_accounts_are_background_scheduler_fixtures():
    assert _is_sft_fixture_owner("sft_alex_creator")
    assert _is_sft_fixture_owner("SFT_MAYA_OPS")


def test_real_and_ownerless_accounts_remain_scheduler_eligible():
    assert not _is_sft_fixture_owner("pewds")
    assert not _is_sft_fixture_owner(None)
