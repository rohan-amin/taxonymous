// The page: setup (download, check, keep for offline), your Taxonymous file, the engines, and
// then the workspace UI, the same one the Mac app shows. The UI talks to "the server" as it
// always does (/api/...); here the service worker hands each request to this page, and this
// page to the home engine (home.py), which runs the app's own server code in memory.
"use strict";

const $ = (id) => document.getElementById(id);
const CACHE_PREFIX = "taxonymous-v-";  // one per version; the service worker keeps the page in "taxonymous-shell"
let manifest, manifestText, cacheName;

const mb = (n) => (n < 2 ** 20 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 2 ** 20).toFixed(1)} MB`);
const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
const sha256 = async (buf) => hex(await crypto.subtle.digest("SHA-256", buf));
const say = (text) => { $("boot-message").textContent = text; };

// ---------------------------------------------------------------- setup

async function start() {
  document.body.classList.add("booting");
  // a new offline layer took over: start again on it, so the page, the workers and the
  // engine are always one version (an old page talking to a new engine breaks)
  const had = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.addEventListener("controllerchange", () => { if (had) location.reload(); });
  navigator.serviceWorker.addEventListener("message", onServiceWorker);
  await navigator.serviceWorker.register("sw.js");
  await navigator.serviceWorker.ready;
  if (!navigator.serviceWorker.controller) return location.reload();  // the first visit: run under it
  const text = await (await fetch("manifest.json", { cache: "no-store" })).text();
  manifest = JSON.parse(text);
  manifestText = text;
  cacheName = CACHE_PREFIX + (await sha256(new TextEncoder().encode(text))).slice(0, 16);
  const current = await currentVersion();
  if (current === cacheName && await isSetUp()) return ready();
  const todo = await toDownload(current);
  const size = mb(todo.reduce((a, f) => a + f.size, 0));
  if (current && await caches.has(current)) {  // set up before: this is an update
    $("setup-what").textContent = `A new version is ready: ${size} to download. The version you have keeps `
      + "working until you update.";
    $("setup-go").textContent = "Update";
    ready(true);
  } else {
    $("setup-what").textContent = `One-time download, ${size}. After this, Taxonymous works with no internet `
      + "connection, and your documents never leave this computer.";
  }
  $("setup-go").hidden = false;
}

async function currentVersion() {
  const r = await (await caches.open("taxonymous-current")).match("version");
  return r ? r.text() : null;
}

async function isSetUp() {
  const cache = await caches.open(cacheName);
  const keys = new Set((await cache.keys()).map((r) => new URL(r.url).pathname.replace(/^\//, "")));
  return manifest.files.every((f) => keys.has(f.path));
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
  $("setup-go").hidden = true;
  $("progress").hidden = false;
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
    $("progress-text").textContent = `${f.path.split("/").pop()}: ${mb(done)} of ${mb(total)}`;
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
      $("bar").style.width = `${((done + got) / total) * 100}%`;
    }
    const buf = await new Blob(parts).arrayBuffer();
    if (await sha256(buf) !== f.sha256) throw new Error(`${f.path} isn't the file it should be: not kept`);
    await cache.put(f.path, new Response(buf, { headers: { "Content-Type": res.headers.get("Content-Type") || "" } }));
    done += f.size;
  }
  await cache.put("manifest.json", new Response(manifestText, { headers: { "Content-Type": "application/json" } }));
  // this version is the one in use from now on; the one before goes
  await (await caches.open("taxonymous-current")).put("version", new Response(cacheName));
  for (const k of await caches.keys()) if (k.startsWith(CACHE_PREFIX) && k !== cacheName) await caches.delete(k);
  if (navigator.storage?.persist) await navigator.storage.persist();  // not cleared to make room
  if (current && current !== cacheName) return location.reload();  // an update: start on the new version
  ready();
}

function ready(updateWaiting = false) {
  $("progress").hidden = true;
  $("setup-go").hidden = !updateWaiting;
  if (!updateWaiting) $("setup-what").textContent = "Everything Taxonymous needs is on this computer.";
  $("ready").hidden = false;
  $("remove").hidden = false;
  $("file").hidden = false;
  engines();  // starting now, while you open your file
}

async function removeAll() {
  for (const k of await caches.keys()) if (k.startsWith("taxonymous-")) await caches.delete(k);
  for (const r of await navigator.serviceWorker.getRegistrations()) await r.unregister();
  location.reload();
}

// ---------------------------------------------------------------- your Taxonymous file
// opened into memory, saved only when you say: { profile, vault, decisions }

let session = null;  // { vault, name, handle, passphrase, dirty }
const FS = "showOpenFilePicker" in window && !new URLSearchParams(location.search).has("nofs");
const TYPES = [{ description: "Taxonymous file", accept: { "application/json": [".taxonymous"] } }];

function showFile() {
  $("filebar").hidden = !session || !opened;
  if (!session) return;
  $("file-name").textContent = session.name + (session.passphrase ? "" : " (not protected)");
  $("dirty").hidden = !session.dirty;
  $("save").textContent = FS && session.handle ? "Save" : "Save (downloads it)";
}

function changed() {
  if (!session) return;
  session.dirty = true;
  showFile();
}

// the passphrase: to protect a new file ("create"), or to open a protected one ("open")
function askPass(mode, name) {
  const d = $("pass");
  $("pass-title").textContent = mode === "create" ? "Protect your Taxonymous file" : `Open ${name}`;
  $("pass-hint").textContent = mode === "create"
    ? "It holds your SSN and account numbers. With a passphrase, a copy that gets out can't be read. "
      + "If you forget it, the file can't be opened: you'd start again from your profile.json."
    : "Enter the passphrase this file was saved with.";
  $("pass-2-row").hidden = $("protect-row").hidden = mode !== "create";
  $("protect").checked = true;
  $("pass-1").value = $("pass-2").value = "";
  $("pass-error").textContent = "";
  return new Promise((resolve) => {
    $("pass-form").onsubmit = (e) => {
      if (e.submitter?.value === "cancel") return;
      const p1 = $("pass-1").value;
      if (mode === "create" && $("protect").checked) {
        if (p1.length < 8) { e.preventDefault(); $("pass-error").textContent = "Use at least 8 characters."; return; }
        if (p1 !== $("pass-2").value) { e.preventDefault(); $("pass-error").textContent = "The two don't match."; return; }
      }
    };
    $("protect").onchange = () => { $("pass-1").disabled = $("pass-2").disabled = !$("protect").checked; };
    $("pass-1").disabled = $("pass-2").disabled = false;
    d.onclose = () => {
      if (d.returnValue !== "ok") return resolve(null);
      resolve({ passphrase: mode === "create" && !$("protect").checked ? "" : $("pass-1").value });
    };
    d.showModal();
  });
}

async function newFrom(profileText) {
  const profile = profileText ? JSON.parse(profileText) : null;
  const pass = await askPass("create");
  if (!pass) return;
  session = { vault: null, name: "my.taxonymous", handle: null, passphrase: pass.passphrase, dirty: true };
  await open(profile);
}

async function readPicked() {
  if (FS) {
    const [handle] = await window.showOpenFilePicker({ types: TYPES });
    return { text: await (await handle.getFile()).text(), name: handle.name, handle };
  }
  return new Promise((resolve) => {
    $("open-input").onchange = async (e) => {
      const f = e.target.files[0];
      e.target.value = "";
      if (f) resolve({ text: await f.text(), name: f.name, handle: null });
    };
    $("open-input").click();
  });
}

async function openFile() {
  const { text, name, handle } = await readPicked();
  let passphrase = "";
  let contents;
  for (;;) {
    try {
      if (KEYFILE.isProtected(text)) {
        const pass = await askPass("open", name);
        if (!pass) return;
        passphrase = pass.passphrase;
      }
      contents = await KEYFILE.open(text, passphrase);
      break;
    } catch (e) {
      if (!KEYFILE.isProtected(text)) throw e;
      setTimeout(() => { $("pass-error").textContent = e.message; }, 0);  // at the top of the next prompt
    }
  }
  session = { vault: contents.vault, name, handle, passphrase, dirty: false };
  await open(contents.profile);
}

// the profile as the workspace has it now (you may have changed it there)
async function currentProfile() {
  const data = await homeRead("vault/profile.json");
  return data ? JSON.parse(new TextDecoder().decode(data)) : null;
}

async function save(as = false) {
  const text = await KEYFILE.seal({ profile: await currentProfile(), vault: session.vault, decisions: {} },
                                  session.passphrase);
  if (FS && (as || !session.handle)) {
    session.handle = await window.showSaveFilePicker({ suggestedName: session.name, types: TYPES });
    session.name = session.handle.name;
  }
  if (FS && session.handle) {
    const w = await session.handle.createWritable();
    await w.write(text);
    await w.close();
  } else {  // Safari, Firefox: a new copy, downloaded; replace your old one with it
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    a.download = session.name;
    a.click();
  }
  session.dirty = false;
  showFile();
}

function closeFile() {
  if (session?.dirty && !confirm("Close without saving? Your changes since the last save will be lost.")) return;
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
    changed();
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
  if (r.method !== "GET" && !r.path.endsWith("/words") && !r.path.endsWith("/suggest")) changed();
  const res = await homeAsk({ type: "request", method: r.method, path: r.path, query: r.query, headers: r.headers,
                              body: r.body }, r.body ? [r.body] : []);
  port.postMessage({ status: res.status, headers: res.headers, body: res.body }, [res.body]);
}

// ---------------------------------------------------------------- the workspace

let opened = false;
let begin;
const started = new Promise((resolve) => { begin = resolve; });

async function open(profile) {
  say("Starting the engine…");
  await engines();
  const token = hex(crypto.getRandomValues(new Uint8Array(16)));
  await new Promise((resolve) => {
    const done = (e) => { if (e.data.type === "started") { home.worker.removeEventListener("message", done); resolve(); } };
    home.worker.addEventListener("message", done);
    home.worker.postMessage({ type: "start", token, lanes: LANES, profile: profile ? JSON.stringify(profile) : null });
  });
  begin();
  // the workspace UI, as the Mac app opens it: the new workspace, this token
  sessionStorage.setItem("taxonymous-token", token);
  sessionStorage.setItem("taxonymous-ui", "workspace");
  $("boot").hidden = true;
  document.body.classList.remove("booting");
  opened = true;
  showFile();
  const assets = $("app-assets").content;
  for (const el of assets.querySelectorAll("link, script")) {
    const copy = document.createElement(el.tagName);
    for (const a of el.attributes) copy.setAttribute(a.name, a.value);
    document.head.append(copy);
  }
}

$("setup-go").onclick = () => setUp().catch((e) => {
  $("progress-text").textContent = `Setup stopped: ${e.message}. Try again to resume.`;
  $("setup-go").hidden = false;
});
$("remove").onclick = removeAll;
const report = (e) => { if (e.name !== "AbortError") say(e.message); };
$("open-file").onclick = () => openFile().catch(report);
$("new-empty").onclick = () => newFrom(null).catch(report);
$("new-from-sample").onclick = async () => newFrom(await (await fetch("samples/household.json")).text()).catch(report);
$("new-from-profile").onchange = async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (f) newFrom(await f.text()).catch(report);
};
$("save").onclick = () => save().catch(report);
$("save-as").onclick = () => save(true).catch(report);
$("close-file").onclick = closeFile;
start().catch((e) => { $("setup-what").textContent = `That didn't work: ${e.message}`; });
