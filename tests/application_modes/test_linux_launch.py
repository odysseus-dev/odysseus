import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import application_modes, hermes_migration


class WebuiStartCommandTests(unittest.TestCase):
    def test_windows_uses_the_powershell_launcher(self):
        with mock.patch.object(application_modes.os, 'name', 'nt'):
            command = application_modes.webui_start_command(Path('repo'), 8787)
        self.assertEqual(command[0], 'powershell.exe')
        self.assertTrue(command[command.index('-File') + 1].endswith('start.ps1'))
        self.assertEqual(command[-2:], ('-Port', '8787'))

    def test_linux_uses_start_sh_and_never_powershell(self):
        with mock.patch.object(application_modes.os, 'name', 'posix'):
            command = application_modes.webui_start_command(Path('repo'), '8787')
        self.assertEqual(command[0], 'bash')
        self.assertTrue(command[1].endswith('start.sh'))
        self.assertEqual(command[2:], ('--host', '127.0.0.1', '8787'))
        self.assertNotIn('powershell.exe', command)


@unittest.skipIf(os.name == 'nt', 'POSIX pid probe')
class LiveGatewayPosixTests(unittest.TestCase):
    def _home(self, pid):
        import json
        import tempfile
        home = Path(tempfile.mkdtemp())
        (home / 'gateway.pid').write_text(json.dumps({'pid': pid}), encoding='utf-8')
        return home

    def test_own_pid_is_live(self):
        self.assertTrue(hermes_migration._live_gateway(self._home(os.getpid())))

    def test_dead_pid_is_not_live(self):
        import subprocess
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait()
        self.assertFalse(hermes_migration._live_gateway(self._home(child.pid)))


if __name__ == '__main__':
    unittest.main()
