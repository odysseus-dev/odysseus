"""WINLIFE-2 caveat (a): a FAILED netstat probe must fail the restart guard CLOSED.

``listening_pids`` historically ignored the netstat returncode, so a failed probe parsed as
"no listeners" and ``gateway_in_launcher_tree`` returned False — letting a tree restart
proceed on a machine whose gateway layout could not be verified (a fail-open edge on a
fail-closed safety refusal). The guard now opts into ``fail_closed=True``: a failed live
probe is UNKNOWN and raises ``Unavailable`` naming scripts/restart-odysseus-app.ps1, before
any helper is written or spawned. Default callers keep the lenient semantics.

These tests inject the probe at the ``_run`` edge (returncode/stdout fixtures) so the real
``listening_pids`` parser runs; no live netstat, process table, helper spawn or service is
touched. Injected ``netstat_output`` stays pure (no probe). PID-reuse (caveat b) is out of scope.
"""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import service_controls as controls  # noqa: E402

PORT = 9130
PLAN = (100, 'Odysseus AutoStart')


def netstat(*rows):
    """rows: (host, port, pid) -> a netstat -ano -p TCP style listing."""
    lines = ['  Proto  Local Address          Foreign Address        State           PID']
    for host, port, pid in rows:
        lines.append(f'  TCP    {host}:{port}         0.0.0.0:0              LISTENING       {pid}')
    return '\n'.join(lines) + '\n'


def table(gateway_parent):
    # Launcher 100 -> app-control 200 -> app 300; gateway 400 inside (300) or outside (1).
    return {
        100: (1, 'powershell.exe', 'powershell -File launch-windows.ps1 -RunOnly'),
        200: (100, 'python.exe', 'python scripts\\windows-app-control.py run'),
        300: (200, 'python.exe', 'python -m uvicorn app:app'),
        400: (gateway_parent, 'python.exe', 'python -m gateway.run'),
        1: (0, 'services.exe', ''),
    }


def probe(returncode=0, stdout=''):
    """A netstat probe result fixture (the only OS edge the guard touches before refusing)."""
    return subprocess.CompletedProcess(args=['netstat.exe', '-ano', '-p', 'TCP'],
                                       returncode=returncode, stdout=stdout, stderr='')


class GuardProbeFailClosedTests(unittest.TestCase):
    """gateway_in_launcher_tree treats a failed LIVE probe as UNKNOWN, not 'absent'."""

    def test_failed_probe_fails_closed_before_process_table(self):
        with patch.object(controls, '_run', return_value=probe(returncode=1, stdout='')), \
                patch.object(controls, 'processes') as processes:
            with self.assertRaises(controls.Unavailable) as ctx:
                controls.gateway_in_launcher_tree(100, PORT)
        self.assertIn('restart-odysseus-app.ps1', str(ctx.exception))
        processes.assert_not_called()   # refusal fires at the probe, before the table walk

    def test_empty_probe_output_fails_closed(self):
        # netstat always prints a header; empty stdout with rc 0 is still an unverified probe.
        with patch.object(controls, '_run', return_value=probe(returncode=0, stdout='   \n')):
            with self.assertRaises(controls.Unavailable):
                controls.gateway_in_launcher_tree(100, PORT)

    def test_failed_probe_with_partial_output_still_fails_closed(self):
        # Even output that names an in-tree listener is untrusted when the probe failed.
        with patch.object(controls, '_run', return_value=probe(returncode=1, stdout=netstat(('127.0.0.1', PORT, 400)))):
            with self.assertRaises(controls.Unavailable):
                controls.gateway_in_launcher_tree(100, PORT)

    def test_successful_probe_in_tree_is_detected(self):
        with patch.object(controls, '_run', return_value=probe(0, netstat(('127.0.0.1', PORT, 400)))), \
                patch.object(controls, 'processes', return_value=table(300)):
            self.assertTrue(controls.gateway_in_launcher_tree(100, PORT))

    def test_successful_probe_outside_tree_is_not_detected(self):
        with patch.object(controls, '_run', return_value=probe(0, netstat(('127.0.0.1', PORT, 400)))), \
                patch.object(controls, 'processes', return_value=table(1)):
            self.assertFalse(controls.gateway_in_launcher_tree(100, PORT))

    def test_successful_probe_absent_gateway_is_not_detected(self):
        with patch.object(controls, '_run', return_value=probe(0, netstat())), \
                patch.object(controls, 'processes', return_value=table(300)):
            self.assertFalse(controls.gateway_in_launcher_tree(100, PORT))

    def test_injected_netstat_output_skips_the_probe(self):
        with patch.object(controls, '_run') as run:
            self.assertFalse(controls.gateway_in_launcher_tree(
                100, PORT, table=table(300), netstat_output=netstat()))
        run.assert_not_called()   # pure path unchanged: no live probe when output is injected


class SharedSemanticsPreservedTests(unittest.TestCase):
    """Default (non-guard) callers keep the historical lenient semantics."""

    def test_default_failed_probe_stays_lenient(self):
        with patch.object(controls, '_run', return_value=probe(returncode=1, stdout='')):
            self.assertEqual(controls.listening_pids(PORT), (set(), True))

    def test_default_empty_output_stays_lenient(self):
        with patch.object(controls, '_run', return_value=probe(returncode=0, stdout='')):
            self.assertEqual(controls.listening_pids(PORT), (set(), True))

    def test_stop_hermes_listener_failed_probe_still_refuses(self):
        # The stop path already refuses when the port is not owned by exactly one process.
        with patch.object(controls, '_run', return_value=probe(returncode=1, stdout='')):
            with self.assertRaises(controls.Unavailable):
                controls.stop_hermes_listener(PORT)


class ScheduleProbeFailClosedTests(unittest.TestCase):
    """schedule_odysseus_restart refuses a failed probe BEFORE writing/spawning any helper."""

    def _refuses(self, fixture):
        spawned = Mock()
        with patch.object(controls, '_run', return_value=fixture), \
                patch.object(controls, 'processes', return_value=table(300)), \
                patch.object(controls.tempfile, 'mkstemp') as mkstemp:
            with self.assertRaises(controls.Unavailable) as ctx:
                controls.schedule_odysseus_restart(plan=PLAN, spawn=spawned)
        self.assertIn('restart-odysseus-app.ps1', str(ctx.exception))
        mkstemp.assert_not_called()
        spawned.assert_not_called()

    def test_failed_probe_refuses_before_helper_spawn(self):
        self._refuses(probe(returncode=1, stdout=''))

    def test_empty_probe_output_refuses_before_helper_spawn(self):
        self._refuses(probe(returncode=0, stdout=''))

    def test_successful_probe_in_tree_refuses(self):
        with patch.object(controls, '_run', return_value=probe(0, netstat(('127.0.0.1', PORT, 400)))), \
                patch.object(controls, 'processes', return_value=table(300)), \
                patch.object(controls.tempfile, 'mkstemp') as mkstemp:
            with self.assertRaises(controls.Unavailable) as ctx:
                controls.schedule_odysseus_restart(plan=PLAN, spawn=Mock())
        self.assertIn('restart-odysseus-app.ps1', str(ctx.exception))
        mkstemp.assert_not_called()

    def test_successful_probe_outside_tree_spawns_helper(self):
        spawned = []
        with patch.object(controls, '_run', return_value=probe(0, netstat(('127.0.0.1', PORT, 400)))), \
                patch.object(controls, 'processes', return_value=table(1)):
            result = controls.schedule_odysseus_restart(plan=PLAN, spawn=spawned.append)
        try:
            self.assertEqual(result['state'], 'restarting')
            self.assertEqual(len(spawned), 1)
            self.assertIn('/PID 100', Path(result['helper']).read_text(encoding='utf-8'))
        finally:
            Path(result['helper']).unlink(missing_ok=True)

    def test_successful_probe_absent_gateway_proceeds(self):
        spawned = []
        with patch.object(controls, '_run', return_value=probe(0, netstat())), \
                patch.object(controls, 'processes', return_value=table(300)):
            result = controls.schedule_odysseus_restart(plan=PLAN, spawn=spawned.append)
        try:
            self.assertEqual(result['state'], 'restarting')
            self.assertEqual(len(spawned), 1)
        finally:
            Path(result['helper']).unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()