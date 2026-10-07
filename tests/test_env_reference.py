"""Guards for the generated ODYSSEUS_* configuration reference.

`website/configuration-reference.md` is produced by
`scripts/generate_env_reference.py`. The point of these tests is that adding a
new `ODYSSEUS_*` read without documenting it fails the suite: the current state -
most of the configuration surface undiscoverable - happened because nothing
objected.

The generator's detection is exercised against a synthetic source tree rather
than against the real one, so a test failure names a behavior rather than a
count that drifted. Only the two whole-tree tests touch the repository, and they
compare generator output to the committed page instead of asserting on source
text.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers.cli_loader import load_script

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "website" / "configuration-reference.md"


@pytest.fixture(scope="module")
def generator():
    return load_script("generate_env_reference.py")


@pytest.fixture(scope="module")
def built(generator):
    """The generator run once against the real tree; reused by the slow tests."""
    return generator.build()


def _write(root: Path, relative: str, body: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def fake_tree(tmp_path, generator, monkeypatch):
    """A miniature source tree covering every read pattern the generator claims."""
    monkeypatch.setattr(generator, "SOURCE_ROOTS", ("app.py", "src"))
    _write(tmp_path, "app.py", """
import os

DIRECT = os.environ.get("ODYSSEUS_DIRECT_GET", "on")
GETENV = os.getenv("ODYSSEUS_PLAIN_GETENV")
SUBSCRIPT = os.environ["ODYSSEUS_SUBSCRIPT"]
SPANNING = os.environ.get(
    "ODYSSEUS_SPANS_TWO_LINES", "spanned"
)
os.environ["ODYSSEUS_WRITE_ONLY"] = "1"
""")
    _write(tmp_path, "src/indirect.py", '''
import os

NAME_HELD_IN_CONSTANT = "ODYSSEUS_VIA_CONSTANT"
FALLBACK = 7

value = os.environ.get(NAME_HELD_IN_CONSTANT, FALLBACK)


def read_limit(name, default):
    """An env-reader helper: the generator should follow calls to this."""
    raw = os.getenv(name)
    return default if raw is None else int(raw)


LIMIT = read_limit("ODYSSEUS_VIA_HELPER", 5 * 1024)


def flag(environ=None):
    source = os.environ if environ is None else environ
    return source.get("ODYSSEUS_VIA_MAPPING_ARG", "1")


GENERATED = [
    "import os",
    "if os.environ.get('ODYSSEUS_INSIDE_A_STRING'): pass",
]
''')
    return tmp_path


def test_finds_every_read_pattern_it_claims_to(generator, fake_tree):
    found = generator.collect(fake_tree)

    assert set(found) == {
        "ODYSSEUS_DIRECT_GET",
        "ODYSSEUS_PLAIN_GETENV",
        "ODYSSEUS_SUBSCRIPT",
        "ODYSSEUS_SPANS_TWO_LINES",
        "ODYSSEUS_VIA_CONSTANT",
        "ODYSSEUS_VIA_HELPER",
        "ODYSSEUS_VIA_MAPPING_ARG",
        "ODYSSEUS_INSIDE_A_STRING",
    }


def test_a_plain_grep_would_miss_what_the_extra_passes_find(generator, fake_tree):
    """Pins why the generator is not a one-line grep."""
    naive = generator.naive_line_scan(fake_tree)
    found = set(generator.collect(fake_tree))

    assert found - naive == {
        "ODYSSEUS_SPANS_TWO_LINES",
        "ODYSSEUS_VIA_CONSTANT",
        "ODYSSEUS_VIA_HELPER",
        "ODYSSEUS_VIA_MAPPING_ARG",
    }
    # And it over-counts in the other direction: a line-based scan cannot tell a
    # write from a read, which is why the page reports the intersection.
    assert naive - found == {"ODYSSEUS_WRITE_ONLY"}


def test_records_defaults_and_locations_from_the_source(generator, fake_tree):
    found = generator.collect(fake_tree)

    assert found["ODYSSEUS_DIRECT_GET"].primary.default == "'on'"
    assert found["ODYSSEUS_SPANS_TWO_LINES"].primary.default == "'spanned'"
    # One level of indirection is resolved: the name and the default both come
    # from module-level constants.
    assert found["ODYSSEUS_VIA_CONSTANT"].primary.default == "7"
    assert found["ODYSSEUS_VIA_HELPER"].primary.default == "5 * 1024"
    assert found["ODYSSEUS_PLAIN_GETENV"].primary.default is None

    assert found["ODYSSEUS_DIRECT_GET"].primary.location == "app.py:4"
    assert found["ODYSSEUS_VIA_HELPER"].primary.path == "src/indirect.py"


def test_environ_writes_are_not_reads(generator, fake_tree):
    assert "ODYSSEUS_WRITE_ONLY" not in generator.collect(fake_tree)


def test_an_undocumented_variable_is_reported(generator, fake_tree):
    """The whole point: a new variable with no notes entry must fail loudly."""
    problems = generator.check_notes(generator.collect(fake_tree))

    assert problems
    offender = "ODYSSEUS_VIA_HELPER"
    assert any(offender in problem for problem in problems)
    assert any("VARIABLE_NOTES" in problem for problem in problems)
    assert any("src/indirect.py" in problem for problem in problems)


def test_a_stale_notes_entry_is_reported(generator):
    problems = generator.check_notes({})

    assert len(problems) == len(generator.VARIABLE_NOTES)
    assert all("no longer read anywhere" in problem for problem in problems)


def test_renders_one_table_row_per_variable(generator, fake_tree, monkeypatch):
    monkeypatch.setitem(
        generator.VARIABLE_NOTES,
        "ODYSSEUS_VIA_HELPER",
        ("Search", generator.USER, "A synthetic limit."),
    )
    found = {"ODYSSEUS_VIA_HELPER": generator.collect(fake_tree)["ODYSSEUS_VIA_HELPER"]}

    page = generator.render(found, generator.naive_line_scan(fake_tree))

    assert page.startswith("---\nlayout: default\n---\n")
    assert "| `ODYSSEUS_VIA_HELPER` | `5 * 1024` | `src/indirect.py` | A synthetic limit. |" in page
    assert "### Search" in page


def _render_one(generator, root, name, monkeypatch):
    monkeypatch.setitem(
        generator.VARIABLE_NOTES, name, ("Search", generator.USER, "A synthetic flag.")
    )
    return generator.render(
        {name: generator.collect(root)[name]}, generator.naive_line_scan(root)
    )


def test_page_does_not_change_when_an_unrelated_edit_shifts_lines(
    generator, fake_tree, monkeypatch
):
    """An edit that moves a read down a file must not make the page stale.

    The page used to cite `path:line`, so any PR adding a line above a read had
    to regenerate it and then conflicted with every other PR that had.
    """
    before = _render_one(generator, fake_tree, "ODYSSEUS_VIA_HELPER", monkeypatch)
    source = fake_tree / "src" / "indirect.py"
    source.write_text("import sys\n\n\n" + source.read_text(encoding="utf-8"), encoding="utf-8")

    after = _render_one(generator, fake_tree, "ODYSSEUS_VIA_HELPER", monkeypatch)

    assert generator.collect(fake_tree)["ODYSSEUS_VIA_HELPER"].primary.lineno == 19
    assert after == before


def test_extra_reads_are_counted_per_file(generator, tmp_path, monkeypatch):
    monkeypatch.setattr(generator, "SOURCE_ROOTS", ("app.py", "src"))
    _write(tmp_path, "app.py", """
import os

FIRST = os.environ.get("ODYSSEUS_READ_OFTEN", "1")
SECOND = os.environ.get("ODYSSEUS_READ_OFTEN", "1")
""")
    _write(tmp_path, "src/elsewhere.py", """
import os

AGAIN = os.getenv("ODYSSEUS_READ_OFTEN")
""")

    page = _render_one(generator, tmp_path, "ODYSSEUS_READ_OFTEN", monkeypatch)

    assert "| `app.py` (+1 more) |" in page


def test_every_variable_read_in_the_repository_is_documented(built):
    _, variables, problems = built

    assert not problems, "\n".join(problems)
    assert variables, "expected the generator to find ODYSSEUS_* reads"


def test_committed_page_matches_the_source(built):
    page, _, _ = built

    assert PAGE.read_text(encoding="utf-8") == page, (
        "website/configuration-reference.md is stale - regenerate it with "
        "`python3 scripts/generate_env_reference.py`"
    )


def test_page_has_no_emoji_or_other_non_ascii(built):
    page, _, _ = built

    offenders = sorted({character for character in page if ord(character) > 126})
    assert not offenders, f"non-ASCII in the generated page: {offenders}"


def test_check_mode_reports_a_stale_page(generator, tmp_path, monkeypatch):
    stale = tmp_path / "configuration-reference.md"
    stale.write_text("out of date\n", encoding="utf-8")
    monkeypatch.setattr(generator, "OUTPUT_PATH", stale)

    assert generator.main(["--check"]) == 1
    assert generator.main([]) == 0
    assert generator.main(["--check"]) == 0


def test_script_runs_as_a_subprocess_without_importing_the_app():
    result = subprocess.run(
        [sys.executable, "scripts/generate_env_reference.py", "--check"],
        cwd=REPO, capture_output=True, text=True, timeout=180,
    )

    assert result.returncode == 0, result.stderr
    assert re.search(r"\d+ variables", result.stdout), result.stdout


def test_page_is_linked_from_the_places_a_reader_starts():
    assert "configuration-reference.md" in (REPO / "website" / "setup.md").read_text(
        encoding="utf-8"
    )
    assert "configuration-reference.md" in (REPO / ".env.example").read_text(
        encoding="utf-8"
    )
