import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.font_routes as font_routes
from routes.font_routes import _derive_family


def test_derive_family_keeps_jetbrains_together():
    assert _derive_family("JetBrainsMono-Regular.woff2") == "JetBrains Mono"


def test_derive_family_splits_common_family_suffixes():
    assert _derive_family("FiraCode-SemiBold.ttf") == "Fira Code"
    assert _derive_family("NotoSans-Bold.otf") == "Noto Sans"
    assert _derive_family("RobotoSlab-Bold.woff2") == "Roboto Slab"


@pytest.mark.parametrize("filename,expected", [
    ("JetBrainsMono-SemiBoldItalic.woff2", "JetBrains Mono"),
    ("FiraCode-BoldItalic.ttf", "Fira Code"),
    ("NotoSans-ExtraLightItalic.otf", "Noto Sans"),
    ("RobotoSlab-UltraBoldOblique.WOFF2", "Roboto Slab"),
    ("SourceSansPro-Bold-Italic.woff", "Source Sans Pro"),
    ("FiraCode-SemiBold_Italic.ttf", "Fira Code"),
    ("FiraCode-Italic.woff2", "Fira Code"),
    ("FiraCode-VF.woff2", "Fira Code"),
    ("MyFont-Book.woff2", "My Font Book"),
])
def test_compound_weight_and_style_are_not_part_of_the_family(filename, expected):
    assert _derive_family(filename) == expected


def test_custom_font_endpoint_groups_all_styles_under_one_family(tmp_path, monkeypatch):
    files = ["JetBrainsMono-Regular.woff2", "JetBrainsMono-SemiBoldItalic.woff2",
             "JetBrainsMono-Bold-Oblique.ttf"]
    for name in [*files, "OtherFont-Regular.otf", "README.md"]:
        (tmp_path / name).write_bytes(b"filename-only discovery fixture")
    monkeypatch.setattr(font_routes, "CUSTOM_FONTS_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(font_routes.setup_font_routes())
    with TestClient(app) as client:
        response = client.get("/api/fonts/custom")
    assert response.status_code == 200
    fonts = response.json()["fonts"]
    assert set(fonts) == {"JetBrains Mono", "Other Font"}
    assert fonts["JetBrains Mono"] == [
        {"file": name, "url": f"/static/fonts/custom/{name}", "format": name.rsplit(".", 1)[1]}
        for name in sorted(files)
    ]
