"""Run a browser probe against a process-local ephemeral static server."""
import http.server
import os
import subprocess
import sys
import threading

class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Quiet)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    result = subprocess.run(sys.argv[1:], env=dict(os.environ, ODYSSEUS_TEST_STATIC_ORIGIN=f'http://127.0.0.1:{server.server_port}'))
finally:
    server.shutdown()
sys.exit(result.returncode)
