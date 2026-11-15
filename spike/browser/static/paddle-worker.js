// The second OCR engine, in place of the Mac's Vision: PaddleOCR v5 (Apache 2.0) run by
// onnxruntime-web, in a worker of its own. A page in, Tesseract-shaped TSV out (a row per
// word; a block per line read), so the engine reads it as it reads Vision's.
//
//   detect: the text map (DB), thresholded; each connected region is a line, grown back to
//           its full size (DB shrinks text when it trains), axis-aligned (forms are upright)
//   read:   each line cropped to 48 px high, read; CTC greedy decoding; each character's
//           position from the step it was read at, so each word gets its own box (the engine
//           then shrinks it to the ink, as it does Vision's)
//
// On the graphics card (WebGPU) when the browser has one, about 4x faster; otherwise the
// processor. The two read the same (a letter's case can differ, which matching ignores).
"use strict";
const ASKED = new URL(location.href).searchParams;
const GPU = !ASKED.has("cpu") && !!self.navigator.gpu;
importScripts("paddle/ort.webgpu.min.js");
ort.env.wasm.wasmPaths = new URL("paddle/", location.href).href;
ort.env.wasm.numThreads = Number(ASKED.get("threads")) || Math.max(1, Math.min(4, (navigator.hardwareConcurrency || 2) - 1));
const REC = ASKED.get("rec") || "rec.onnx";

const MAX_SIDE = 1600;  // the detector's input: big enough for form-sized print on a page
const THRESH = 0.3, BOX_SCORE = 0.6, UNCLIP = 1.5;
// a line is read stretched this much wider than it is: narrow repeated characters ("1111")
// get more of the recognizer's steps each, and aren't read as one fewer
let STRETCH = 1.5;
const MEAN = [0.406, 0.456, 0.485], STD = [0.225, 0.224, 0.229];  // BGR, as PaddleOCR reads images
const HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext";

const ready = (async () => {
  const opts = { executionProviders: GPU ? ["webgpu", "wasm"] : ["wasm"], graphOptimizationLevel: "all" };
  const [det, rec, dict] = await Promise.all([
    ort.InferenceSession.create("paddle/det.onnx", opts),
    ort.InferenceSession.create("paddle/" + REC, opts),
    fetch("paddle/dict.txt").then((r) => r.text()),
  ]);
  const lines = dict.replace(/\r/g, "").split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  // CTC's blank is class 0: this file's first line is empty and stands for it (another dict may not)
  if (lines[0] !== "") lines.unshift("");
  return { det, rec, chars: [...lines, " "] };  // a space last, as PaddleOCR adds it
})();

async function pixels(png) {
  const bmp = await createImageBitmap(new Blob([png], { type: "image/png" }));
  const c = new OffscreenCanvas(bmp.width, bmp.height);
  c.getContext("2d").drawImage(bmp, 0, 0);
  return { bmp, w: bmp.width, h: bmp.height };
}

function tensor(rgba, w, h, mean, std) {
  const out = new Float32Array(3 * w * h);
  const n = w * h;
  for (let i = 0; i < n; i++) {
    const r = rgba[4 * i] / 255, g = rgba[4 * i + 1] / 255, b = rgba[4 * i + 2] / 255;
    out[i] = (b - mean[0]) / std[0];
    out[n + i] = (g - mean[1]) / std[1];
    out[2 * n + i] = (r - mean[2]) / std[2];
  }
  return new ort.Tensor("float32", out, [1, 3, h, w]);
}

function draw(bmp, sx, sy, sw, sh, w, h) {
  const c = new OffscreenCanvas(w, h);
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, w, h);
  ctx.drawImage(bmp, sx, sy, sw, sh, 0, 0, w, h);
  return ctx.getImageData(0, 0, w, h).data;
}

// the lines on the page, as boxes in its pixels
async function detect(det, img) {
  const s = Math.min(1, MAX_SIDE / Math.max(img.w, img.h));
  const w = Math.max(32, Math.round((img.w * s) / 32) * 32), h = Math.max(32, Math.round((img.h * s) / 32) * 32);
  const input = tensor(draw(img.bmp, 0, 0, img.w, img.h, w, h), w, h, MEAN, STD);
  const out = await det.run({ [det.inputNames[0]]: input });
  const prob = out[det.outputNames[0]].data;
  const seen = new Uint8Array(w * h);
  const boxes = [];
  const stack = [];
  for (let start = 0; start < w * h; start++) {
    if (seen[start] || prob[start] <= THRESH) continue;
    let x0 = w, y0 = h, x1 = 0, y1 = 0, sum = 0, count = 0;
    stack.push(start);
    seen[start] = 1;
    while (stack.length) {
      const p = stack.pop();
      const x = p % w, y = (p - x) / w;
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
      sum += prob[p];
      count++;
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          const nx = x + dx, ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
          const q = ny * w + nx;
          if (!seen[q] && prob[q] > THRESH) { seen[q] = 1; stack.push(q); }
        }
      }
    }
    const bw = x1 - x0 + 1, bh = y1 - y0 + 1;
    if (bw < 3 || bh < 3 || sum / count < BOX_SCORE) continue;
    const grow = (bw * bh * UNCLIP) / (2 * (bw + bh));  // DB's unclip: a shrunk region grown back
    const sx = img.w / w, sy = img.h / h;
    boxes.push({
      x0: Math.max(0, (x0 - grow) * sx), y0: Math.max(0, (y0 - grow) * sy),
      x1: Math.min(img.w, (x1 + 1 + grow) * sx), y1: Math.min(img.h, (y1 + 1 + grow) * sy),
    });
  }
  boxes.sort((a, b) => (Math.abs(a.y0 - b.y0) < 8 ? a.x0 - b.x0 : a.y0 - b.y0));
  return boxes;
}

// the lines, read in batches (sorted by width, padded to the widest in each, as PaddleOCR
// batches): each line's characters, and each one's place across it
let BATCH = 16;  // lines per run: what lets the graphics card help (the processor doesn't care)
let TIMES = {};
async function readAll(rec, chars, img, boxes) {
  const H = 48;
  const lines = boxes.map((box, i) => {
    const bw = box.x1 - box.x0, bh = box.y1 - box.y0;
    return { i, box, bw, W: Math.max(16, Math.min(3200, Math.ceil((STRETCH * H * bw) / bh))) };
  });
  lines.sort((a, b) => a.W - b.W);
  const out = new Array(boxes.length);
  for (let s = 0; s < lines.length; s += BATCH) {
    const batch = lines.slice(s, s + BATCH);
    const Wmax = batch[batch.length - 1].W;
    const n = batch.length;
    const data = new Float32Array(n * 3 * H * Wmax);  // 0 is mid-grey once normalized: the padding
    batch.forEach((ln, k) => {
      const px = draw(img.bmp, ln.box.x0, ln.box.y0, ln.bw, ln.box.y1 - ln.box.y0, ln.W, H);
      const plane = H * Wmax;
      for (let y = 0; y < H; y++) {
        for (let x = 0; x < ln.W; x++) {
          const q = 4 * (y * ln.W + x), o = k * 3 * plane + y * Wmax + x;
          data[o] = (px[q + 2] / 255 - 0.5) / 0.5;  // B
          data[o + plane] = (px[q + 1] / 255 - 0.5) / 0.5;  // G
          data[o + 2 * plane] = (px[q] / 255 - 0.5) / 0.5;  // R
        }
      }
    });
    const res = await rec.run({ [rec.inputNames[0]]: new ort.Tensor("float32", data, [n, 3, H, Wmax]) });
    const t = res[rec.outputNames[0]];
    const [, T, C] = t.dims;
    const p = t.data;
    batch.forEach((ln, k) => {
      const steps = Math.min(T, Math.ceil((T * ln.W) / Wmax));  // past its own width is padding
      const got = [];
      let last = 0;
      for (let i = 0; i < steps; i++) {
        let best = 0, bp = -1;
        const base = (k * T + i) * C;
        for (let c = 0; c < C; c++) if (p[base + c] > bp) { bp = p[base + c]; best = c; }
        if (best !== 0 && best !== last) got.push({ ch: chars[best] ?? "", step: i, conf: bp });
        last = best;
      }
      // a step's place: across the padded width, then back onto the line as it is on the page
      const xOf = (step) => ln.box.x0 + (((step + 0.5) * Wmax) / T / ln.W) * ln.bw;
      out[ln.i] = { got, half: ((Wmax / T) / ln.W) * ln.bw / 2, xOf };
    });
  }
  return out;
}

async function page(png) {
  const { det, rec, chars } = await ready;
  const img = await pixels(png);
  const rows = [HEADER];
  const t0 = performance.now();
  const boxes = await detect(det, img);
  const t1 = performance.now();
  const read = await readAll(rec, chars, img, boxes);
  TIMES = { gpu: GPU, detect: Math.round(t1 - t0), read: Math.round(performance.now() - t1) };
  for (let b = 0; b < boxes.length; b++) {
    const { got, half, xOf } = read[b];
    let word = [];
    let n = 0;
    const flush = () => {
      if (!word.length) return;
      const text = word.map((g) => g.ch).join("");
      const x0 = Math.max(0, xOf(word[0].step) - half * 2), x1 = xOf(word[word.length - 1].step) + half * 2;
      const conf = (word.reduce((a, g) => a + g.conf, 0) / word.length) * 100;
      n++;
      rows.push(["5", "1", b + 1, "1", "1", n, Math.round(x0), Math.round(boxes[b].y0),
                 Math.max(1, Math.round(x1 - x0)), Math.max(1, Math.round(boxes[b].y1 - boxes[b].y0)),
                 conf.toFixed(2), text].join("\t"));
      word = [];
    };
    for (const g of got) {
      if (g.ch === " ") flush(); else word.push(g);
    }
    flush();
  }
  img.bmp.close();
  return rows.join("\n") + "\n";
}

onmessage = async (e) => {
  const { id, png, stretch, batch } = e.data;
  if (stretch) STRETCH = stretch;  // for trying values
  if (batch) BATCH = batch;
  try {
    postMessage({ id, tsv: await page(png), times: TIMES });
  } catch (err) {
    postMessage({ id, error: String(err) });
  }
};
