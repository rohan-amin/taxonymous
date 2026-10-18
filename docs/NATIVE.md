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
| torch (for the name model)          | 549 MB  |
| en_core_web_trf (the name model)    | 482 MB  |
| PyMuPDF                             | 58 MB   |
| Tesseract 5.5 + its dylibs + eng    | ~50 MB  |
| everything else                     | ~0.3 GB |

So the app will weigh about 1.1–1.4 GB either way, and Tauri's small shell doesn't
help. Both routes need the same work:

1. **Bundle Tesseract.** Today it comes from Homebrew. Its binary, the leptonica
   and image-library dylibs, and `eng.traineddata` have to be copied into the
   app, with their library paths rewritten.
2. **Bundle the name model.** Load it from inside the app rather than from
   site-packages.
3. **Sign and notarize** with an Apple Developer ID. Without this, Gatekeeper
   blocks a downloaded app.
4. **Optional, for size:** a small spaCy model (`en_core_web_sm`, ~13 MB, no
   torch) brings the bundle to roughly 0.3 GB, but it misses more names in
   prose. That would need measuring on the held-out corpus before choosing it.

## Recommendation

**pywebview.** It needs no new toolchain, runs the engine in the same process as
the window, and the spike works today. Tauri's advantages (auto-update, richer
native menus, a smaller shell) don't outweigh adding Rust and a sidecar for an app
whose size is set by torch. If auto-update becomes important, Sparkle can be added
to a py2app bundle.

The security model is unchanged: the server is loopback-only and needs the token,
and the window gets no JavaScript bridge into Python. The window is just another
client of the API.
