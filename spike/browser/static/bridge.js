// The main page's side of the engine's synchronous calls. The engine (Python, in its worker)
// calls OCR and the barcode reader as ordinary functions; its worker posts the work here and
// waits on shared memory; the work runs here (OCR on a pool of Tesseract workers, one per
// core, up to 6) and the answer is written back, which wakes the engine.
"use strict";

const BRIDGE = (() => {
  const SIZE = 64 * 2 ** 20;  // the longest answer: a batch of pages' OCR, as text
  const ctrl = new Int32Array(new SharedArrayBuffer(8));  // [state: 0 waiting / 1 answered, length]
  const data = new Uint8Array(new SharedArrayBuffer(SIZE));
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

  function ocrPool() {
    if (!pool) {
      const n = Math.max(1, Math.min(6, (navigator.hardwareConcurrency || 2) - 1));
      pool = Promise.all(Array.from({ length: n }, () => Tesseract.createWorker("eng", 1, {
        workerPath: "ocr/worker.min.js", corePath: "ocr/", langPath: "ocr/", gzip: false,
        workerBlobURL: false, cacheMethod: "none",  // nothing kept in the browser
      })));
    }
    return pool;
  }

  // jobs: [{psm, png}] -> each one's TSV (or text), in order; psm "vision" is the second
  // engine's read (Paddle, standing in for the Mac's Vision), at the same time as Tesseract's
  async function ocr({ psms, tsv }, blobs) {
    const out = new Array(psms.length);
    const second = [];
    psms.forEach((psm, i) => { if (psm === "vision") second.push(i); });
    const paddleDone = (async () => {
      for (const i of second) {
        const t = await paddleRead(blobs[i]);
        out[i] = tsv ? t : t.split("\n").slice(1).map((r) => r.split("\t")[11] || "").join(" ");
      }
    })();
    const todo = psms.map((_, i) => i).filter((i) => psms[i] !== "vision");
    const workers = todo.length ? await ocrPool() : [];
    let next = 0;
    const tessDone = Promise.all(workers.map(async (w) => {
      while (next < todo.length) {
        const i = todo[next++];
        await w.setParameters({ tessedit_pageseg_mode: psms[i] });
        const r = await w.recognize(new Blob([blobs[i]], { type: "image/png" }), {}, { tsv, text: !tsv });
        let t = tsv ? r.data.tsv : r.data.text;
        if (tsv && !t.startsWith("level")) t = HEADER + "\n" + t;  // as the command-line tool gives it
        out[i] = t;
      }
    }));
    await Promise.all([paddleDone, tessDone]);
    return out;
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

  async function answer(worker, { kind, payload, blobs }) {
    let result;
    try {
      result = { ok: await ({ ocr, barcodes })[kind](JSON.parse(payload), blobs) };
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

  return { ctrl, data, answer };
})();
