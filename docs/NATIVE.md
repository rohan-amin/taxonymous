# Native Mac app: pywebview vs Tauri (M7 spike, 2026-10-18)

**Goal:** wrap the existing review UI in a native Mac app without a rewrite. The
architecture already allows this: Python engine → FastAPI on 127.0.0.1 → React UI.

## What was tried

`tools/native_window.py` starts the same API with a fresh token on a free loopback
port and opens the UI in a pywebview window (WKWebView, the engine Safari uses):

```sh
uv run --with pywebview python -m tools.native_window --workspace DIR
```

A scripted run on k1_statement.pdf went: window open → document list loaded → Run
all → redacted, verified, and 16 boxes shown → window closed and the server
stopped. The page loaded in 0.3–1.4 s, and the token was taken out of the address
as in a browser. No code changes were needed in the engine, API or UI.

Not yet tried in the native window: dragging files in from Finder, and printing.

## Comparison

|                        | pywebview                                  | Tauri                                           |
|------------------------|--------------------------------------------|-------------------------------------------------|
| Web view               | WKWebView                                  | WKWebView (the same)                            |
| Python engine          | runs in the app's own process              | a separate "sidecar" binary (PyInstaller)       |
| New toolchain          | none (pip package; pyobjc)                 | Rust + cargo + Tauri CLI (not installed here)   |
| Packaging              | py2app or PyInstaller → .app               | Tauri bundler + the PyInstaller sidecar         |
| Shell size             | small (pyobjc)                             | ~10 MB                                          |
| Auto-update, signing   | do it ourselves (Sparkle, codesign)        | built in                                        |
| Native menus, dialogs  | basic (file dialogs, menus)                | full                                            |

## The real cost is the Python bundle, whichever wrapper is used

| Dependency                          | Size    |
|-------------------------------------|---------|
| PyMuPDF                             | 58 MB   |
| Tesseract 5.5 + its dylibs + eng    | ~50 MB  |
| everything else (numpy, Pillow, FastAPI, zxing-cpp...) | ~0.1 GB |

The name model (torch and `en_core_web_trf`, about 1 GB) was removed on 2026-10-20.
The runtime packages now come to about 150 MB (measured 2026-10-20, without the test-only
Playwright), so the bundle is roughly 0.2 GB either way. Both routes need the same work:

1. **Bundle Tesseract.** Today it comes from Homebrew. Its binary, the leptonica
   and image-library dylibs, and `eng.traineddata` have to be copied into the
   app, with their library paths rewritten.
2. **Sign and notarize** with an Apple Developer ID. Without this, Gatekeeper
   blocks a downloaded app.

## Recommendation

**pywebview.** It needs no new toolchain, runs the engine in the same process as
the window, and the spike works today. Tauri's advantages (auto-update, richer
native menus, a smaller shell) don't outweigh adding Rust and a sidecar for an app
whose size is set by the Python packages. If auto-update becomes important, Sparkle can be added
to a py2app bundle.

The security model is unchanged: the server is loopback-only and needs the token,
and the window gets no JavaScript bridge into Python. The window is just another
client of the API.
