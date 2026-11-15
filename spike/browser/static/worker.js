// The engine, off the page's thread: Pyodide, PyMuPDF and Taxonymous's own Python, from this site.
"use strict";
importScripts("pyodide/pyodide.js");

// the engine's synchronous calls (OCR, barcodes): post the work to the page and wait on
// shared memory until it answers (bridge.js); a worker may wait, the page never does
let ctrl = null;
let data = null;
self.syncCall = (kind, payload, blobs) => {
  if (!ctrl) throw new Error("the page didn't give the engine its shared memory");
  Atomics.store(ctrl, 0, 0);
  const copies = blobs ? Array.from(blobs, (b) => new Uint8Array(b)) : [];
  postMessage({ type: "call", kind, payload, blobs: copies }, copies.map((b) => b.buffer));
  Atomics.wait(ctrl, 0, 0);
  const text = new TextDecoder().decode(data.slice(0, Atomics.load(ctrl, 1)));
  const r = JSON.parse(text);
  if (r.error) throw new Error(r.error);
  return JSON.stringify(r.ok);
};

const ready = (async () => {
  const t0 = performance.now();
  const py = await loadPyodide({ indexURL: new URL("pyodide/", location.href).href });
  await py.loadPackage(["pymupdf", "numpy", "pillow"]);
  const manifest = await (await fetch("manifest.json")).json();
  py.FS.mkdirTree("/engine/taxonymous");
  for (const f of manifest.files.filter((x) => x.path.startsWith("engine/"))) {
    py.FS.writeFile("/" + f.path, await (await fetch(f.path)).text());
  }
  py.runPython("import sys; sys.path.insert(0, '/engine')");
  const run = py.pyimport("run");
  postMessage({ type: "ready", seconds: ((performance.now() - t0) / 1000).toFixed(1),
                pymupdf: py.runPython("import pymupdf; pymupdf.__version__") });
  return { py, run };
})().catch((e) => postMessage({ type: "fatal", error: String(e) }));

onmessage = async (e) => {
  if (e.data.type === "shared") { ctrl = e.data.ctrl; data = e.data.data; return; }
  const { py, run } = await ready;
  const { id, kind, profile, vault, name, bytes, fingerprint, decisions } = e.data;
  let result;
  try {
    if (kind === "pages") {  // the review view's page images
      const r = run.pages(py.toPy(bytes));
      result = r.toJs({ dict_converter: Object.fromEntries });
      r.destroy();
      postMessage({ id, result }, result.map((p) => p.png.buffer));
      return;
    }
    const r = run.run(profile, vault || "", name, py.toPy(bytes), fingerprint, decisions || "");
    result = r.toJs({ dict_converter: Object.fromEntries });
    r.destroy();
  } catch (err) {
    result = { name, error: `The engine stopped: ${String(err).split("\n").slice(-2).join(" ")}` };
  }
  postMessage({ id, result }, result.pdf ? [result.pdf.buffer] : []);
};
