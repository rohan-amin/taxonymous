// The page: setup (download, check, keep for offline), your Taxonymous file, the engines, and
// the workspace UI, the same one the Mac app shows. The UI draws everything, the start screen
// too; this is its "host" (window.taxonymousHost, ui/src/workspace/host.ts): it says what
// stage things are at and does what the UI asks. The UI talks to "the server" as it always
// does (/api/...); here the service worker hands each request to this page, and this page to
// the home engine (home.py), which runs the app's own server code in memory.
"use strict";

const CACHE_PREFIX = "taxonymous-app-v-";  // one per version; the service worker keeps the page in "taxonymous-app-shell"
let manifest, manifestText, cacheName;

const mb = (n) => (n < 2 ** 20 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 2 ** 20).toFixed(1)} MB`);
const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
const sha256 = async (buf) => hex(await crypto.subtle.digest("SHA-256", buf));

// what the UI shows: a new object on every change (it compares them)
// how fast this computer cleans a page, timed once on two sample pages (kept on this computer)
const SPEED_KEY = "taxonymous-speed";
let speed = null;
try { speed = JSON.parse(localStorage.getItem(SPEED_KEY)); } catch { /* none kept */ }

let state = { stage: "checking", size: "", update: false, progress: null, error: "", file: null, speed };
const listeners = new Set();
function set(patch) {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}
const say = (error) => set({ error });

// ---------------------------------------------------------------- setup

async function start() {
  // a new offline layer took over: start again on it, so the page, the workers and the
  // engine are always one version (an old page talking to a new engine breaks)
  const had = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.addEventListener("controllerchange", () => { if (had) location.reload(); });
  navigator.serviceWorker.addEventListener("message", onServiceWorker);
  await navigator.serviceWorker.register("sw.js");
  await navigator.serviceWorker.ready;
  if (!navigator.serviceWorker.controller) return location.reload();  // the first visit: run under it
  let text;
  try {
    const res = await fetch("manifest.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`manifest.json: ${res.status}`);
    text = await res.text();
    JSON.parse(text);
  } catch (e) {  // the site didn't answer properly (mid-deploy, say): the version set up, as offline
    const current = await currentVersion();
    const kept = current && await (await caches.open(current)).match("manifest.json");
    if (!kept) throw e;
    text = await kept.text();
  }
  manifest = JSON.parse(text);
  manifestText = text;
  cacheName = CACHE_PREFIX + (await sha256(new TextEncoder().encode(text))).slice(0, 16);
  const current = await currentVersion();
  if (current === cacheName && await isSetUp()) return ready();
  const todo = await toDownload(current);
  const size = mb(todo.reduce((a, f) => a + f.size, 0));
  if (current && await caches.has(current)) {  // set up before: an update, done now (nothing of yours is open yet)
    set({ size, update: true });
    setUp().catch(() => {  // offline, or a file that didn't check out: carry on with the version you have
      set({ update: false, progress: null, notice: "Couldn't update just now. Using the version on your computer." });
      ready();
    });
  } else {
    set({ stage: "setup", size });
  }
}

async function currentVersion() {
  const r = await (await caches.open("taxonymous-app-current")).match("version");
  return r ? r.text() : null;
}

async function isSetUp() {  // every file kept, by its whole address (they're kept relative to this page)
  const cache = await caches.open(cacheName);
  const keys = new Set((await cache.keys()).map((r) => new URL(r.url).pathname));
  return manifest.files.every((f) => keys.has(new URL(f.path, location.href).pathname));
}

// what this version needs that the version set up (if any) doesn't already have, by fingerprint
async function toDownload(current) {
  if (!current || !(await caches.has(current))) return manifest.files;
  const old = await (await caches.open(current)).match("manifest.json");
  const had = new Map(old ? (await old.json()).files.map((f) => [f.path, f.sha256]) : []);
  return manifest.files.filter((f) => had.get(f.path) !== f.sha256);
}

// every new or changed file fetched from the site itself (?fresh: never a kept copy), checked
// against its fingerprint and kept; unchanged files are copied from the version set up. A file
// that doesn't match is refused, and the version set up stays in use.
async function setUp() {
  set({ stage: "downloading", error: "", progress: { fraction: 0, text: "Starting…" } });
  const current = await currentVersion();
  const cache = await caches.open(cacheName);
  const todo = new Set((await toDownload(current)).map((f) => f.path));
  const oldCache = current && current !== cacheName && await caches.has(current) ? await caches.open(current) : null;
  const total = manifest.files.filter((f) => todo.has(f.path)).reduce((a, f) => a + f.size, 0) || 1;
  let done = 0;
  for (const f of manifest.files) {
    if (await cache.match(f.path)) { if (todo.has(f.path)) done += f.size; continue; }  // kept from an interrupted setup
    if (!todo.has(f.path) && oldCache) {  // unchanged: the copy already on this computer
      const kept = await oldCache.match(f.path);
      if (kept) { await cache.put(f.path, kept); continue; }
    }
    const res = await fetch(`${f.path}?fresh=${f.sha256.slice(0, 12)}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`${f.path}: ${res.status}`);
    const reader = res.body.getReader();
    const parts = [];
    let got = 0;
    for (;;) {
      const { done: end, value } = await reader.read();
      if (end) break;
      parts.push(value);
      got += value.length;
      set({ progress: { fraction: (done + got) / total, text: `${mb(done + got)} of ${mb(total)}` } });
    }
    const buf = await new Blob(parts).arrayBuffer();
    if (await sha256(buf) !== f.sha256) throw new Error(`${f.path} isn't the file it should be: not kept`);
    await cache.put(f.path, new Response(buf, { headers: { "Content-Type": res.headers.get("Content-Type") || "" } }));
    done += f.size;
  }
  await cache.put("manifest.json", new Response(manifestText, { headers: { "Content-Type": "application/json" } }));
  // this version is the one in use from now on; the one before goes
  await (await caches.open("taxonymous-app-current")).put("version", new Response(cacheName));
  for (const k of await caches.keys()) if (k.startsWith(CACHE_PREFIX) && k !== cacheName) await caches.delete(k);
  if (navigator.storage?.persist) await navigator.storage.persist();  // not cleared to make room
  if (current && current !== cacheName) return location.reload();  // an update: start on the new version
  set({ update: false });
  ready();
}

function ready() {
  set({ stage: "ready", progress: null });
  engines().then(calibrate, () => {});  // starting now, while you open your file (or answer setup's questions)
}

// Once: a page with text and a scanned page, cleaned and checked in a lane of its own (made-up
// details, nothing of yours), so the time left can be told before any document of yours has
// been measured. Skipped while the lane is cleaning something; tried again next time.
let timing = false;
async function calibrate() {
  if (speed || timing) return;
  const lane = lanes[0];
  if (!lane || lane.busy) return;
  timing = true;
  lane.busy = true;
  set({ timing: true });
  try {
    const data = await new Promise((resolve) => {
      const on = (e) => {
        if (e.data.type !== "calibrated") return;
        lane.worker.removeEventListener("message", on);
        resolve(e.data.data);
      };
      lane.worker.addEventListener("message", on);
      lane.worker.postMessage({ type: "calibrate" });
    });
    if (data) {
      speed = { ...JSON.parse(data), at: new Date().toISOString().slice(0, 10) };
      try { localStorage.setItem(SPEED_KEY, JSON.stringify(speed)); } catch { /* kept for this visit only */ }
      tellSpeed();
    }
  } finally {
    timing = false;
    lane.busy = false;
    set({ timing: false, speed });
    nextLane();
  }
}
function tellSpeed() {  // the home engine, once it's started
  if (speed && state.stage === "open") home.worker.postMessage({ type: "speed", speed: JSON.stringify(speed) });
}

async function removeAll() {
  for (const k of await caches.keys()) if (k.startsWith("taxonymous-app-")) await caches.delete(k);
  for (const r of await navigator.serviceWorker.getRegistrations()) await r.unregister();
  location.reload();
}

// ---------------------------------------------------------------- your Taxonymous file
// opened into memory: { profile, vault, decisions }. In Chrome and Edge, once it has a place on
// disk you've let the page write to, it's saved there by itself a moment after it changes. In
// Safari and Firefox a save is a download, so it's saved when you choose.

let session = null;  // { vault, decisions, name, handle, writable, passphrase, dirty, savedAt, saving, error }
const FS = "showOpenFilePicker" in window && !new URLSearchParams(location.search).has("nofs");
const TYPES = [{ description: "Taxonymous file", accept: { "application/json": [".taxonymous"] } }];

function showFile() {
  set({ file: session && { name: session.name, protected: !!session.passphrase, dirty: session.dirty,
                           inPlace: FS && !!session.handle, auto: FS && !!session.handle && session.writable,
                           savedAt: session.savedAt, saving: !!session.saving, error: session.error || "" } });
}

// saving by itself: a moment after the last change, one write at a time
const AUTOSAVE_MS = 2000;
let autoTimer = null;
function autosave() {
  clearTimeout(autoTimer);
  if (!session?.dirty || !FS || !session.handle || !session.writable) return;
  autoTimer = setTimeout(async () => {
    if (!session || session.saving) return autosave();
    try {
      await write();
    } catch (e) {  // shown in the bar, with Save to try again
      if (session) { session.error = e.message; session.writable = false; showFile(); }
    }
  }, AUTOSAVE_MS);
}

// may this page write to the file? asked once, on a click (Chrome asks you the first time)
async function allowWriting(ask) {
  if (!FS || !session?.handle) return false;
  const opts = { mode: "readwrite" };
  let state = await session.handle.queryPermission(opts);
  if (state === "prompt" && ask) state = await session.handle.requestPermission(opts).catch(() => "denied");
  session.writable = state === "granted";
  if (session.writable) session.error = "";
  showFile();
  autosave();
  return session.writable;
}

// "Unsaved changes" means saving now would write something the file doesn't have: your profile,
// a label named, a box drawn or taken away. Not documents added or removed (they're never in
// the file), nor safe copies saved. Worked out again after anything that could change it.
let saved = null;  // the file's contents as last opened or saved, canonical; null: never saved
const canonical = (v) => JSON.stringify(v, (k, x) => x && typeof x === "object" && !Array.isArray(x)
  ? Object.fromEntries(Object.keys(x).sort().map((key) => [key, x[key]])) : x);
async function contents() {
  return { profile: await currentProfile(), vault: session.vault || "", decisions: await currentDecisions() };
}
let checking = null;
let recheckAgain = false;
async function recheck() {
  if (checking) { recheckAgain = true; return checking; }
  checking = (async () => {
    do {
      recheckAgain = false;
      if (!session) break;
      const now = canonical(await contents());
      if (session) session.dirty = saved === null || now !== saved;
      showFile();
      autosave();
    } while (recheckAgain);
  })().finally(() => { checking = null; });
  return checking;
}

// the passphrase: to protect a new file ("create"), or to open a protected one ("open"); the UI asks
const askPass = (mode, name = "", error = "") => window.taxonymousHost.askPass(mode, name, error);

async function newFrom(profileText) {
  const profile = profileText ? JSON.parse(profileText) : null;
  const pass = await askPass("create");
  if (!pass) return;
  session = { vault: null, decisions: {}, name: "my.taxonymous", handle: null, writable: false,
              passphrase: pass.passphrase, dirty: true, savedAt: null };
  await open(profile);
}

// guided setup's last step: where your new file goes (asked first, while it's still your click),
// then, once setup's download is done, the details you typed, as the app keeps them, opened
// and saved there. { errors } (by field, as profile.clean names them) if something needs fixing.
let chosen = null;  // the place picked for a new file, kept while you fix what setup's check found
async function create(profile, passphrase) {
  if (FS && !chosen) {
    try {
      chosen = await window.showSaveFilePicker({ suggestedName: "My taxes.taxonymous", types: TYPES, id: PICKER,
                                                 startIn: "documents" });
    } catch (e) {
      if (e.name === "AbortError") return null;
      throw e;
    }
  }
  await whenReady();
  await engines();
  const checked = JSON.parse(await homeAsk({ type: "clean", profile: JSON.stringify(profile) }));
  if (Object.keys(checked.errors).length) return { errors: checked.errors };
  session = { vault: null, decisions: {}, name: chosen ? chosen.name : "My taxes.taxonymous", handle: chosen,
              writable: !!chosen, passphrase, dirty: true, savedAt: null };
  chosen = null;
  await open(checked.profile);
  await write().catch((e) => { session.error = e.message; showFile(); });  // the bar says so, with Save
  return { name: session.name, downloaded: !FS };
}

// set up on this computer (now, or when setup's download finishes); fails if setup stopped
function whenReady() {
  return new Promise((resolve, reject) => {
    const check = () => {
      if (["ready", "starting", "open"].includes(state.stage)) { listeners.delete(check); resolve(); }
      else if (state.stage === "setup" && state.error) { listeners.delete(check); reject(new Error(state.error)); }
    };
    listeners.add(check);
    check();
  });
}

const PICKER = "taxonymous-file";  // the folder you last used for it, remembered by the browser
async function readPicked() {
  if (FS) {
    const [handle] = await window.showOpenFilePicker({ types: TYPES, id: PICKER, startIn: "documents" });
    const f = await handle.getFile();
    return { text: await f.text(), name: handle.name, handle, modified: f.lastModified };
  }
  return new Promise((resolve) => {
    const input = Object.assign(document.createElement("input"), { type: "file", accept: ".taxonymous,application/json" });
    input.onchange = async () => {
      const f = input.files[0];
      if (f) resolve({ text: await f.text(), name: f.name, handle: null, modified: f.lastModified });
    };
    input.click();
  });
}

async function openFile() {
  const { text, name, handle, modified } = await readPicked();
  let passphrase = "";
  let contents;
  let error = "";
  for (;;) {
    try {
      if (KEYFILE.isProtected(text)) {
        const pass = await askPass("open", name, error);
        if (!pass) return;
        passphrase = pass.passphrase;
      }
      contents = await KEYFILE.open(text, passphrase);
      break;
    } catch (e) {
      if (!KEYFILE.isProtected(text)) throw new Error(`${name} isn't a Taxonymous file this can open (${e.message}).`);
      error = e.message;  // shown in the next prompt
    }
  }
  session = { vault: contents.vault, decisions: contents.decisions || {}, name, handle, writable: false, passphrase,
              dirty: false, savedAt: modified };
  await allowWriting(true).catch(() => false);  // still your click: Chrome asks whether it may save
  if (!["ready", "starting", "open"].includes(state.stage)) {  // a first visit: opens once the download is done
    set({ waiting: name });
    try { await whenReady(); } finally { set({ waiting: "" }); }
  }
  await open(contents.profile);
}

// the profile as the workspace has it now (you may have changed it there)
async function currentProfile() {
  const data = await homeRead("vault/profile.json");
  return data ? JSON.parse(new TextDecoder().decode(data)) : null;
}

// what you decided on each document (the boxes you drew, what you kept or showed), by its
// fingerprint: the SHA-256 of its bytes. No names, no contents: add the same file again (to the
// byte, any name) and they come back. Documents not open now keep what the file had for them.
const inboxPath = (d) => `inbox/${d.folder ? d.folder + "/" : ""}${d.name}`;
const fingerprints = new Map();  // a document's id -> its fingerprint, while it's here
async function currentDecisions() {
  const out = { ...session.decisions };
  const headers = { "X-Taxonymous-Token": sessionStorage.getItem("taxonymous-token") || "" };
  for (const d of await (await fetch("/api/documents", { headers })).json()) {
    if (!fingerprints.has(d.id)) fingerprints.set(d.id, await sha256(await homeRead(inboxPath(d))));
    const fp = fingerprints.get(d.id);
    const ov = await homeRead(`vault/overrides/${d.id}.json`);
    const decided = ov && JSON.parse(new TextDecoder().decode(ov));
    if (decided && Object.values(decided).some((v) => Array.isArray(v) ? v.length : v && Object.keys(v).length)) {
      out[fp] = decided;
    } else {
      delete out[fp];
    }
  }
  return out;
}

// a document just added: what your file has for it, put back before it's cleaned
async function restoreDecisions(d) {
  const data = await homeRead(inboxPath(d));
  const decided = data && session?.decisions[await sha256(data)];
  if (!decided) return;
  const bytes = new TextEncoder().encode(JSON.stringify(decided));
  await homeAsk({ type: "write", path: `vault/overrides/${d.id}.json`, data: bytes.buffer }, [bytes.buffer]);
}

// Save (⌘S, the button): where it goes first if it has no place yet, or "Save as…"; then written
async function save(as = false) {
  if (FS && (as || !session.handle)) {
    try {
      session.handle = await window.showSaveFilePicker({ suggestedName: session.name, types: TYPES, id: PICKER,
                                                         startIn: "documents" });
    } catch (e) {
      if (e.name === "AbortError") return null;
      throw e;
    }
    session.name = session.handle.name;
    session.writable = true;  // you chose it to save to
  } else if (FS && !session.writable && !(await allowWriting(true))) {
    throw new Error("Taxonymous wasn't allowed to save to it. Choose Save as… to save it somewhere else.");
  }
  await write();
  return { name: session.name, downloaded: !FS };
}

async function write() {
  clearTimeout(autoTimer);
  session.saving = true;
  showFile();
  try {
    const now = await contents();
    const text = await KEYFILE.seal({ ...now, vault: now.vault || null }, session.passphrase);
    if (FS) {
      const w = await session.handle.createWritable();
      await w.write(text);
      await w.close();
    } else {  // Safari, Firefox: a new copy, downloaded; replace your old one with it
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
      a.download = session.name;
      a.click();
    }
    saved = canonical(now);
    session.savedAt = Date.now();
    session.error = "";
  } finally {
    session.saving = false;
  }
  await recheck();  // anything that changed while it was being written
}

function closeFile() {  // the UI has asked about unsaved changes
  session = null;
  location.reload();  // the documents go with it: they were only ever in this page
}

window.addEventListener("beforeunload", (e) => {
  if (session?.dirty) { e.preventDefault(); e.returnValue = ""; }
});

// ---------------------------------------------------------------- the engines
// a home engine (the app's server), and cleaning lanes as the app has them: one below 8 cores,
// 2 from 8, 3 from 12, at most one per 4 GB (?lanes= overrides)

const LANES = Number(new URL(location.href).searchParams.get("lanes")) || Math.max(1, Math.min(
  (navigator.hardwareConcurrency || 2) >= 12 ? 3 : (navigator.hardwareConcurrency || 2) >= 8 ? 2 : 1,
  Math.floor((navigator.deviceMemory || 8) / 4)));
let home = null;
const lanes = [];
let loaded = null;

function spawn(role) {
  const channel = BRIDGE.channel();
  const worker = new Worker(`worker.js?role=${role}`);
  worker.postMessage({ type: "shared", ctrl: channel.ctrl, data: channel.data });
  worker.addEventListener("message", (e) => { if (e.data.type === "call") BRIDGE.answer(channel, e.data); });
  const up = new Promise((resolve, reject) => {
    worker.addEventListener("message", (e) => {
      if (e.data.type === "loaded") resolve(e.data);
      else if (e.data.type === "fatal") reject(new Error(e.data.error));
    });
  });
  return { worker, up, busy: false };
}

function engines() {
  if (!loaded) {
    home = spawn("home");
    home.worker.addEventListener("message", onHome);
    for (let i = 0; i < LANES; i++) {
      const lane = spawn("lane");
      lane.worker.addEventListener("message", (e) => onLane(lane, e.data));
      lanes.push(lane);
    }
    loaded = Promise.all([home.up, ...lanes.map((l) => l.up)]);
    loaded.catch((e) => say(`The engine didn't start: ${e.message}`));
  }
  return loaded;
}

// home -> page: a file it read for us, an answer to a request, a job for a lane
const pending = new Map();
let nextId = 0;
function homeAsk(message, transfer = []) {
  return new Promise((resolve) => {
    const id = nextId++;
    pending.set(id, resolve);
    home.worker.postMessage({ ...message, id }, transfer);
  });
}
const homeRead = (path) => homeAsk({ type: "read", path });

const laneQueue = [];
function onHome(e) {
  const m = e.data;
  if (m.type === "response") { pending.get(m.id)?.(m); pending.delete(m.id); }
  else if (m.type === "file") { pending.get(m.id)?.(m.data); pending.delete(m.id); }
  else if (m.type === "lane_job") { laneQueue.push(m); nextLane(); }
}
function nextLane() {
  for (const lane of lanes) {
    if (lane.busy || !laneQueue.length) continue;
    const job = laneQueue.shift();
    lane.busy = true;
    lane.job = job.id;
    lane.worker.postMessage({ type: "job", id: job.id, payload: job.payload, files: job.files },
                            Object.values(job.files).map((b) => b.buffer));
  }
}
function onLane(lane, m) {
  if (m.type === "progress") home.worker.postMessage({ type: "progress", id: m.id, progress: m.progress });
  else if (m.type === "done") {
    lane.busy = false;
    const buffers = Object.values(m.result.outputs).filter(Boolean).map((b) => b.buffer);
    home.worker.postMessage({ type: "lane_done", id: m.id, result: m.result }, buffers);
    nextLane();
  }
}

// the vault: held here (it's the open file's), lent to one engine at a time (bridge.py's lock);
// whatever it named comes back here, kept in memory until you save
let vaultLent = false;
const waitingForVault = [];
BRIDGE.handlers.vault_read = () => session?.vault || "";
BRIDGE.handlers.vault_lock = async () => {
  while (vaultLent) await new Promise((resolve) => waitingForVault.push(resolve));
  vaultLent = true;
  return session?.vault || "";
};
BRIDGE.handlers.vault_unlock = ({ vault }) => {
  if (session && vault && vault !== session.vault) {
    session.vault = vault;
    recheck();
  }
  vaultLent = false;
  waitingForVault.shift()?.();
  return true;
};

// the UI's requests, from the service worker: answered by the home engine
async function onServiceWorker(e) {
  if (e.data?.type !== "api") return;
  const port = e.ports[0];
  const r = e.data;
  await started;
  const res = await homeAsk({ type: "request", method: r.method, path: r.path, query: r.query, headers: r.headers,
                              body: r.body }, r.body ? [r.body] : []);
  if (r.method === "POST" && r.path === "/api/documents" && res.status === 201) {
    await restoreDecisions(JSON.parse(new TextDecoder().decode(res.body))).catch(() => {});
  }
  if (r.method !== "GET" && (r.method !== "POST" || r.path === "/api/documents")) fingerprints.clear();
  port.postMessage({ status: res.status, headers: res.headers, body: res.body }, [res.body]);
  if (r.method !== "GET" && !r.path.endsWith("/words") && !r.path.endsWith("/suggest")) recheck();
}

// ---------------------------------------------------------------- safe copies
// the workspace's Save: each document exported (the server checks it passed), then its safe
// copy written to a folder you choose (Chrome, Edge: asked first, while it's still your click)
// or downloaded (Safari, Firefox). Folders of documents are kept.

window.taxonymousHost = {
  get state() { return state; },
  folder: FS && "showDirectoryPicker" in window,
  subscribe(l) { listeners.add(l); return () => listeners.delete(l); },
  setUp() { setUp().catch((e) => set({ stage: "setup", progress: null, error: `Setup stopped: ${e.message}. Try again to carry on.` })); },
  remove: () => removeAll(),
  openFile() { act(openFile); },
  create: (profile, passphrase) => create(profile, passphrase).catch((e) => {
    chosen = null;
    if (state.stage === "starting") set({ stage: "ready" });
    throw e;
  }),
  newFile(from) {
    act(async () => newFrom(from === "empty" ? null
      : from === "sample" ? await (await fetch("samples/household.json")).text() : from.profile));
  },
  save: (as = false) => save(as),
  allowSaving: () => allowWriting(true),
  close: () => closeFile(),
  askPass: null,  // the UI's
  async saveCopies(ids) {
    let dir = null;
    if (FS && "showDirectoryPicker" in window) {
      try {
        dir = await window.showDirectoryPicker({ id: "taxonymous-safe-copies", mode: "readwrite", startIn: "documents" });
      } catch (e) {
        if (e.name === "AbortError") return null;
        throw e;
      }
    }
    const headers = { "X-Taxonymous-Token": sessionStorage.getItem("taxonymous-token") || "" };
    for (const id of ids) {
      const path = id.split("/").map(encodeURIComponent).join("/");
      const r = await fetch(`/api/documents/${path}/export`, { method: "POST", headers });
      if (!r.ok) throw new Error(`${id.split("/").pop()} couldn't be saved. Open it to see why.`);
      for (const rel of (await r.json()).paths) {
        const data = await homeRead(`ready-to-upload/${rel}`);
        const parts = rel.split("/");
        const name = parts.pop();
        if (dir) {
          let here = dir;
          for (const p of parts) here = await here.getDirectoryHandle(p, { create: true });
          const w = await (await here.getFileHandle(name, { create: true })).createWritable();
          await w.write(data);
          await w.close();
        } else {
          const a = document.createElement("a");
          a.href = URL.createObjectURL(new Blob([data], { type: "application/pdf" }));
          a.download = name;
          a.click();
          setTimeout(() => URL.revokeObjectURL(a.href), 60_000);
        }
      }
    }
    return dir ? `“${dir.name}”` : "your Downloads folder";
  },
};

// ---------------------------------------------------------------- the workspace

let begin;
const started = new Promise((resolve) => { begin = resolve; });

async function open(profile) {
  set({ stage: "starting", error: "" });
  await engines();
  await new Promise((resolve) => {
    const done = (e) => { if (e.data.type === "started") { home.worker.removeEventListener("message", done); resolve(); } };
    home.worker.addEventListener("message", done);
    home.worker.postMessage({ type: "start", token, lanes: LANES, profile: profile ? JSON.stringify(profile) : null });
  });
  begin();
  if (!session.dirty) saved = canonical(await contents());  // opened: what the file has, as the workspace reads it
  showFile();
  set({ stage: "open" });
  tellSpeed();
}

// what the start screen asks for: one at a time; a cancelled picker is no error
let acting = false;
async function act(fn) {
  if (acting) return;
  acting = true;
  set({ error: "" });
  try {
    await fn();
  } catch (e) {
    if (e.name !== "AbortError") set({ stage: state.stage === "starting" ? "ready" : state.stage, error: e.message });
  } finally {
    acting = false;
  }
}

// the token the UI shows the server (the app's own check); made now, as the UI reads it once
const token = hex(crypto.getRandomValues(new Uint8Array(16)));
sessionStorage.setItem("taxonymous-token", token);
// the workspace UI's files (build.py puts their tags in the template); it shows the start screen
for (const el of document.getElementById("app-assets").content.querySelectorAll("link, script")) {
  const copy = document.createElement(el.tagName);
  for (const a of el.attributes) copy.setAttribute(a.name, a.value);
  document.head.append(copy);
}
start().catch((e) => set({ stage: "setup", error: `That didn't work: ${e.message}` }));
