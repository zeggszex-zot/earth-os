"""A tiny OpenAI-compatible server for tests: replies from a queue of canned messages."""
import json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

class Fake:
    def __init__(self, replies):
        self.replies, self.requests, self.gets = list(replies), [], 0
        fake = self
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def _send(self, obj):
                b = json.dumps(obj).encode(); self.send_response(200)
                self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
                self.end_headers(); self.wfile.write(b)
            def do_GET(self): fake.gets += 1; self._send({"data": [{"id": "fake-owl"}]})
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(body)
                r = fake.replies.pop(0) if fake.replies else {"content": ""}
                self._send({"choices": [{"message": r if isinstance(r, dict) else {"content": r}}]})
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
    def close(self): self.srv.shutdown()
