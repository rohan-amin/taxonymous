// An engine, off the page's thread: Pyodide, PyMuPDF and Taxonymous's own Python, from this site.
//   worker.js?role=home  the app's server (home.py): answers the UI's requests
//   worker.js?role=lane  a cleaning lane (lane.py): cleans the documents it's given
"use strict";
importScripts("pyodide/pyodide.js");
const ROLE = new URL(location.href).searchParams.get("role") || "lane";

// the engine's synchronous calls (OCR, barcodes, the vault's lock): post the work to the page
// and wait on shared memory until it answers (bridge.js); a worker may wait, the page never does
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

// home: a job for a lane goes to the page, which gives it to a free lane and brings back its outputs
let nextJob = 0;
const jobs = new Map();
self.laneJob = (payload, files) => new Promise((resolve, reject) => {
  const id = nextJob++;
  jobs.set(id, { resolve, reject });
  const out = {};
  for (const [k, v] of files) out[k] = v;
  postMessage({ type: "lane_job", id, payload, files: out }, Object.values(out).map((b) => b.buffer));
});

const PACKAGES = ["pymupdf", "numpy", "pillow"].concat(ROLE === "home" ? ["pydantic", "anyio"] : []);

const ready = (async () => {
  const t0 = performance.now();
  const py = await loadPyodide({ indexURL: new URL("pyodide/", location.href).href });
  await py.loadPackage(PACKAGES);
  const manifest = await (await fetch("manifest.json")).json();
  py.FS.mkdirTree("/engine/taxonymous");
  py.FS.mkdirTree("/engine/wheels");
  for (const f of manifest.files.filter((x) => x.path.startsWith("engine/"))) {
    const res = await fetch(f.path);
    py.FS.writeFile("/" + f.path, f.path.endsWith(".whl") ? new Uint8Array(await res.arrayBuffer()) : await res.text());
  }
  // the server's own libraries (pure Python, pinned as the app pins them): unpacked first on the path
  py.runPython(`
import sys, zipfile, pathlib
if ${ROLE === "home" ? "True" : "False"}:
    for w in sorted(pathlib.Path("/engine/wheels").glob("*.whl")):
        zipfile.ZipFile(w).extractall("/engine/lib")
    sys.path.insert(0, "/engine/lib")
sys.path.insert(0, "/engine")
`);
  const mod = py.pyimport(ROLE === "home" ? "home" : "lane");
  return { py, mod, seconds: ((performance.now() - t0) / 1000).toFixed(1),
           pymupdf: py.runPython("import pymupdf; pymupdf.__version__") };
})();

ready.then((r) => postMessage({ type: "loaded", seconds: r.seconds, pymupdf: r.pymupdf }))
  .catch((e) => postMessage({ type: "fatal", error: String(e) }));

const toObj = { dict_converter: Object.fromEntries };

onmessage = async (e) => {
  const m = e.data;
  if (m.type === "shared") { ctrl = m.ctrl; data = m.data; return; }
  const { py, mod } = await ready;
  try {
    if (m.type === "start") {  // home: the workspace, with the open file's profile
      py.FS.mkdirTree("/ws/vault");
      if (m.profile) py.FS.writeFile("/ws/vault/profile.json", m.profile);
      mod.start(m.token, m.lanes);
      postMessage({ type: "started" });
    } else if (m.type === "clean") {  // home: guided setup's details, as the app keeps them, and what to fix
      postMessage({ type: "file", id: m.id, data: mod.clean_profile(m.profile) });
    } else if (m.type === "request") {
      const [status, headers, body] = await mod.request(m.method, m.path, m.query, JSON.stringify(m.headers),
                                                        m.body ? new Uint8Array(m.body) : undefined);
      const bytes = new Uint8Array(body);
      postMessage({ type: "response", id: m.id, status, headers: JSON.parse(headers), body: bytes.buffer }, [bytes.buffer]);
    } else if (m.type === "progress") {
      mod.progress(m.id, m.progress);
    } else if (m.type === "lane_done") {
      const j = jobs.get(m.id);
      jobs.delete(m.id);
      // as home.py takes it (a dict): what's missing left out, not null (Python would see JsNull)
      const outputs = Object.fromEntries(Object.entries(m.result.outputs).filter(([, v]) => v));
      j.resolve(m.result.error ? { outputs, error: m.result.error } : { outputs });
    } else if (m.type === "write") {  // home: a file the page restores (the boxes you drew, from your file)
      const path = "/ws/" + m.path;
      py.FS.mkdirTree(path.slice(0, path.lastIndexOf("/")));
      py.FS.writeFile(path, new Uint8Array(m.data));
      postMessage({ type: "file", id: m.id, data: null });
    } else if (m.type === "read") {  // home: a file of the workspace, for the page (saving your file)
      const path = "/ws/" + m.path;
      const exists = py.FS.analyzePath(path).exists;
      postMessage({ type: "file", id: m.id, data: exists ? py.FS.readFile(path) : null });
    } else if (m.type === "calibrate") {  // lane: how fast this computer cleans (lane.py calibrate)
      postMessage({ type: "calibrated", data: mod.calibrate() });
    } else if (m.type === "speed") {  // home: that, for the time left
      mod.set_speed(m.speed);
    } else if (m.type === "job") {  // lane: clean or check one document
      const r = mod.run(m.payload, m.files);
      const result = r.toJs(toObj);
      r.destroy();
      const buffers = Object.values(result.outputs).filter(Boolean).map((b) => b.buffer);
      postMessage({ type: "done", id: m.id, result }, buffers);
    }
  } catch (err) {
    if (m.type === "request") {
      const body = new TextEncoder().encode(JSON.stringify({ detail: `The engine stopped: ${String(err).split("\n").slice(-2).join(" ")}` }));
      postMessage({ type: "response", id: m.id, status: 500, headers: [["content-type", "application/json"]], body: body.buffer }, [body.buffer]);
    } else if (m.type === "job") {
      postMessage({ type: "done", id: m.id, result: { outputs: {}, error: `The engine stopped: ${String(err).split("\n").slice(-2).join(" ")}` } });
    } else if (m.type === "calibrate" || m.type === "speed") {
      postMessage({ type: "calibrated", data: null });  // no estimate: the time left comes from measuring
    } else {
      postMessage({ type: "fatal", error: String(err) });
    }
  }
};
