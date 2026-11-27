"""The /proc-based Linux path of service_controls: process table, port owner and tree kill (no netstat.exe / PowerShell)."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src import service_controls as controls  # noqa: E402
from src.hermes_webapp_component import _port_free  # noqa: E402


@unittest.skipIf(os.name == 'nt' or not os.path.isdir('/proc/self/fd'), 'Linux /proc only')
class ProcPathTests(unittest.TestCase):
    def test_process_table_has_this_process_and_its_parent(self):
        table = controls.processes()
        ppid, image, command = table[os.getpid()]
        self.assertEqual(ppid, os.getppid())
        self.assertTrue(controls._is_python_image(image), image)
        self.assertIn('python', command)

    def test_finds_the_loopback_listener_and_flags_a_wildcard_one(self):
        with socket.socket() as loopback, socket.socket() as wildcard:
            loopback.bind(('127.0.0.1', 0)); loopback.listen()
            wildcard.bind(('0.0.0.0', 0)); wildcard.listen()
            self.assertEqual(controls.listening_pids(loopback.getsockname()[1]), ({os.getpid()}, True))
            self.assertEqual(controls.listening_pids(wildcard.getsockname()[1]), ({os.getpid()}, False))

    def test_a_free_port_has_no_listener(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        self.assertEqual(controls.listening_pids(port), (set(), True))

    def test_python_image_names(self):
        for name in ('python', 'python3', 'python3.14', 'python.exe', 'pythonw.exe'):
            self.assertTrue(controls._is_python_image(name), name)
        for name in ('node', 'pythonista', '', None):
            self.assertFalse(controls._is_python_image(name), name)

    def test_kill_tree_stops_the_process_and_its_children(self):
        parent = subprocess.Popen(['sh', '-c', 'sleep 60 & sleep 60 & wait'])
        time.sleep(0.3)
        children = [pid for pid, row in controls.processes().items() if row[0] == parent.pid]
        self.assertEqual(len(children), 2)
        controls.kill_tree(parent.pid)
        parent.wait(timeout=5)
        time.sleep(0.2)
        self.assertFalse([pid for pid in children if controls._posix_state(pid) not in (None, 'Z')])

    def test_port_with_only_time_wait_connections_counts_as_free(self):
        server = socket.socket(); server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', 0)); server.listen(); port = server.getsockname()[1]
        client = socket.create_connection(('127.0.0.1', port)); conn, _ = server.accept()
        conn.close(); client.close(); server.close()   # server side closed first -> TIME_WAIT on the server port
        self.assertTrue(_port_free(port))
        with socket.socket() as busy:
            busy.bind(('127.0.0.1', 0)); busy.listen()
            self.assertFalse(_port_free(busy.getsockname()[1]))


if __name__ == '__main__':
    unittest.main()
