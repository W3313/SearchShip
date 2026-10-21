"""Run the same UI in a normal browser (handy for development, or if the
desktop window cannot start). Serves on 127.0.0.1 only.

Requests must carry an X-SearchShip header. Browsers only send a custom
header after a CORS preflight, which this server never approves, so other
websites cannot call the API.
"""
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .paths import resource

MARKER = "<!--SEARCHSHIP_BRIDGE-->"


def make_handler(api):
    public = {n for n in dir(api) if not n.startswith("_") and callable(getattr(api, n))}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.split("?")[0] not in ("/", "/index.html"):
                return self._send(404, "not found", "text/plain")
            html = resource("tracker", "ui", "index.html").read_text(encoding="utf-8")
            html = html.replace(MARKER, "<script>window.SEARCHSHIP_HTTP_API = true;</script>", 1)
            self._send(200, html, "text/html")

        def do_POST(self):
            name = self.path[len("/api/"):] if self.path.startswith("/api/") else ""
            if self.headers.get("X-SearchShip") != "1" or name not in public:
                return self._send(403, json.dumps({"error": "forbidden"}))
            try:
                length = int(self.headers.get("Content-Length") or 0)
                args = json.loads(self.rfile.read(length) or b"[]")
                if not isinstance(args, list):
                    raise ValueError
            except ValueError:
                return self._send(400, json.dumps({"error": "bad request"}))
            result = getattr(api, name)(*args)
            self._send(200, json.dumps(result, ensure_ascii=False))

    return Handler


def serve(api, port=8765, open_browser=True):
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(api))
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"SearchShip running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
