from __future__ import annotations

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
ROUTES = {
    "/": ROOT / "web" / "static" / "index.html",
    "/desktop": ROOT / "web" / "static" / "index.html",
    "/mobile": ROOT / "web" / "static" / "mobile.html",
    "/terminal": ROOT / "web" / "static" / "terminal.html",
    "/vendor/xterm.mjs": ROOT / "node_modules" / "@xterm" / "xterm" / "lib" / "xterm.mjs",
    "/vendor/xterm.css": ROOT / "node_modules" / "@xterm" / "xterm" / "css" / "xterm.css",
    "/vendor/addon-fit.mjs": ROOT / "node_modules" / "@xterm" / "addon-fit" / "lib" / "addon-fit.mjs",
}


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        request_path = urlparse(self.path).path
        path = ROUTES.get(request_path)
        if path is None and request_path.startswith("/static/"):
            path = ROOT / "web" / "static" / request_path.removeprefix("/static/")
        if path is None or not path.is_file() or ROOT not in path.resolve().parents:
            self.send_error(404)
            return
        payload = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".mjs":
            content_type = "text/javascript"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 4173), Handler).serve_forever()
