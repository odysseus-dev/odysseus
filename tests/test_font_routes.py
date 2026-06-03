from routes.font_routes import _derive_family


def test_derive_family_keeps_jetbrains_together():
    assert _derive_family("JetBrainsMono-Regular.woff2") == "JetBrains Mono"


def test_derive_family_splits_common_family_suffixes():
    assert _derive_family("FiraCode-SemiBold.ttf") == "Fira Code"
    assert _derive_family("NotoSans-Bold.otf") == "Noto Sans"
    assert _derive_family("RobotoSlab-Bold.woff2") == "Roboto Slab"


def test_derive_family_strips_compound_weight_italic():
    # Compound weight+style faces (BoldItalic, MediumItalic, ...) must group
    # with their base family, not spawn a phantom "<Family> Bold" family.
    assert _derive_family("JetBrainsMono-BoldItalic.woff2") == "JetBrains Mono"
    assert _derive_family("Inter-MediumItalic.ttf") == "Inter"
    assert _derive_family("RobotoSlab-LightItalic.woff2") == "Roboto Slab"
