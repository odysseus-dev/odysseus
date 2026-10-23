"""Regression coverage for Cookbook download progress badge rendering."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def _run_node(script: str) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed:\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_progress_percent_parses_and_clamps(node_available):
    script = textwrap.dedent("""
        const { downloadProgressPercent } = await import('./static/js/cookbookDownloadProgress.js');
        console.log(JSON.stringify({
          missing: downloadProgressPercent('downloading'),
          ordinary: downloadProgressPercent('73% · 1.81GB/s'),
          decimal: downloadProgressPercent('12.5%'),
          upper: downloadProgressPercent('135%'),
        }));
    """)
    assert _run_node(script) == {
        "missing": 0,
        "ordinary": 73,
        "decimal": 12.5,
        "upper": 100,
    }


def test_badge_keeps_download_class_and_sets_fill(node_available):
    script = textwrap.dedent("""
        const { setDownloadProgressBadge } = await import('./static/js/cookbookDownloadProgress.js');
        const properties = {};
        const badge = { textContent: '', className: '', style: { setProperty: (key, value) => { properties[key] = value; } } };
        setDownloadProgressBadge(badge, '73% · 1.81GB/s');
        console.log(JSON.stringify({ text: badge.textContent, cls: badge.className, properties }));
    """)
    assert _run_node(script) == {
        "text": "73% · 1.81GB/s",
        "cls": "cookbook-task-status cookbook-task-downloading",
        "properties": {"--download-progress": "73%"},
    }


def test_running_download_status_reserves_stable_width():
    css = (_REPO / "static/css/10-cookbook.css").read_text(encoding="utf-8")
    selector = '.cookbook-task[data-type="download"][data-status="running"] .cookbook-task-status'
    rule = css.split(selector, 1)[1].split("}", 1)[0]
    assert "width: 120px" in rule
    assert "white-space: nowrap" in rule
    assert "overflow: hidden" in rule
    assert "width: var(--download-progress, 0%)" in css
    assert 'width: 80px' in css.split('@media (max-width: 768px)', 2)[-1]
