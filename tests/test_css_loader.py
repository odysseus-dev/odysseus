from pathlib import Path

import pytest

from tests.helpers.css_loader import read_css_with_imports


ROOT = Path(__file__).resolve().parents[1]


def test_read_css_with_imports_expands_nested_local_files_in_order(tmp_path: Path):
    entry = tmp_path / "style.css"
    first = tmp_path / "first.css"
    nested = tmp_path / "nested.css"
    second = tmp_path / "second.css"

    entry.write_text(
        'entry-before\n@import "./first.css";\nentry-middle\n'
        '@import url("./second.css");\n'
        '@import "https://example.com/remote.css";\nentry-after\n',
        encoding="utf-8",
    )
    first.write_text(
        'first-before\n@import url(./nested.css);\nfirst-after\n',
        encoding="utf-8",
    )
    nested.write_text("nested\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")

    combined = read_css_with_imports(entry)

    ordered_markers = [
        "entry-before",
        "first-before",
        "nested",
        "first-after",
        "entry-middle",
        "second",
        "https://example.com/remote.css",
        "entry-after",
    ]
    positions = [combined.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions)


def test_read_css_with_imports_rejects_circular_local_imports(tmp_path: Path):
    first = tmp_path / "first.css"
    second = tmp_path / "second.css"
    first.write_text('@import "./second.css";', encoding="utf-8")
    second.write_text('@import "./first.css";', encoding="utf-8")

    with pytest.raises(ValueError, match="Circular CSS @import"):
        read_css_with_imports(first)


def test_service_worker_precaches_css_imports_and_eager_cursor_effect():
    entrypoint = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    service_worker = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
    import_targets = [
        line.split('"', 2)[1]
        for line in entrypoint.splitlines()
        if line.lstrip().startswith("@import ")
    ]

    assert import_targets
    for target in import_targets:
        url = f"/static/{target.removeprefix('./')}"
        assert f"'{url}'" in service_worker
    assert "'/static/js/effects/cursorTrail.js'" in service_worker
