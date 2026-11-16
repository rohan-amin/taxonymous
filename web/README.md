# Taxonymous in the browser

The whole app inside a web page: the workspace UI (`ui/`) over the app's own Python (the
engine and its server, `src/taxonymous`) running in Pyodide. A one-time, checked download,
then everything works offline, and nothing you add leaves the computer.

    uv run python web/build.py      # builds the UI, fetches pinned files once, builds dist/
    uv run python web/serve.py      # then open http://127.0.0.1:8800

Deploy (Cloudflare Pages, static; `_headers` carries the content-security policy):

    npx wrangler pages deploy web/dist --project-name taxonymous --branch main   # https://taxonymous.pages.dev

The site's root is the landing page (`landing/`); the app is at `app/`, with its service worker
scoped there. `landing/sw.js` only exists for browsers set up before the app moved: it clears
that old install and unregisters itself.

## How it fits together

- **The page** (`static/boot.js`): setup, your Taxonymous file and the engines, as the UI's
  "host" (`ui/src/workspace/host.ts`): it reports what stage things are at and does what the UI
  asks. Everything you see is the workspace UI: its start screen (`Start.tsx`), then the
  workspace with your file in the top bar (`FileControls.tsx`). The UI talks to "the server" as it always does (`/api/...`): the service
  worker (`static/sw.js`) hands each request to the page, and the page to the home engine.
- **The home engine** (`engine/home.py`, a worker): the app's server, `taxonymous.api`
  unchanged, over a workspace in memory, each request run as an ASGI call. FastAPI and
  Starlette are bundled as wheels at the versions the app pins. No threads in a browser
  worker: the app's job queue runs as tasks on the worker's event loop.
- **Cleaning lanes** (`engine/lane.py`, a worker each, as many as the app would use): a job
  takes the files it reads (profile, document, boxes, outputs to check again) to a lane and
  brings the document's outputs back.
- **OCR, barcodes, the vault's lock** (`engine/bridge.py`, `static/bridge.js`): synchronous
  calls from an engine to the page, which waits on shared memory (cross-origin isolation:
  `_headers`, and the service worker's headers on what it serves). Tesseract (tesseract.js,
  the same tessdata_fast model as natively) on a pool shared by every engine; PaddleOCR v5
  (`static/paddle-worker.js`, on the graphics card through WebGPU when there is one) in the
  Mac's Vision's place; ZXing (zxing-wasm) for barcodes.
- **The vault** is held by the page and lent to one engine at a time, as the app's lanes
  share the vault file: a value gets the same label whichever engine sees it first.

## Your Taxonymous file

Your profile and the vault (each label and the real value it stands for) live in one file
you keep where you like (`static/keyfile.js`). The page reads it into memory when you open
it. In Chrome and Edge it then saves itself in place a moment after anything in it changes
(a new file once you've chosen where it goes with Save…; an opened one once you've let the
page write to it, which Chrome asks as you open it), and the top bar says "Saved". In Safari
and Firefox a save is a download, so you save when you choose, and the bar says when you last
did. Nothing is kept in the browser (no localStorage, IndexedDB or file
system storage; the cache holds only the app's own files). By default it's protected with a
passphrase: AES-256-GCM, the key from PBKDF2-SHA-256 with 600,000 rounds, all WebCrypto.

Documents are only ever in the page: close it and they're gone (your originals are wherever
they were).

## Measured (this Mac, Chrome)

The synthetic corpus (35 documents) cleaned and checked: 35 of 35 pass, labels as the Mac
app's; 125 s with two lanes (the Mac app: 64 s). PaddleOCR alone missed details Tesseract
caught, and docTR in Tesseract's place was no faster; the time left is mostly checking every
page with the second engine on the graphics card.
