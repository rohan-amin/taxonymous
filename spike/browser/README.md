# Taxonymous in the browser (spike)

The engine (Python, PyMuPDF) running inside a web page via Pyodide and WebAssembly: a
one-time, checked download, then everything works offline. OCR is Tesseract (tesseract.js,
the same engine and tessdata_fast model as natively, on a pool of workers) and barcodes are
ZXing (zxing-wasm), reached from the engine through a synchronous bridge: the engine's
worker posts the work to the page and waits on shared memory (worker.js syncCall,
bridge.js), so the engine code is unchanged. That needs cross-origin isolation (the
COOP/COEP headers in _headers, and added by the service worker to what it serves).
Not yet: a second OCR engine (the Mac app's Vision), which catches what Tesseract misreads.

Run it locally (in your own browser):

    uv run python spike/browser/build.py      # fetches pinned Pyodide files once, builds dist/
    uv run python spike/browser/serve.py      # then open http://127.0.0.1:8800

Deploy it (Cloudflare Pages, static; `_headers` carries the content-security policy):

    npx wrangler login
    npx wrangler pages deploy spike/browser/dist --project-name taxonymous-spike

Your Taxonymous file: your profile and the vault (each label and the real value it stands
for) live in one file you keep where you like (keyfile.js). The page reads it into memory when
you open it and writes it only when you choose Save: in place in Chrome and Edge, as a
download in Safari and Firefox. Nothing is kept in the browser (no localStorage, IndexedDB or
file system storage; the cache holds only the app's own files). By default the file is
protected with a passphrase: AES-256-GCM, the key from PBKDF2-SHA-256 with 600,000 rounds,
all WebCrypto; a wrong passphrase or a changed file fails to open. Unprotected is an option,
and the page says what that means.

Documents stay separate: added for a session, safe copies downloaded. What you decided on a
document (the boxes you drew) is kept in the Taxonymous file under the document's fingerprint,
the SHA-256 of its bytes: no names, no contents. Add the same file again (to the byte, any
name) and its boxes come back, with a note saying so; a re-downloaded copy whose bytes differ
starts fresh, since a box is a position and a shifted layout would put it in the wrong place.

What's in dist/: the page, a service worker (offline), a worker running the engine, the
pinned Pyodide runtime and packages, the engine's own Python from src/taxonymous, and the
synthetic sample documents. manifest.json lists every downloaded file with its SHA-256;
the page checks each before keeping it.

Measured (M4): the whole synthetic corpus, Tesseract only, cleaned and checked with OCR:
35 of 35 pass with labels identical to native; 140 s in the browser, 74 s natively (1.9x).
Engine start ~1.8 s; first setup 48.8 MB.
