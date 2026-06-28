from scripts.import_from_vllm_recipes import _parse_param_count


def test_parse_param_count_ignores_bad_decimal_tokens():
    assert _parse_param_count("1..2B") == 0
    assert _parse_param_count("8.6B") == 8_600_000_000
