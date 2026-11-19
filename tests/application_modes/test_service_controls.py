"""Stop/restart controls for the local Hermes WebUI and for Odysseus itself.

Pure logic is tested with injected tables; the Windows-only tests below use real processes on loopback ports
and a real WMI spawn, never the user's running services.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import service_controls as controls  # noqa: E402
import src.application_modes as modes  # noqa: E402

WINDOWS_ONLY = unittest.skipUnless(os.name == 'nt', 'Windows service controls')

NETSTAT = '''
  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:8787         0.0.0.0:0              LISTENING       1532
  TCP    127.0.0.1:7000         0.0.0.0:0              LISTENING       16240
  TCP    0.0.0.0:9000           0.0.0.0:0              LISTENING       444
  TCP    [::1]:9100             [::]:0                 LISTENING       555
  TCP    127.0.0.1:8787         127.0.0.1:51000        ESTABLISHED     1532
  TCP    127.0.0.1:18787        0.0.0.0:0              LISTENING       999
'''


@contextmanager
def health_server(payload=None):
    payload = payload if payload is not None else {'status': 'ok', 'sessions': 0, 'server_started_at': 1.0, 'active_runs': 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state = {'server': server, 'stopped': False}

    def close():
        if not state['stopped']:
            state['stopped'] = True
            server.shutdown()
            server.server_close()

    try:
        yield f'http://127.0.0.1:{server.server_port}/', close
    finally:
        close()
        thread.join()


class ParsingTests(unittest.TestCase):
    def test_exact_port_listeners_only(self):
        self.assertEqual(controls.listening_pids(8787, NETSTAT), ({1532}, True))
        self.assertEqual(controls.listening_pids(7000, NETSTAT), ({16240}, True))
        self.assertEqual(controls.listening_pids(787, NETSTAT), (set(), True), 'a port that merely ends the same way must not match')
        self.assertEqual(controls.listening_pids(18787, NETSTAT), ({999}, True))

    def test_wildcard_binding_is_reported_as_not_loopback_only(self):
        self.assertEqual(controls.listening_pids(9000, NETSTAT), ({444}, False))
        self.assertEqual(controls.listening_pids(9100, NETSTAT), ({555}, True))

    def test_launcher_is_found_through_the_ancestor_chain(self):
        table = {
            50: (4, 'wscript.exe', 'wscript start-hidden.vbs'),
            60: (50, 'powershell.exe', 'powershell -File F:\\Odysseus\\launch-windows.ps1 -Port 7000'),
            70: (60, 'python.exe', 'venv\\python.exe -m uvicorn app:app'),
            80: (70, 'python.exe', 'python312\\python.exe -m uvicorn app:app'),
            90: (4, 'python.exe', 'standalone.py'),
        }
        self.assertEqual(controls.find_launcher(table, 80), 60)
        self.assertIsNone(controls.find_launcher(table, 90), 'a process outside the launcher has no launcher')
        self.assertIsNone(controls.find_launcher({1: (1, 'python.exe', 'loop')}, 1), 'a parent cycle must terminate')

    def test_restart_script_rejects_unvalidated_values(self):
        for pid, task in [(0, 'Odysseus AutoStart'), (4, 'Odysseus AutoStart'), ('60', 'Odysseus AutoStart'),
                          (60, "x'; Remove-Item C:\\ -Recurse #"), (60, ''), (60, 'a' * 101)]:
            with self.assertRaises(controls.Unavailable):
                controls.restart_script(pid, task)
        script = controls.restart_script(60, 'Odysseus AutoStart')
        self.assertIn('taskkill.exe /F /T /PID 60', script)
        self.assertIn("Start-ScheduledTask -TaskName 'Odysseus AutoStart'", script)
        self.assertLess(script.index('taskkill'), script.index('Start-ScheduledTask'))


class StopListenerTests(unittest.TestCase):
    def test_refuses_when_the_port_has_several_or_non_loopback_owners(self):
        with patch.object(controls, 'listening_pids', return_value=({1, 2}, True)), self.assertRaises(controls.Unavailable):
            controls.stop_hermes_listener(8787, table={}, kill=Mock())
        kill = Mock()
        with patch.object(controls, 'listening_pids', return_value=({1}, False)), self.assertRaises(controls.Unavailable):
            controls.stop_hermes_listener(8787, table={1: (0, 'python.exe', '')}, kill=kill)
        kill.assert_not_called()

    def test_refuses_a_listener_that_is_not_python(self):
        kill = Mock()
        with patch.object(controls, 'listening_pids', return_value=({7}, True)), self.assertRaises(controls.Unavailable):
            controls.stop_hermes_listener(8787, table={7: (0, 'nginx.exe', '')}, kill=kill)
        kill.assert_not_called()

    def test_kills_only_the_exact_listener(self):
        kill = Mock()
        with patch.object(controls, 'listening_pids', return_value=({7}, True)):
            self.assertEqual(controls.stop_hermes_listener(8787, table={7: (0, 'pythonw.exe', ''), 8: (0, 'python.exe', '')}, kill=kill), 7)
        kill.assert_called_once_with(7)


class ServiceTests(unittest.TestCase):
    def service(self, url, command=()):
        return modes.ModeService(modes.ModeConfig(url=url, command=command, log_dir=Path(os.environ.get('TEMP', '.')) if command else None))

    def test_stop_is_refused_while_runs_are_active_and_forced_through_on_request(self):
        with health_server({'status': 'ok', 'sessions': 1, 'server_started_at': 1.0, 'active_runs': 2}) as (url, close):
            service = self.service(url)
            with patch.object(controls, 'stop_hermes_listener', side_effect=lambda port: close()) as stop, \
                    patch.object(controls, 'supported', return_value=True):
                with self.assertRaises(controls.ActiveRuns) as raised:
                    service.stop()
                self.assertEqual(raised.exception.count, 2)
                stop.assert_not_called()
                self.assertEqual(service.stop(force=True)['state'], 'stopped')
                stop.assert_called_once()

    def test_stop_when_already_stopped_is_a_quiet_success(self):
        with health_server() as (url, close):
            close()
            with patch.object(controls, 'supported', return_value=True), patch.object(controls, 'stop_hermes_listener') as stop:
                self.assertEqual(self.service(url).stop()['state'], 'stopped')
                stop.assert_not_called()

    def test_stop_never_touches_a_port_that_is_not_hermes(self):
        class Silent(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'not hermes'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Silent)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            service = self.service(f'http://127.0.0.1:{server.server_port}/')
            with patch.object(controls, 'supported', return_value=True), patch.object(controls, 'stop_hermes_listener') as stop:
                with self.assertRaises(RuntimeError):
                    service.stop(force=True)
                stop.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()

    def test_restart_requires_a_configured_launcher_and_stops_before_starting(self):
        with health_server() as (url, close):
            with self.assertRaises(controls.Unavailable):
                self.service(url).restart()
            service = self.service(url, command=('cmd',))
            order = []
            with patch.object(controls, 'supported', return_value=True), \
                    patch.object(controls, 'stop_hermes_listener', side_effect=lambda port: (order.append('stop'), close())), \
                    patch.object(modes.ModeService, 'start', side_effect=lambda self_: order.append('start') or {'state': 'starting'}, autospec=True):
                self.assertEqual(service.restart(), {'state': 'starting'})
            self.assertEqual(order, ['stop', 'start'])

    def test_unsupported_platform_is_reported_not_attempted(self):
        with health_server() as (url, _):
            with patch.object(controls, 'supported', return_value=False), self.assertRaises(controls.Unavailable):
                self.service(url).stop(force=True)


class RouteTests(unittest.TestCase):
    ORIGIN = {'Origin': 'http://127.0.0.1:17000'}

    def client(self, admin=True, user='fixture'):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.state.auth_manager = Mock(is_configured=True)
        app.state.auth_manager.is_admin.return_value = admin

        @app.middleware('http')
        async def identity(request, call_next):
            request.state.current_user = user
            return await call_next(request)

        service = Mock()
        service.status.return_value = {'enabled': True, 'state': 'ready'}
        service.can_start.return_value = True
        service.stop.return_value = {'enabled': True, 'state': 'stopped'}
        service.restart.return_value = {'enabled': True, 'state': 'starting'}
        app.include_router(modes.make_router(service))
        return TestClient(app, base_url='http://127.0.0.1:17000', client=('127.0.0.1', 40000)), service

    def test_every_control_needs_a_same_origin_local_administrator(self):
        for path in ('/hermes/stop', '/hermes/restart', '/odysseus/restart'):
            client, service = self.client()
            self.assertEqual(client.post('/api/application-modes' + path).status_code, 403, 'no Origin header')
            self.assertEqual(client.post('/api/application-modes' + path, headers={'Origin': 'https://evil.example'}).status_code, 403)
            self.assertEqual(client.post('/api/application-modes' + path, headers={**self.ORIGIN, 'X-Forwarded-For': '192.0.2.1'}).status_code, 403)
            client, service = self.client(admin=False)
            self.assertEqual(client.post('/api/application-modes' + path, headers=self.ORIGIN).status_code, 403)
            for user in ('internal-tool', 'api'):
                client, service = self.client(user=user)
                self.assertEqual(client.post('/api/application-modes' + path, headers=self.ORIGIN).status_code, 403)
            service.stop.assert_not_called()
            service.restart.assert_not_called()

    def test_stop_and_restart_pass_force_only_when_asked(self):
        client, service = self.client()
        self.assertEqual(client.post('/api/application-modes/hermes/stop', headers=self.ORIGIN).json()['state'], 'stopped')
        service.stop.assert_called_once_with(force=False)
        client.post('/api/application-modes/hermes/restart?force=true', headers=self.ORIGIN)
        service.restart.assert_called_once_with(force=True)

    def test_active_runs_answer_tells_the_page_to_ask_before_forcing(self):
        client, service = self.client()
        service.stop.side_effect = controls.ActiveRuns(3)
        response = client.post('/api/application-modes/hermes/stop', headers=self.ORIGIN)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['active_runs'], 3)
        self.assertTrue(response.json()['force_required'])

    def test_unavailable_and_runtime_failures_are_recoverable(self):
        client, service = self.client()
        service.stop.side_effect = controls.Unavailable('Not Windows')
        self.assertEqual(client.post('/api/application-modes/hermes/stop', headers=self.ORIGIN).status_code, 409)
        service.stop.side_effect = RuntimeError('Port busy')
        self.assertIn('Port busy', client.post('/api/application-modes/hermes/stop', headers=self.ORIGIN).text)
        service.stop.side_effect = OSError('disk')
        self.assertEqual(client.post('/api/application-modes/hermes/stop', headers=self.ORIGIN).status_code, 503)

    def test_odysseus_restart_schedules_the_helper_and_hides_its_path(self):
        client, _ = self.client()
        with patch.object(controls, 'schedule_odysseus_restart', return_value={'state': 'restarting', 'helper': 'C:\\secret\\x.ps1'}):
            response = client.post('/api/application-modes/odysseus/restart', headers=self.ORIGIN)
        self.assertEqual(response.json(), {'state': 'restarting'})
        with patch.object(controls, 'schedule_odysseus_restart', side_effect=controls.Unavailable('No launcher')):
            self.assertEqual(client.post('/api/application-modes/odysseus/restart', headers=self.ORIGIN).status_code, 409)

    def test_status_advertises_only_the_controls_that_work_here(self):
        client, service = self.client()
        with patch.object(controls, 'supported', return_value=True), patch.object(controls, 'odysseus_restart_plan', return_value=None):
            result = client.get('/api/application-modes').json()
        self.assertEqual(result['controls'], {'hermes_stop': True, 'hermes_restart': True, 'hermes_start': False, 'odysseus_restart': False})
        service.status.return_value = {'enabled': False}
        self.assertNotIn('controls', client.get('/api/application-modes').json())
        with patch.object(controls, 'supported', return_value=False):
            service.status.return_value = {'enabled': True, 'state': 'ready'}
            self.assertEqual(set(client.get('/api/application-modes').json()['controls'].values()), {False})


class OdysseusRestartTests(unittest.TestCase):
    # The gateway guard reads the process table and the gateway port; inject both so these stay hermetic.
    NO_GATEWAY = {'processes': {60: (1, 'powershell.exe', 'launch-windows.ps1')}, 'listening_pids': (set(), True)}

    def test_helper_script_is_written_spawned_and_validated(self):
        spawned = []
        with patch.object(controls, 'processes', return_value=self.NO_GATEWAY['processes']), \
                patch.object(controls, 'listening_pids', return_value=self.NO_GATEWAY['listening_pids']):
            result = controls.schedule_odysseus_restart(plan=(60, 'Odysseus AutoStart'), spawn=spawned.append)
        try:
            self.assertEqual(result['state'], 'restarting')
            text = Path(result['helper']).read_text(encoding='utf-8')
            self.assertIn('/PID 60', text)
            self.assertIn('-WindowStyle Hidden -File', spawned[0])
            self.assertIn(result['helper'], spawned[0])
        finally:
            Path(result['helper']).unlink(missing_ok=True)

    def test_failed_spawn_leaves_no_helper_file_behind(self):
        seen = {}

        def boom(command):
            seen['path'] = command.split('-File "')[1].rstrip('"')
            raise controls.Unavailable('no')

        with patch.object(controls, 'processes', return_value=self.NO_GATEWAY['processes']), \
                patch.object(controls, 'listening_pids', return_value=self.NO_GATEWAY['listening_pids']):
            with self.assertRaises(controls.Unavailable):
                controls.schedule_odysseus_restart(plan=(60, 'Odysseus AutoStart'), spawn=boom)
        self.assertFalse(Path(seen['path']).exists())

    def test_restart_is_refused_without_a_launcher_plan(self):
        with patch.object(controls, 'odysseus_restart_plan', return_value=None), self.assertRaises(controls.Unavailable):
            controls.schedule_odysseus_restart()

    def test_task_name_override_must_be_a_plain_name(self):
        controls.odysseus_restart_plan.cache_clear()
        try:
            with patch.dict(os.environ, {'ODYSSEUS_RESTART_TASK': 'x"; calc #'}), patch.object(controls, 'supported', return_value=True), \
                    patch.object(controls, 'processes') as table:
                self.assertIsNone(controls.odysseus_restart_plan())
                table.assert_not_called()
        finally:
            controls.odysseus_restart_plan.cache_clear()


@WINDOWS_ONLY
class RealProcessTests(unittest.TestCase):
    def test_process_table_contains_this_process(self):
        table = controls.processes()
        self.assertIn(os.getpid(), table)
        self.assertEqual(table[os.getpid()][1], os.path.basename(sys.executable).lower())

    def test_real_listener_is_found_by_port_and_killed_with_its_children(self):
        code = ('import socket,subprocess,sys,time\n'
                's=socket.socket(); s.bind(("127.0.0.1",0)); s.listen(); print(s.getsockname()[1],flush=True)\n'
                'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); print(child.pid,flush=True)\n'
                'time.sleep(60)\n')
        real_python = getattr(sys, '_base_executable', sys.executable)    # the venv's python.exe is a shim with a child
        process = subprocess.Popen([real_python, '-c', code.replace('sys.executable', 'sys._base_executable')], stdout=subprocess.PIPE, text=True)
        try:
            port, child = int(process.stdout.readline()), int(process.stdout.readline())
            self.assertEqual(controls.listening_pids(port)[0], {process.pid})
            self.assertEqual(controls.stop_hermes_listener(port), process.pid)
            self.assertIsNotNone(process.wait(timeout=15))
            deadline = time.time() + 10
            while child in controls.processes() and time.time() < deadline:
                time.sleep(0.3)
            self.assertNotIn(child, controls.processes(), 'the listener tree, including its child, must be gone')
        finally:
            process.kill()
            process.wait()

    def test_wmi_spawned_helper_is_not_a_descendant_of_this_process(self):
        marker = f'ODYSSEUS-TEST-{os.getpid()}-{time.time_ns()}'
        controls.spawn_outside_tree(f'cmd.exe /c "ping -n 30 127.0.0.1 >NUL & rem {marker}"')
        found = None
        try:
            deadline = time.time() + 15
            while not found and time.time() < deadline:
                found = next((pid for pid, (_, name, command) in controls.processes().items() if marker in command and name == 'cmd.exe'), None)
                time.sleep(0.3)
            self.assertIsNotNone(found, 'the helper process did not start')
            table, pid, ancestors = controls.processes(), found, set()
            while pid in table and pid not in ancestors:
                ancestors.add(pid)
                pid = table[pid][0]
            self.assertNotIn(os.getpid(), ancestors, 'a helper inside this process tree would die with it')
        finally:
            if found:
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(found)], capture_output=True)


if __name__ == '__main__':
    unittest.main()
