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

What's in dist/: the page, a service worker (offline), a worker running the engine, the
pinned Pyodide runtime and packages, the engine's own Python from src/taxonymous, and the
synthetic sample documents. manifest.json lists every downloaded file with its SHA-256;
the page checks each before keeping it.

Measured (M4, single core, no OCR): the same as native within ~10-40%; engine start ~1.8s;
first setup 35.6 MB.
