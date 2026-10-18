"""Spike (M7): the review UI in a native macOS window instead of a browser tab.

    uv run --with pywebview python -m tools.native_window --workspace DIR

Same local API and token as `taxonymous serve`; pywebview shows it in a
WKWebView window, and closing the window stops the server. No JavaScript
bridge is exposed to the page. See docs/NATIVE.md.
"""

import argparse
import secrets
import socket
import threading
import time
from pathlib import Path

import uvicorn
import webview

from taxonymous.api import UI_DIR, create_app
from taxonymous.pipeline import Workspace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    if not (UI_DIR / "index.html").exists():
        raise SystemExit("build the UI first: cd ui && npm install && npm run build")

    token = secrets.token_urlsafe(32)
    app = create_app(Workspace(args.workspace), token)
    with socket.socket() as s:  # a free loopback port for this run
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)

    webview.create_window("Taxonymous", f"http://127.0.0.1:{port}/?token={token}", width=1400, height=900)
    webview.start()
    server.should_exit = True


if __name__ == "__main__":
    main()
