"""Session image context and wizard validation."""

from titan.image_wizard import apply_image_defaults, validate_tool_params, wizard_message
from titan.session_image_context import SEED_MAX, format_context_for_llm, validate_seed_value


def test_format_context_no_prior():
    text = format_context_for_llm({"has_prior_image": False})
    assert "No prior generated image" in text
    assert "last_seed" not in text or "No prior" in text


def test_format_context_with_prior():
    text = format_context_for_llm({
        "has_prior_image": True,
        "last_gallery_id": "gid-1",
        "last_seed": 165861460,
        "last_style": "realistic",
        "last_prompt": "1girl, test",
    })
    assert "last_seed: 165861460" in text
    assert "last_gallery_id: gid-1" in text
    assert "interpret the user's message" in text


def test_validate_seed_rejects_overflow():
    err = validate_seed_value(SEED_MAX + 1)
    assert err is not None
    assert "out of allowed range" in err


def test_validate_tool_params_seed():
    msg = validate_tool_params(seed=9999999999999, n=1, raw_args={})
    assert msg is not None
    assert "NEEDS_USER_INPUT" in msg


def test_apply_image_defaults_respects_explicit_n():
    args = apply_image_defaults({"n": 3}, {"n": 3})
    assert args["n"] == 3


def test_apply_image_defaults_missing_n():
    args = apply_image_defaults({}, {})
    assert args["n"] == 1


def test_wizard_message_includes_context():
    out = wizard_message(
        "Confirm parameters.",
        ctx={"has_prior_image": True, "last_gallery_id": "g1", "last_seed": 42},
    )
    assert "NEEDS_USER_INPUT" in out
    assert "last_seed: 42" in out


def test_check_non_default_params_batch_requires_confirm():
    from titan.image_followup import check_non_default_params

    msg = check_non_default_params({"n": 3}, {"n": 3}, confirm=False)
    assert msg is not None
    assert "n=3" in msg
    assert "NEEDS_USER_INPUT" in msg
    assert check_non_default_params({"n": 3}, {"n": 3}, confirm=True) is None
    assert check_non_default_params({"n": 1}, {"n": 1}, confirm=False) is None
    assert check_non_default_params({"n": 1}, {}, confirm=False) is None
