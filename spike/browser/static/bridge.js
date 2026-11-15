// The main page's side of the engines' synchronous calls. An engine (Python, in its worker)
// calls OCR, the barcode reader and the vault's lock as ordinary functions; its worker posts
// the work here and waits on shared memory of its own (a channel); the work runs here (OCR
// on a pool of Tesseract workers, one per core, up to 6, shared by every engine, a read at a
// time each) and the answer is written back, which wakes that engine.
"use strict";

const BRIDGE = (() => {
  const SIZE = 64 * 2 ** 20;  // the longest answer: a batch of pages' OCR, as text
  // one per engine: [state: 0 waiting / 1 answered, length], and the answer
  const channel = () => ({ ctrl: new Int32Array(new SharedArrayBuffer(8)), data: new Uint8Array(new SharedArrayBuffer(SIZE)) });
  const handlers = {};  // more calls the page answers (the vault's lock: app.js)
  const HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext";
  let pool = null;
  let zxing = null;
  let paddle = null;  // the second engine (paddle-worker.js), one page at a time
  let paddleNext = 0;
  const paddleWaiting = new Map();

  function paddleRead(png) {
    if (!paddle) {
      paddle = new Worker("paddle-worker.js");
      paddle.onmessage = (e) => {
        const { id, tsv, error } = e.data;
        const w = paddleWaiting.get(id);
        paddleWaiting.delete(id);
        if (error) w.reject(new Error(error)); else w.resolve(tsv);
      };
    }
    return new Promise((resolve, reject) => {
      const id = paddleNext++;
      paddleWaiting.set(id, { resolve, reject });
      paddle.postMessage({ id, png }, [png.buffer]);
    });
  }

  // Tesseract: every engine's reads in one queue, each worker taking the next when it's free
  // (a worker's layout mode is set before each read, so it only ever has one)
  const tessQueue = [];
  function ocrPool() {
    if (!pool) {
      const n = Math.max(1, Math.min(6, (navigator.hardwareConcurrency || 2) - 1));
      pool = Promise.all(Array.from({ length: n }, () => Tesseract.createWorker("eng", 1, {
        workerPath: "ocr/worker.min.js", corePath: "ocr/", langPath: "ocr/", gzip: false,
        workerBlobURL: false, cacheMethod: "none",  // nothing kept in the browser
      }))).then((ws) => { ws.forEach((w) => { w.idle = true; }); return ws; });
    }
    return pool;
  }
  async function pump() {
    for (const w of await ocrPool()) {
      if (!w.idle || !tessQueue.length) continue;
      const job = tessQueue.shift();
      w.idle = false;
      (async () => {
        try {
          await w.setParameters({ tessedit_pageseg_mode: job.psm });
          const r = await w.recognize(new Blob([job.png], { type: "image/png" }), {}, { tsv: job.tsv, text: !job.tsv });
          let t = job.tsv ? r.data.tsv : r.data.text;
          if (job.tsv && !t.startsWith("level")) t = HEADER + "\n" + t;  // as the command-line tool gives it
          job.resolve(t);
        } catch (e) {
          job.reject(e);
        }
        w.idle = true;
        pump();
      })();
    }
  }
  function tessRead(psm, png, tsv) {
    return new Promise((resolve, reject) => { tessQueue.push({ psm, png, tsv, resolve, reject }); pump(); });
  }

  // jobs: [{psm, png}] -> each one's TSV (or text), in order; psm "vision" is the second
  // engine's read (Paddle, standing in for the Mac's Vision), at the same time as Tesseract's
  async function ocr({ psms, tsv }, blobs) {
    const asText = (t) => (tsv ? t : t.split("\n").slice(1).map((r) => r.split("\t")[11] || "").join(" "));
    return Promise.all(psms.map(async (psm, i) => (
      psm === "vision" ? asText(await paddleRead(blobs[i])) : tessRead(psm, blobs[i], tsv))));
  }

  async function barcodes({ formats }, blobs) {
    if (!zxing) {
      zxing = ZXingWASM;
      zxing.prepareZXingModule({ overrides: { locateFile: (f) => `barcode/${f}` } });
    }
    const found = await zxing.readBarcodes(new Blob([blobs[0]], { type: "image/png" }),
                                           { formats, tryHarder: true, maxNumberOfSymbols: 32 });
    return found.filter((r) => r.isValid).map((r) => r.position);
  }

  async function answer({ ctrl, data }, { kind, payload, blobs }) {
    let result;
    try {
      result = { ok: await ({ ocr, barcodes, ...handlers })[kind](JSON.parse(payload), blobs) };
    } catch (e) {
      result = { error: String(e) };
    }
    const bytes = new TextEncoder().encode(JSON.stringify(result));
    if (bytes.length > SIZE) throw new Error("answer too long for the bridge");
    data.set(bytes);
    Atomics.store(ctrl, 1, bytes.length);
    Atomics.store(ctrl, 0, 1);
    Atomics.notify(ctrl, 0);
  }

  return { channel, answer, handlers };
})();
