"""Behavioral regression coverage for dropdown carets in the shipped CSS."""

import os

from tests.helpers.cli_loader import load_script


snapshot = load_script("css_snapshot.py")

SELECTORS = {
    "settings": ".admin-card select.settings-select",
    "sort": ".admin-card select.memory-sort-select",
    "number": ".admin-card input.settings-select[type=number]",
}

VARIANTS = {
    "desktop-dark-comfortable",
    "desktop-light-comfortable",
    "phone-dark-comfortable",
    "phone-light-comfortable",
}


def test_dropdown_carets_survive_shipped_stylesheet_cascade():
    assert snapshot.playwright_available(), (
        "Node Playwright is required for rendered CSS validation"
    )

    original = snapshot.load_inventory()

    inventory = {
        "properties": [
            "background-image",
            "background-repeat",
            "background-position",
            "background-size",
            "padding-right",
        ],
        "variants": [
            variant
            for variant in original["variants"]
            if variant["name"] in VARIANTS
        ],
        "pages": [{
            "name": "dropdown-caret-regression",
            "url": "/tests/css_snapshot/bench.html",
            "stylesheetsFrom": "/static/index.html",
            "bench": list(SELECTORS.values()),
        }],
    }

    assert len(inventory["variants"]) == len(VARIANTS)

    captured = snapshot.capture(
        os.environ["ODYSSEUS_TEST_STATIC_ORIGIN"],
        inventory=inventory,
    )

    assert captured["missing"] == {}

    variants = captured["snapshot"]["dropdown-caret-regression"]

    for variant_name, measured in variants.items():
        settings = measured[SELECTORS["settings"]]
        sort = measured[SELECTORS["sort"]]
        number = measured[SELECTORS["number"]]

        for label, styles in (
            ("Settings", settings),
            ("Skills/Cookbook sort", sort),
        ):
            assert "data:image/svg+xml" in styles["background-image"], (
                f"{variant_name}: {label} caret missing"
            )
            assert styles["background-repeat"] == "no-repeat", (
                f"{variant_name}: {label} caret repeats"
            )

        assert float(settings["padding-right"].removesuffix("px")) >= 24, (
            f"{variant_name}: insufficient Settings caret padding"
        )

        assert number["background-image"] == "none", (
            f"{variant_name}: number input unexpectedly received a caret"
        )

        assert (
            float(settings["padding-right"].removesuffix("px"))
            > float(number["padding-right"].removesuffix("px"))
        ), f"{variant_name}: select-only padding not preserved"
