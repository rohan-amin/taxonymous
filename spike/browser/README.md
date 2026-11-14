# Taxonymous in the browser (spike)

The engine (Python, PyMuPDF) running inside a web page via Pyodide and WebAssembly: a
one-time, checked download, then everything works offline. Text PDFs only: no OCR and no
barcode reading yet (the page says so, and never marks such a document checked).

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

What's in dist/: the page, a service worker (offline), a worker running the engine, the
pinned Pyodide runtime and packages, the engine's own Python from src/taxonymous, and the
synthetic sample documents. manifest.json lists every downloaded file with its SHA-256;
the page checks each before keeping it.

Measured (M4, single core, no OCR): the same as native within ~10-40%; engine start ~1.8s;
first setup 35.6 MB.
