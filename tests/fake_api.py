"""A tiny local HTTP server that impersonates Open-Meteo, Transitous and LTA.

Tests register a reply per URL path, start the real MCP server pointed at
this fake (via environment variables), and then inspect which requests
the MCP server made.
"""

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

# URL paths the MCP server is pointed at (see conftest.server_env).
GEOCODE = "/geocoding/v1/search"
FORECAST = "/forecast/v1/forecast"
TRANSIT_GEOCODE = "/transitous/api/v1/geocode"
PLAN = "/transitous/api/v5/plan"
STOPTIMES = "/transitous/api/v5/stoptimes"
LTA = "/lta/v3/BusArrival"


@dataclass
class Reply:
    """What the fake API sends back for one request."""
    status: int = 200
    body: bytes = b"{}"
    content_type: str = "application/json"
    delay: float = 0.0


@dataclass
class Request:
    """One request the MCP server made to the fake API."""
    path: str
    query: dict          # single-valued query parameters
    headers: dict


@dataclass
class FakeAPI:
    routes: dict[str, Callable[[dict], Reply]] = field(default_factory=dict)
    requests: list[Request] = field(default_factory=list)

    # ---- registering replies ------------------------------------------
    def json(self, path: str, data, status: int = 200, delay: float = 0.0):
        """Always answer `path` with this JSON."""
        body = json.dumps(data).encode()
        self.routes[path] = lambda _q: Reply(status, body, delay=delay)

    def raw(self, path: str, body: bytes, status: int = 200,
            content_type: str = "text/html"):
        """Answer `path` with raw bytes (e.g. an HTML error page)."""
        self.routes[path] = lambda _q: Reply(status, body, content_type)

    def dynamic(self, path: str, fn: Callable[[dict], object]):
        """Answer `path` with fn(query) encoded as JSON (status 200)."""
        self.routes[path] = lambda q: Reply(body=json.dumps(fn(q)).encode())

    # ---- inspecting requests ------------------------------------------
    def calls(self, path: str) -> list[Request]:
        return [r for r in self.requests if r.path == path]

    # ---- lifecycle ----------------------------------------------------
    def start(self) -> "FakeAPI":
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                api.requests.append(
                    Request(url.path, query, dict(self.headers.items())))
                route = api.routes.get(url.path)
                reply = route(query) if route else Reply(
                    404, b'{"error": "no fake route"}')
                if reply.delay:
                    time.sleep(reply.delay)
                try:
                    self.send_response(reply.status)
                    self.send_header("Content-Type", reply.content_type)
                    self.send_header("Content-Length", str(len(reply.body)))
                    self.end_headers()
                    self.wfile.write(reply.body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # client gave up (timeout tests)

            def log_message(self, *args):
                pass  # keep test output quiet

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address
        return f"http://{host}:{port}"
