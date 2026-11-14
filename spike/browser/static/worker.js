// The engine, off the page's thread: Pyodide, PyMuPDF and Taxonymous's own Python, from this site.
"use strict";
importScripts("pyodide/pyodide.js");

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
  const { py, run } = await ready;
  const { id, profile, name, bytes } = e.data;
  let result;
  try {
    const r = run.run(profile, name, py.toPy(bytes));
    result = r.toJs({ dict_converter: Object.fromEntries });
    r.destroy();
  } catch (err) {
    result = { name, error: `The engine stopped: ${String(err).split("\n").slice(-2).join(" ")}` };
  }
  postMessage({ id, result }, result.pdf ? [result.pdf.buffer] : []);
};
