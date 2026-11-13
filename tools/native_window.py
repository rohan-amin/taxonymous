"""The review UI in a native macOS window instead of a browser tab.

    uv run --with pywebview python -m tools.native_window --workspace DIR
    uv run --with pywebview python -m tools.native_window --workspace DIR --dev   # live UI edits

Same local API and token as `taxonymous serve`; pywebview shows it in a
WKWebView window, and closing the window stops the server. It opens the new
three-panel workspace (`?ui=workspace`); `--classic` opens the old screen.

`--dev` serves the API on 127.0.0.1:8765 and loads the UI from Vite
(`cd ui && npm run dev`), so UI edits show in the window as you save them.

No JavaScript bridge is exposed to the page: the menu bar runs Python, which
only ever sends the page a command name (`tx-command`) or a message to show.
See docs/NATIVE.md.
"""

import argparse
import json
import secrets
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import uvicorn
import webview
from webview.menu import Menu, MenuAction, MenuSeparator

from taxonymous.api import UI_DIR, create_app
from taxonymous.pipeline import Workspace

ACCEPT = ("pdf", "jpg", "jpeg", "png", "tif", "tiff", "heic", "heif", "webp")
STATE = Path.home() / "Library" / "Application Support" / "Taxonymous" / "window.json"
DEV_API_PORT = 8765
DEV_UI = "http://127.0.0.1:5173/"


def dark_mode() -> bool:
    out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True)
    return out.stdout.strip() == "Dark"


def load_frame() -> dict:
    try:
        f = json.loads(STATE.read_text())
        return {k: int(f[k]) for k in ("width", "height", "x", "y") if k in f}
    except (OSError, ValueError, TypeError):
        return {}


def save_frame(window: webview.Window) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        frame = {"width": window.width, "height": window.height, "x": window.x, "y": window.y}
        STATE.write_text(json.dumps(frame))
    except OSError:
        pass  # not remembered: fine


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--dev", action="store_true", help="load the UI from Vite (live edits)")
    parser.add_argument("--classic", action="store_true", help="open the classic review screen")
    args = parser.parse_args()
    if not args.dev and not (UI_DIR / "index.html").exists():
        raise SystemExit("build the UI first: cd ui && npm install && npm run build")

    ws = Workspace(args.workspace)
    ws.init()
    token = secrets.token_urlsafe(32)
    port = DEV_API_PORT if args.dev else free_port()
    config = uvicorn.Config(create_app(ws, token), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)

    base = DEV_UI if args.dev else f"http://127.0.0.1:{port}/"
    url = f"{base}?token={token}" + ("" if args.classic else "&ui=workspace")
    frame = load_frame()
    window = webview.create_window(
        "Taxonymous", url, width=frame.get("width", 1440), height=frame.get("height", 900),
        x=frame.get("x"), y=frame.get("y"), min_size=(960, 600), hidden=True,
        background_color="#1B1C1A" if dark_mode() else "#F6F5F1",
        menu=menus(lambda: window, ws),
    )
    # shown only once the page has drawn, so it opens without a white flash
    window.events.loaded += lambda: window.show()
    window.events.closing += lambda: save_frame(window)
    webview.start()
    server.should_exit = True


def menus(get_window, ws: Workspace) -> list[Menu]:
    def send(command: str):
        return lambda: get_window().evaluate_js(
            f"window.dispatchEvent(new CustomEvent('tx-command', {{detail: {json.dumps(command)}}}))")

    def toast(message: str) -> None:
        get_window().evaluate_js(
            f"window.dispatchEvent(new CustomEvent('tx-toast', {{detail: {json.dumps(message)}}}))")

    def add_documents() -> None:
        picked = get_window().create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True,
            file_types=("Tax documents (" + ";".join(f"*.{e}" for e in ACCEPT) + ")",))
        if not picked:
            return
        added, skipped = 0, []
        for p in map(Path, picked):
            dest = ws.inbox / p.name
            if dest.exists():
                skipped.append(p.name)
                continue
            shutil.copy2(p, dest)
            added += 1
        note = f"Added {added} document{'' if added == 1 else 's'}." if added else ""
        if skipped:
            note += f" You already added {', '.join(skipped)}."
        toast(note.strip())
        send("added")()

    def show_safe_copies() -> None:
        ws.ready.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(ws.ready)], check=False)

    return [
        Menu("File", [
            MenuAction("Add Documents…", add_documents),
            MenuAction("Show Safe Copies in Finder", show_safe_copies),
            MenuSeparator(),
            MenuAction("Safe Copies…", send("safe-copies")),
            MenuAction("My Profile", send("profile")),
        ]),
        Menu("View", [
            MenuAction("Show or Hide Documents   ⌘1", send("toggle-files")),
            MenuAction("Show or Hide My Profile   ⌘2", send("toggle-profile")),
            MenuSeparator(),
            MenuAction("Original", send("original")),
            MenuAction("Safe Copy", send("safe")),
            MenuSeparator(),
            MenuAction("Zoom In   ⌘+", send("zoom-in")),
            MenuAction("Zoom Out   ⌘−", send("zoom-out")),
            MenuAction("Actual Size   ⌘0", send("zoom-reset")),
        ]),
        Menu("Go", [
            MenuAction("Next Document   ⌘]", send("next")),
            MenuAction("Previous Document   ⌘[", send("prev")),
        ]),
        Menu("Help", [MenuAction("How Taxonymous Works", send("welcome"))]),
    ]


if __name__ == "__main__":
    main()
