"""The Odysseus self-restart must not stop a Hermes gateway that lives inside the launcher tree.

The legacy (non-RunOnly) launcher starts the gateway on demand as a descendant of the app, so a
``taskkill /T`` of the launcher would kill the gateway and any turn in progress. The reviewed
scripts/restart-odysseus-app.ps1 refuses exactly that layout; these tests pin the same refusal into
src/service_controls.py. Pure logic is tested with injected process tables and netstat text; the
schedule-level tests patch the two OS probes and never touch the user's running services.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import service_controls as controls  # noqa: E402


def netstat(*rows):
    """rows: (host, port, pid) -> a netstat -ano -p TCP style listing."""
    lines = ['  Proto  Local Address          Foreign Address        State           PID']
    for host, port, pid in rows:
        lines.append(f'  TCP    {host}:{port}         0.0.0.0:0              LISTENING       {pid}')
    return '\n'.join(lines) + '\n'


# Launcher 100 (powershell) -> app-control 200 (python) -> app 300 (python).
# Gateway 400 is either OUTSIDE the tree (parent 1, the RunOnly/autostart layout)
# or INSIDE it (parent 300, the legacy on-demand layout).
def table(gateway_parent):
    return {
        100: (1, 'powershell.exe', 'powershell -File launch-windows.ps1 -RunOnly'),
        200: (100, 'python.exe', 'python scripts\\windows-app-control.py run'),
        300: (200, 'python.exe', 'python -m uvicorn app:app'),
        400: (gateway_parent, 'python.exe', 'python -m gateway.run'),
        1: (0, 'services.exe', ''),
    }


class GatewayInLauncherTreeTests(unittest.TestCase):
    PORT = 9130

    def test_gateway_inside_the_tree_is_detected(self):
        self.assertTrue(controls.gateway_in_launcher_tree(
            100, self.PORT, table=table(300), netstat_output=netstat(('127.0.0.1', self.PORT, 400))))

    def test_gateway_outside_the_tree_is_not_detected(self):
        self.assertFalse(controls.gateway_in_launcher_tree(
            100, self.PORT, table=table(1), netstat_output=netstat(('127.0.0.1', self.PORT, 400))))

    def test_absent_gateway_is_not_grounds_to_block(self):
        self.assertFalse(controls.gateway_in_launcher_tree(
            100, self.PORT, table=table(300), netstat_output=netstat()))

    def test_listener_on_a_different_port_is_ignored(self):
        self.assertFalse(controls.gateway_in_launcher_tree(
            100, self.PORT, table=table(300), netstat_output=netstat(('127.0.0.1', 8787, 400))))

    def test_vanished_gateway_pid_is_not_detected(self):
        # netstat still names pid 400 but the process table no longer contains it.
        t = table(300)
        del t[400]
        self.assertFalse(controls.gateway_in_launcher_tree(
            100, self.PORT, table=t, netstat_output=netstat(('127.0.0.1', self.PORT, 400))))

    def test_parent_cycle_terminates(self):
        t = table(300)
        t[300] = (400, 'python.exe', 'app')   # 300 -> 400 -> 300 cycle, neither is the launcher
        self.assertFalse(controls.gateway_in_launcher_tree(
            100, self.PORT, table=t, netstat_output=netstat(('127.0.0.1', self.PORT, 400))))

    def test_dual_listener_with_one_inside_the_tree_is_detected(self):
        # A dual IPv4/IPv6 bind can list two PIDs; the in-tree one must still be protected.
        t = table(300)
        t[401] = (1, 'python.exe', 'python -m gateway.run')   # second listener outside
        self.assertTrue(controls.gateway_in_launcher_tree(
            100, self.PORT, table=t,
            netstat_output=netstat(('127.0.0.1', self.PORT, 400), ('[::1]', self.PORT, 401))))

    def test_gateway_that_is_itself_the_launcher_is_detected(self):
        self.assertTrue(controls.gateway_in_launcher_tree(
            400, self.PORT, table=table(300), netstat_output=netstat(('127.0.0.1', self.PORT, 400))))


class ScheduleRestartGuardTests(unittest.TestCase):
    """schedule_odysseus_restart refuses the in-tree layout and otherwise behaves exactly as before."""
    PLAN = (100, 'Odysseus AutoStart')

    def _run(self, gateway_parent, port_rows=None, gateway_port=None):
        rows = port_rows if port_rows is not None else [('127.0.0.1', 9130, 400)]
        spawned = []
        with patch.object(controls, 'processes', return_value=table(gateway_parent)), \
                patch.object(controls, 'listening_pids', return_value=({pid for _, _, pid in rows}, True)):
            result = controls.schedule_odysseus_restart(plan=self.PLAN, spawn=spawned.append, gateway_port=gateway_port)
        return result, spawned

    def test_in_tree_gateway_refuses_and_writes_no_helper(self):
        with patch.object(controls, 'processes', return_value=table(300)), \
                patch.object(controls, 'listening_pids', return_value=({400}, True)), \
                patch.object(controls.tempfile, 'mkstemp') as mkstemp:
            with self.assertRaises(controls.Unavailable) as ctx:
                controls.schedule_odysseus_restart(plan=self.PLAN, spawn=Mock())
        self.assertIn('restart-odysseus-app.ps1', str(ctx.exception))
        mkstemp.assert_not_called()

    def test_outside_tree_gateway_proceeds_and_spawns_helper(self):
        result, spawned = self._run(gateway_parent=1)
        try:
            self.assertEqual(result['state'], 'restarting')
            self.assertEqual(len(spawned), 1)
            text = Path(result['helper']).read_text(encoding='utf-8')
            self.assertIn('/PID 100', text)
            self.assertIn("Start-ScheduledTask -TaskName 'Odysseus AutoStart'", text)
        finally:
            Path(result['helper']).unlink(missing_ok=True)

    def test_absent_gateway_proceeds(self):
        result, spawned = self._run(gateway_parent=300, port_rows=[])
        try:
            self.assertEqual(result['state'], 'restarting')
            self.assertEqual(len(spawned), 1)
        finally:
            Path(result['helper']).unlink(missing_ok=True)

    def test_explicit_gateway_port_is_honored(self):
        # Gateway 400 is in-tree but listens on 9999; asking about 9130 (no listener) must proceed.
        by_port = lambda port, netstat_output=None, fail_closed=False: ({400} if port == 9999 else set(), True)
        with patch.object(controls, 'processes', return_value=table(300)), \
                patch.object(controls, 'listening_pids', side_effect=by_port):
            result = controls.schedule_odysseus_restart(plan=self.PLAN, spawn=Mock(), gateway_port=9130)
        try:
            self.assertEqual(result['state'], 'restarting')
        finally:
            Path(result['helper']).unlink(missing_ok=True)
        # ...and asking about the port it actually owns must refuse.
        with patch.object(controls, 'processes', return_value=table(300)), \
                patch.object(controls, 'listening_pids', side_effect=by_port):
            with self.assertRaises(controls.Unavailable):
                controls.schedule_odysseus_restart(plan=self.PLAN, spawn=Mock(), gateway_port=9999)

    def test_lab_gateway_port_from_environment_is_used(self):
        with patch.dict('os.environ', {'ODYSSEUS_RESTART_LAB_GATEWAY_PORT': '9130'}):
            with patch.object(controls, 'processes', return_value=table(300)), \
                    patch.object(controls, 'listening_pids', return_value=({400}, True)) as lp:
                with self.assertRaises(controls.Unavailable):
                    controls.schedule_odysseus_restart(plan=self.PLAN, spawn=Mock())
        lp.assert_called_once_with(9130, None, fail_closed=True)

    def test_non_numeric_lab_port_refuses_without_guessing(self):
        with patch.dict('os.environ', {'ODYSSEUS_RESTART_LAB_GATEWAY_PORT': 'not-a-port'}):
            with self.assertRaises(controls.Unavailable):
                controls.schedule_odysseus_restart(plan=self.PLAN, spawn=Mock())

    def test_no_plan_still_refuses(self):
        with patch.object(controls, 'odysseus_restart_plan', return_value=None):
            with self.assertRaises(controls.Unavailable):
                controls.schedule_odysseus_restart()


class RoutePassesGatewayPortTests(unittest.TestCase):
    """The /odysseus/restart route hands the configured Hermes port to the guard."""
    ORIGIN = {'Origin': 'http://127.0.0.1:17000'}

    def client(self, url):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import src.application_modes as modes
        app = FastAPI()
        app.state.auth_manager = Mock(is_configured=True)
        app.state.auth_manager.is_admin.return_value = True

        @app.middleware('http')
        async def identity(request, call_next):
            request.state.current_user = 'fixture'
            return await call_next(request)

        service = Mock()
        service.config.url = url
        app.include_router(modes.make_router(service))
        return TestClient(app, base_url='http://127.0.0.1:17000', client=('127.0.0.1', 40000)), service

    def test_route_passes_the_configured_hermes_port(self):
        client, _ = self.client('http://127.0.0.1:9130/')
        with patch.object(controls, 'schedule_odysseus_restart',
                          return_value={'state': 'restarting', 'helper': 'C:\\secret\\x.ps1'}) as sched:
            response = client.post('/api/application-modes/odysseus/restart', headers=self.ORIGIN)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'state': 'restarting'})
        sched.assert_called_once_with(gateway_port=9130)

    def test_route_falls_back_to_the_default_when_no_url_is_configured(self):
        client, service = self.client('http://127.0.0.1:9130/')
        service.config = None
        with patch.object(controls, 'schedule_odysseus_restart',
                          return_value={'state': 'restarting', 'helper': 'C:\\secret\\x.ps1'}) as sched:
            client.post('/api/application-modes/odysseus/restart', headers=self.ORIGIN)
        sched.assert_called_once_with(gateway_port=None)


if __name__ == '__main__':
    unittest.main()