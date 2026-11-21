"""OpenAI-compatible model that fails: /fail/v1 answers 500, /slow/v1 hangs 300 s, /garbage/v1 answers non-JSON."""
import json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        self._send(200, json.dumps({"object": "list", "data": [{"id": "bad-model", "object": "model"}]}))
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); self.rfile.read(n)
        mode = sys.argv[2] if len(sys.argv) > 2 else self.path.split("/")[1]
        if mode == "fail": return self._send(500, json.dumps({"error": {"message": "upstream exploded"}}))
        if mode == "slow": time.sleep(300); return self._send(504, "{}")
        if mode == "garbage": return self._send(200, "<html>not json</html>", "text/html")
        self._send(404, "{}")
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
