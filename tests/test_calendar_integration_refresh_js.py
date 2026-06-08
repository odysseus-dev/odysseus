"""Test calendar integrations refresh logic."""

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


def test_calendar_integration_refresh_clears_cache_and_syncs(node_available):
    script = textwrap.dedent(
        r"""
        import fs from 'node:fs';
        
        let source = fs.readFileSync('./static/js/calendar.js', 'utf8');
        const match = source.match(/^async function _refreshAfterIntegrationsChanged\(\) \{([\s\S]*?)\n\}/m);
        if (!match) throw new Error('Could not find _refreshAfterIntegrationsChanged in calendar.js');
        const functionCode = 'globalThis._refreshAfterIntegrationsChanged = async function _refreshAfterIntegrationsChanged() {' + match[1] + '\n}';

        const state = {
          syncCalled: false,
          fetchCalsCalled: false,
          fetchEventsCalled: false,
          renderCalled: false,
          badgeCalled: false,
        };

        globalThis.LS_KEY = 'odysseus-calendar-cache';
        globalThis._caldavSyncedOnce = true;
        globalThis._allEvents = { '2026-06-01': [] };
        globalThis._fetchedRanges = ['2026-06-01_2026-07-01'];
        globalThis.localStorage = {
          removed: null,
          removeItem(k) { this.removed = k; },
          getItem() { return null; },
          setItem() {}
        };
        globalThis._syncCaldav = async (val) => { state.syncCalled = val === false; };
        globalThis._fetchCalendars = async () => { state.fetchCalsCalled = true; };
        globalThis._view = 'month';
        globalThis._currentDate = new Date('2026-06-08T00:00:00');
        globalThis._monthRange = (d) => ['2026-06-01', '2026-07-01'];
        globalThis._weekRange = (d) => ['2026-06-01', '2026-06-07'];
        globalThis._fetchEvents = async (r1, r2, force) => { state.fetchEventsCalled = force === true; };
        globalThis._open = true;
        globalThis._render = () => { state.renderCalled = true; };
        globalThis._updateBadge = () => { state.badgeCalled = true; };

        eval(functionCode);
        
        globalThis._refreshAfterIntegrationsChanged().then(() => {
          console.log(JSON.stringify({
            caldavSyncedOnce: globalThis._caldavSyncedOnce,
            allEventsCleared: Object.keys(globalThis._allEvents).length === 0,
            fetchedRangesCleared: globalThis._fetchedRanges.length === 0,
            localStorageRemoved: globalThis.localStorage.removed,
            state: state
          }));
        }).catch(err => {
          console.error(err);
          process.exit(1);
        });
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}")
    
    out = json.loads(result.stdout.strip())
    assert out["caldavSyncedOnce"] is False
    assert out["allEventsCleared"] is True
    assert out["fetchedRangesCleared"] is True
    assert out["localStorageRemoved"] == "odysseus-calendar-cache"
    assert out["state"]["syncCalled"] is True
    assert out["state"]["fetchCalsCalled"] is True
    assert out["state"]["fetchEventsCalled"] is True
    assert out["state"]["renderCalled"] is True
    assert out["state"]["badgeCalled"] is True
