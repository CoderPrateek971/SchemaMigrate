"""HTTP Server and REST/SSE endpoints for SchemaMigrate Web Dashboard."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import queue
from typing import Any
from urllib.parse import parse_qs, urlparse

from schemamigrate.web.runner import MigrationRunner


class WebHandler(BaseHTTPRequestHandler):
    """Handles REST API, Server-Sent Events, and static file serving."""

    runner: MigrationRunner

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy default console logging
        pass

    # -------------------------------------------------------------------------
    # Response Helpers
    # -------------------------------------------------------------------------

    def send_json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            raw = self.rfile.read(length).decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
        return {}

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # -------------------------------------------------------------------------
    # GET Handlers (Static files & SSE Stream & Status)
    # -------------------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.serve_index()
        elif path == "/api/status":
            self.send_json(self.runner.get_state())
        elif path == "/api/stream":
            self.serve_sse_stream()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def serve_index(self) -> None:
        index_file = os.path.join(os.path.dirname(__file__), "static", "index.html")
        if os.path.exists(index_file):
            with open(index_file, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"index.html missing")

    def serve_sse_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q = self.runner.subscribe()
        try:
            # Send initial state immediately
            init_payload = f"event: init\ndata: {json.dumps(self.runner.get_state(), default=str)}\n\n"
            self.wfile.write(init_payload.encode("utf-8"))
            self.wfile.flush()

            while True:
                try:
                    msg = q.get(timeout=3.0)
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # Keep-alive comment
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, IOError):
            pass
        finally:
            self.runner.unsubscribe(q)

    # -------------------------------------------------------------------------
    # POST Handlers (Control Actions)
    # -------------------------------------------------------------------------

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        body = self.read_json_body()

        if path == "/api/plan":
            sql_text = body.get("sql", "").strip()
            if not sql_text:
                self.send_json({"status": "error", "message": "SQL statement is required"}, 400)
                return
            res = self.runner.plan_sql(sql_text, body.get("dsn"))
            self.send_json(res)

        elif path == "/api/prepare":
            sql_text = body.get("sql", "ALTER TABLE schemamigrate_demo.customers ADD COLUMN loyalty_level integer DEFAULT 1").strip()
            allow_risky = bool(body.get("allow_risky", False))
            res = self.runner.prepare(sql_text, body.get("dsn"), allow_risky)
            self.send_json(res)

        elif path == "/api/copy/start":
            batch_size = int(body.get("batch_size", 250))
            throttle = float(body.get("throttle", 0.3))
            res = self.runner.start_copy(batch_size, throttle)
            self.send_json(res)

        elif path == "/api/copy/pause":
            res = self.runner.pause_copy()
            self.send_json(res)

        elif path == "/api/copy/resume":
            res = self.runner.resume_copy()
            self.send_json(res)

        elif path == "/api/cancel":
            res = self.runner.cancel_migration()
            self.send_json(res)

        elif path == "/api/cutover":
            res = self.runner.cutover()
            self.send_json(res)

        elif path == "/api/rollback":
            res = self.runner.rollback()
            self.send_json(res)

        elif path == "/api/finalize":
            res = self.runner.finalize()
            self.send_json(res)

        elif path == "/api/config":
            dsn = body.get("dsn")
            if dsn is not None:
                self.runner.dsn = dsn.strip() or None
            self.send_json({"status": "ok", "dsn": self.runner.dsn})

        else:
            self.send_json({"status": "error", "message": f"Endpoint not found: {path}"}, 404)


def create_server(host: str = "127.0.0.1", port: int = 8080, dsn: str | None = None) -> ThreadingHTTPServer:
    runner = MigrationRunner(dsn=dsn)
    handler_cls = type("ConfiguredWebHandler", (WebHandler,), {"runner": runner})
    server = ThreadingHTTPServer((host, port), handler_cls)
    return server


def start_server(host: str = "127.0.0.1", port: int = 8080, dsn: str | None = None) -> None:
    server = create_server(host=host, port=port, dsn=dsn)
    print(f"\n========================================================")
    print(f"🚀 SchemaMigrate Web Dashboard running at:")
    print(f"   👉 http://{host}:{port}")
    print(f"========================================================")
    print("Real-time migration progress, pause/cancel, and cutover UI ready.")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping SchemaMigrate Web Dashboard.")
        server.server_close()


if __name__ == "__main__":
    start_server()
