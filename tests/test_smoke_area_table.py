"""The release smoke suite's report cannot overstate what it checked.

The suite's value is entirely in whether its table is honest, and the
table is built from a registry rather than from what happened to run.
These pin the properties that make it honest: every advertised area has
a row whether or not its module ran, every module that exists is
registered, a failure outranks a pass, and the whole thing stays ASCII.
"""
from pathlib import Path

import pytest

from tests.smoke import areas

SMOKE_DIR = Path(__file__).parent / "smoke"


def test_every_registered_area_has_its_module_on_disk():
    missing = [area.module for area in areas.COVERED
               if not (SMOKE_DIR / area.module).exists()]
    assert not missing, f"registered areas with no test module: {missing}"


def test_every_smoke_module_is_registered():
    """A module nobody registered would run and never appear in the table."""
    on_disk = {path.name for path in SMOKE_DIR.glob("test_*_smoke.py")}
    registered = {area.module for area in areas.COVERED}
    assert on_disk == registered, (
        f"unregistered modules: {sorted(on_disk - registered)}; "
        f"registered but absent: {sorted(registered - on_disk)}"
    )


def test_area_keys_are_unique():
    keys = [area.key for area in areas.COVERED]
    assert len(keys) == len(set(keys)), keys


def test_a_run_that_reported_nothing_shows_every_area_as_not_run():
    table = areas.render_table({})
    for area in areas.COVERED:
        assert area.label in table, area.label
    assert table.count(areas.NOT_RUN) == len(areas.COVERED)
    assert areas.PASS not in table


def test_an_unreported_area_is_not_dropped_from_the_table():
    """The registry decides the rows, so a partial run still lists the rest."""
    table = areas.render_table({areas.COVERED[0].key: {"result": areas.PASS, "checks": 1}})
    assert table.count(areas.NOT_RUN) == len(areas.COVERED) - 1
    assert areas.COVERED[-1].label in table


def test_declared_gaps_are_printed_with_their_reason():
    assert areas.DECLARED_GAPS, "a suite with no declared gaps is claiming total coverage"
    table = areas.render_table({})
    for gap in areas.DECLARED_GAPS:
        assert gap.label in table, gap.label
        # The reason is wrapped across lines, so match its first words.
        assert " ".join(gap.reason.split()[:3]) in " ".join(table.split()), gap.reason


@pytest.mark.parametrize("outcomes,expected", [
    ([], areas.NOT_RUN),
    ([areas.PASS], areas.PASS),
    ([areas.PASS, areas.SKIP], areas.SKIP),
    ([areas.PASS, areas.SKIP, areas.FAIL], areas.FAIL),
    ([areas.PASS, areas.FAIL], areas.FAIL),
])
def test_the_worst_outcome_decides_the_row(outcomes, expected):
    assert areas.resolve(outcomes) == expected


def test_the_table_is_ascii_only():
    """No emoji or status glyphs: the repo bans them in UI and in code."""
    table = areas.render_table({area.key: {"result": areas.PASS, "checks": 1}
                                for area in areas.COVERED})
    assert table.isascii(), [ch for ch in table if not ch.isascii()]


def test_module_names_map_back_to_their_area():
    for area in areas.COVERED:
        assert areas.area_for_module(area.module) == area.key
    assert areas.area_for_module("test_not_a_smoke_module.py") is None


def test_the_summary_line_counts_every_row():
    results = {area.key: {"result": areas.PASS, "checks": 1} for area in areas.COVERED}
    results[areas.COVERED[0].key] = {"result": areas.FAIL, "checks": 0}
    results[areas.COVERED[1].key] = {"result": areas.SKIP, "checks": 0}
    summary = areas.render_table(results).splitlines()[-1]
    assert f"{len(areas.COVERED) - 2} pass" in summary, summary
    assert "1 fail" in summary, summary
    assert "1 skip" in summary, summary
    assert "0 not run" in summary, summary
    assert f"{len(areas.DECLARED_GAPS)} declared gaps" in summary, summary
