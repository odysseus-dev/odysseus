"""Hero/section image selection must not drop photos whose slug contains
'icon' or 'logo' as a substring.

generate_visual_report filtered images with `"/icon" not in url` etc., a
plain substring test that wrongly dropped legitimate photos like
/iconic-moment-2026.jpg and /logos-history-explained.png while intending
to drop only icon/logo/favicon ASSETS. The boundary-aware
_is_icon_or_logo_url helper fixes that.
"""
from src.visual_report import _is_icon_or_logo_url


def test_real_photos_with_icon_or_logo_in_slug_are_kept():
    assert _is_icon_or_logo_url("https://news.com/iconic-moment-2026.jpg") is False
    assert _is_icon_or_logo_url("https://news.com/logos-history-explained.png") is False
    assert _is_icon_or_logo_url("https://x.com/the-iconography-of-art.jpg") is False


def test_actual_icon_and_logo_assets_are_still_flagged():
    assert _is_icon_or_logo_url("https://x.com/icon.png") is True
    assert _is_icon_or_logo_url("https://x.com/logo.svg") is True
    assert _is_icon_or_logo_url("https://x.com/favicon.ico") is True
    assert _is_icon_or_logo_url("https://x.com/assets/icon/main.png") is True
    assert _is_icon_or_logo_url("https://x.com/logo-dark.png") is True


def test_empty_and_none_are_not_flagged():
    assert _is_icon_or_logo_url("") is False
    assert _is_icon_or_logo_url(None) is False
