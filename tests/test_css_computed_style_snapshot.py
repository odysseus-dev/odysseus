"""Computed-style snapshot regression for the shipped app CSS cascade.

The stylesheet is an ordered multi-file cascade whose result depends on source
order, so a move that "looks fine" can still change which declaration wins. These
tests capture ``getComputedStyle`` over a fixed element inventory across pages,
viewports, themes and density modes, and compare the hash against
``tests/css_snapshot/baseline.json``.

Run ``python scripts/css_snapshot.py --write-baseline`` to re-record the
baseline, and only after confirming the change is intended - see
``tests/css_snapshot/README.md``.
"""
import os

import pytest

from tests.helpers.cli_loader import load_script

snapshot = load_script("css_snapshot.py")

_HAS_BROWSER = snapshot.playwright_available()
_requires_browser = pytest.mark.skipif(
    not _HAS_BROWSER,
    reason="node with the playwright package is required (npm ci)",
)

def static_origin() -> str:
    """Origin of the session static server, read at call time.

    The session fixture binds an ephemeral port and publishes it through the
    environment, which happens after this module is imported at collection.
    Reading it into a module constant therefore captured the fallback and the
    capture then connected to a port nothing was listening on. The literal
    fallback is the fixed port the fixture used before it moved to an ephemeral
    one, so this still works on a revision that predates that change.
    """
    return os.environ.get("ODYSSEUS_TEST_STATIC_ORIGIN", "http://127.0.0.1:7011")

# One conflicting selector used to prove the harness is actually sensitive to
# source order. `.attach-strip` is declared three times at the top level of
# cascade with different margin, min-height and padding, so swapping the
# first two changes which declaration wins without changing a single byte of
# any individual rule.
CONFLICTING_SELECTOR = ".attach-strip"


def test_baseline_covers_every_inventory_entry():
    """The committed baseline and the inventory describe the same surface.

    Cheap and browserless: it catches an inventory entry added without
    re-recording the baseline, which would otherwise look like a pass because
    nothing compares an absent key.
    """
    inventory = snapshot.load_inventory()
    baseline = snapshot.load_baseline()

    variant_names = {variant["name"] for variant in inventory["variants"]}
    for page in inventory["pages"]:
        name = page["name"]
        assert name in baseline["elements"], f"{name} missing from the baseline"
        expected_keys = {entry["key"] for entry in page.get("elements", [])}
        expected_keys |= set(page.get("bench", []))
        assert set(baseline["elements"][name]) == expected_keys, (
            f"{name}: baseline elements differ from the inventory; "
            "re-record with scripts/css_snapshot.py --write-baseline"
        )
        assert set(baseline["variants"][name]) == variant_names


@_requires_browser
def test_capture_does_not_execute_script_markup_or_handlers(tmp_path):
    """Use the real capture subprocess: every script vector would turn green."""
    attack = "document.getElementById('guard').style.color='rgb(0, 128, 0)'"
    source = f'''<!doctype html><html><head><style>#guard {{ color: rgb(255, 0, 0); }}</style></head>
      <body onload="{attack}"><div id="guard">Stable</div>
      <script>{attack}</script >
      <SCRIPT>{attack}</SCRIPT>
      <script data-note=">">{attack}</script\t>
      <script src="attack.js"></script>
      <svg onload="{attack}"><script>{attack}</script></svg>
      <img src=x onerror="{attack}">
      <template><script>{attack}</script></template>
      </body></html>'''
    (tmp_path / "fixture.html").write_text(source)
    (tmp_path / "attack.js").write_text(attack)
    inventory = {
        "properties": ["color"],
        "variants": [snapshot.load_inventory()["variants"][0]],
        "pages": [{"name": "fixture", "url": "/fixture.html",
                   "elements": [{"key": "guard", "selector": "#guard"}]}],
    }
    origin, shutdown = snapshot.serve_repository(tmp_path)
    try:
        captured = snapshot.capture(origin, inventory, measurement_delay_ms=100)
        assert captured["missing"] == {}
        assert captured["snapshot"]["fixture"][inventory["variants"][0]["name"]]["guard"]["color"] == "rgb(255, 0, 0)"
    finally:
        shutdown()


@_requires_browser
def test_computed_styles_match_the_committed_baseline():
    captured = snapshot.capture(static_origin())

    assert captured["missing"] == {}, (
        "inventory entries matched no element - the markup moved under the "
        f"harness: {captured['missing']}"
    )

    summary = snapshot.summarize(captured["snapshot"])
    drift = snapshot.compare(snapshot.load_baseline(), summary)
    assert not drift["elements"] and not drift["variants"] and not drift["digest_changed"], (
        "computed styles moved against tests/css_snapshot/baseline.json.\n"
        f"elements: {drift['elements']}\n"
        f"variants: {drift['variants']}\n"
        "If the change is intended, re-record with "
        "`python scripts/css_snapshot.py --write-baseline`; if it is not, the "
        "restructuring changed which declaration wins."
    )


@_requires_browser
def test_capture_is_independent_of_elapsed_time_and_font_metrics(tmp_path):
    page = tmp_path / "fixture.html"
    source = """<!doctype html><html><head><style>
      @keyframes fade { from { opacity: .25; } to { opacity: .75; } }
      .sample { animation: fade .4s linear infinite; transition: color .1s;
                font: 13px monospace; max-height: 30lh; }
      #other { font: 23px serif; max-height: 31lh; }
      textarea { outline-offset: 0; }
      textarea:focus { outline-offset: 7px; }
      #alias { font-family: Inter, -apple-system, BlinkMacSystemFont, sans-serif; }
      #serialized { font-family: Inter, -apple-system, "system-ui", sans-serif; }
    </style></head><body>
      <textarea id="focus" autofocus></textarea><div class="sample" id="sample"></div>
      <div class="sample" id="other"></div><div id="alias"></div><div id="serialized"></div>
    </body></html>"""
    page.write_text(source, encoding="utf-8")
    inventory = {
        "properties": ["font-family", "opacity", "max-height", "outline-offset",
                       "animation-name", "animation-duration", "animation-play-state",
                       "transition-duration"],
        "variants": [snapshot.load_inventory()["variants"][0]],
        "pages": [{"name": "fixture", "url": "/fixture.html", "elements": [
            {"key": key, "selector": f"#{key}",
             "lineRelativeProperties": ["max-height"] if key in {"sample", "other"} else []}
            for key in ("focus", "sample", "other", "alias", "serialized")
        ]}],
    }
    origin, shutdown = snapshot.serve_repository(tmp_path)
    try:
        early = snapshot.capture(origin, inventory)["snapshot"]
        late = snapshot.capture(origin, inventory, measurement_delay_ms=150)["snapshot"]
        assert early == late
        values = early["fixture"][inventory["variants"][0]["name"]]
        assert values["focus"]["outline-offset"] == "0px"
        assert values["sample"]["opacity"] == "0.25"
        assert values["sample"]["animation-name"] == "fade"
        assert values["sample"]["animation-duration"] == "0.4s"
        assert values["sample"]["animation-play-state"] == "running"
        assert values["sample"]["transition-duration"] == "0.1s"
        assert values["sample"]["max-height"] == "30lh"
        assert values["other"]["max-height"] == "31lh"
        assert values["alias"]["font-family"] == values["serialized"]["font-family"]
        page.write_text(source.replace("opacity: .25", "opacity: .5"), encoding="utf-8")
        changed = snapshot.capture(origin, inventory)["snapshot"]
        assert changed["fixture"][inventory["variants"][0]["name"]]["sample"]["opacity"] == "0.5"
        assert snapshot.summarize(changed)["digest"] != snapshot.summarize(early)["digest"]
    finally:
        shutdown()


@_requires_browser
def test_reordering_two_conflicting_declarations_moves_the_digest():
    """The harness has to fail when the cascade changes, or it proves nothing.

    Captures one variant twice - once normally, once with the first two
    top-level `.attach-strip` blocks swapped - and asserts the digest moves and
    points at the affected element.
    """
    variants = ["desktop-dark-comfortable"]
    unchanged = snapshot.summarize(
        snapshot.capture(static_origin(), variants=variants)["snapshot"]
    )
    reordered = snapshot.summarize(
        snapshot.capture(static_origin(), variants=variants,
                         swap_rule=CONFLICTING_SELECTOR)["snapshot"]
    )

    assert unchanged["digest"] != reordered["digest"]
    drift = snapshot.compare(unchanged, reordered)
    assert "app-shell/attach-strip" in drift["elements"]
    assert "bench/.attach-strip" in drift["elements"]
