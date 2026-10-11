"""Local HTTP API and dashboard. Binds to 127.0.0.1 only."""
import hmac
import json
import logging
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import context, engine, nudge
from .config import ROOT, ensure_token, settings
from .worker import Worker

log = logging.getLogger("align.server")
MAX_BODY = 20000


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind calls socket.getfqdn(), a reverse DNS lookup that can stall
        # for tens of seconds. The server is loopback-only, so skip it.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def make_server(port: int | None = None, worker: Worker | None = None) -> LocalServer:
    s = settings()
    token = ensure_token(s)
    worker = worker or Worker().start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

        def send_json(self, obj, status=200):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def local_host(self) -> bool:
            # Rejects DNS-rebinding requests that reach 127.0.0.1 under a foreign hostname.
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            return host in ("127.0.0.1", "localhost")

        def authorized(self) -> bool:
            return hmac.compare_digest(self.headers.get("X-Align-Token", ""), token)

        def guard(self) -> bool:
            if not self.local_host():
                self.send_json({"error": "forbidden host"}, 403)
                return False
            if urlparse(self.path).path not in ("/", "/health") and not self.authorized():
                self.send_json({"error": "unauthorized"}, 401)
                return False
            return True

        def do_GET(self):
            if not self.guard():
                return
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/":
                    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
                    body = html.replace("__ALIGN_TOKEN__", token).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                elif url.path == "/health":
                    self.send_json({"ok": True})
                elif url.path == "/api/state":
                    self.send_json(engine.snapshot(q.get("task") or None))
                elif url.path == "/api/hook":
                    self.send_json(nudge.respond(
                        q["task"], q["session"], q["hook"], q.get("mode") or None, q.get("event_id") or None,
                        float(q["wait"]) if "wait" in q else None))
                elif url.path == "/api/findings":
                    self.send_json(engine.open_findings(q["task"], q.get("session")))
                elif url.path == "/api/knowledge":
                    self.send_json(engine.search_knowledge(q.get("q", ""), q.get("task") or None,
                                                           int(q.get("limit", "20"))))
                else:
                    self.send_json({"error": "not found"}, 404)
            except (ValueError, KeyError) as e:
                self.send_json({"error": str(e)}, 400)

        def do_POST(self):
            if not self.guard():
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if n < 1 or n > MAX_BODY:
                    return self.send_json({"error": "invalid payload size"}, 413)
                data = json.loads(self.rfile.read(n))
                if not isinstance(data, dict):
                    raise ValueError("expected a JSON object")
                path = urlparse(self.path).path
                if path == "/api/events":
                    event_id = engine.ingest(data)
                    worker.submit(event_id)
                    return self.send_json({"event_id": event_id, "status": "pending"}, 202)
                if path == "/api/respond":
                    return self.send_json(engine.respond(data["finding_id"], data["response"],
                                                         str(data.get("note", ""))))
                if path == "/api/import":
                    return self.send_json(context.import_dir(data["path"]))
                self.send_json({"error": "unknown path"}, 404)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
                self.send_json({"error": str(e)}, 400)

    return LocalServer((s.host, s.port if port is None else port), Handler)
