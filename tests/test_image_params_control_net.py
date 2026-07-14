"""ControlNet flag inferred from user chat text."""

from titan.image_params import infer_control_net_from_text, normalize_image_args


def test_infer_control_net_on_czech():
    assert infer_control_net_from_text("vygeneruj obrázek s control netem") is True
    assert infer_control_net_from_text("use controlnet for this") is True


def test_infer_control_net_off():
    assert infer_control_net_from_text("bez control netu prosím") is False
    assert infer_control_net_from_text("without controlnet") is False


def test_infer_control_net_unmentioned():
    assert infer_control_net_from_text("vygeneruj anime portrét") is None


def test_normalize_image_args_applies_control_net_from_source_text():
    out = normalize_image_args({"prompt": "1girl, forest"}, source_text="s control netem")
    assert out.get("control_net") is True


def test_normalize_image_args_explicit_control_net_wins():
    out = normalize_image_args(
        {"prompt": "scene", "control_net": False},
        source_text="s control netem",
    )
    assert out.get("control_net") is False
