import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .collectors import CONFIG, hardware_information, info_payload, status_payload
from .logging_utils import log
from .state import STATE


class Handler(BaseHTTPRequestHandler):
    def authorised(self):
        token = str(CONFIG.get("api_token", "")).strip()
        return not token or self.headers.get("Authorization", "") == f"Bearer {token}"

    def send_json(self, code, payload):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.authorised():
            return self.send_json(401, {"error": "Unauthorized"})
        path = self.path.split("?", 1)[0]
        if path in ("/", "/status"):
            return self.send_json(200, status_payload())
        if path == "/hardware":
            return self.send_json(200, hardware_information(force=True))
        if path == "/health":
            hardware = hardware_information()
            return self.send_json(200, {"ok": True, "iperf3": STATE.iperf_running(), "hardware": bool(hardware.get("available")), "version": STATE.version})
        if path == "/info":
            return self.send_json(200, info_payload())
        return self.send_json(404, {"error": "Not found"})

    def log_message(self, format_string, *args):
        log(f"HTTP {self.client_address[0]} {format_string % args}")


def serve() -> None:
    server = ThreadingHTTPServer((CONFIG["bind_address"], int(CONFIG["agent_port"])), Handler)
    server.timeout = 1
    log(f"API listening on {CONFIG['bind_address']}:{CONFIG['agent_port']}")
    try:
        while not STATE.stop.is_set():
            server.handle_request()
    finally:
        server.server_close()
