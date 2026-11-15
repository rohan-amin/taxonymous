"""Serve spike/browser/dist on http://127.0.0.1:8800, with the same headers Cloudflare Pages
would send (from dist/_headers), so the page runs under its real content-security policy.

    uv run python spike/browser/serve.py [PORT]
"""

import functools
import http.server
import sys
from pathlib import Path

DIST = Path(__file__).parent / "dist"


def headers() -> list[tuple[str, str]]:
    out = []
    for line in (DIST / "_headers").read_text().splitlines():
        if line.startswith("  ") and ":" in line:
            k, v = line.strip().split(":", 1)
            out.append((k.strip(), v.strip()))
    return out


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".wasm": "application/wasm",
                      ".js": "text/javascript", ".webmanifest": "application/manifest+json"}

    def end_headers(self):
        for k, v in headers():
            self.send_header(k, v)
        super().end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8800
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Handler, directory=DIST))
    print(f"http://127.0.0.1:{port}  (Ctrl-C to stop)")
    server.serve_forever()
