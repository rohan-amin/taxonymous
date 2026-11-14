// Setup (download, check, keep for offline), then the engine in a worker.
"use strict";

const $ = (id) => document.getElementById(id);
const CACHE_PREFIX = "taxonymous-v-";  // one per version; the service worker keeps the page in "taxonymous-shell"
let manifest, manifestText, cacheName, worker, workerReady;

const mb = (n) => `${(n / 2 ** 20).toFixed(1)} MB`;
const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

async function sha256(buf) {
  return hex(await crypto.subtle.digest("SHA-256", buf));
}

async function start() {
  if ("serviceWorker" in navigator) await navigator.serviceWorker.register("sw.js");
  const text = await (await fetch("manifest.json", { cache: "no-store" }).catch(() => caches.match("manifest.json"))).text();
  manifest = JSON.parse(text);
  manifestText = text;
  cacheName = CACHE_PREFIX + (await sha256(new TextEncoder().encode(text))).slice(0, 16);
  const total = manifest.files.reduce((a, f) => a + f.size, 0);
  if (await isSetUp()) return ready();
  $("setup-what").textContent = `One-time download, ${mb(total)}. After this, Taxonymous works with no internet `
    + "connection, and your documents never leave this computer.";
  $("setup-go").hidden = false;
}

async function isSetUp() {
  const cache = await caches.open(cacheName);
  const keys = new Set((await cache.keys()).map((r) => new URL(r.url).pathname.replace(/^\//, "")));
  return manifest.files.every((f) => keys.has(f.path));
}

// every file fetched, checked against its fingerprint, and kept; a changed file is refused
async function setUp() {
  $("setup-go").hidden = true;
  $("progress").hidden = false;
  const cache = await caches.open(cacheName);
  const total = manifest.files.reduce((a, f) => a + f.size, 0);
  let done = 0;
  for (const f of manifest.files) {
    if (await cache.match(f.path)) { done += f.size; continue; }  // kept from an interrupted setup
    $("progress-text").textContent = `${f.path.split("/").pop()}: ${mb(done)} of ${mb(total)}`;
    const res = await fetch(f.path, { cache: "no-store" });
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
  await cache.addAll(["./", "index.html"]);  // the page too, so it opens with no connection
  await cache.put("manifest.json", new Response(manifestText, { headers: { "Content-Type": "application/json" } }));
  for (const k of await caches.keys()) if (k.startsWith(CACHE_PREFIX) && k !== cacheName) await caches.delete(k);
  if (navigator.storage?.persist) await navigator.storage.persist();  // not cleared to make room
  ready();
}

function ready() {
  $("progress").hidden = true;
  $("setup-go").hidden = true;
  $("setup-what").textContent = "Everything Taxonymous needs is on this computer.";
  $("ready").hidden = false;
  $("remove").hidden = false;
  $("file").hidden = false;
  showFile();
}

// ---- your Taxonymous file: opened into memory, saved only when you say
let session = null;  // { profile, vault, name, handle, passphrase, dirty }
const FS = "showOpenFilePicker" in window && !new URLSearchParams(location.search).has("nofs");
const TYPES = [{ description: "Taxonymous file", accept: { "application/json": [".taxonymous"] } }];

function showFile() {
  $("file-closed").hidden = !!session;
  $("file-open").hidden = !session;
  $("try").hidden = !session;
  if (!session) return;
  $("file-name").textContent = session.name;
  $("file-lock").textContent = session.passphrase ? "Protected with a passphrase" : "Not protected: anyone who gets this file can read it";
  $("dirty").hidden = !session.dirty;
  $("save").textContent = FS && session.handle ? "Save" : "Save (downloads it)";
}

function changed() {
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
    const check = (e) => {
      if (e.submitter?.value === "cancel") return;
      const p1 = $("pass-1").value;
      if (mode === "create" && $("protect").checked) {
        if (p1.length < 8) { e.preventDefault(); $("pass-error").textContent = "Use at least 8 characters."; return; }
        if (p1 !== $("pass-2").value) { e.preventDefault(); $("pass-error").textContent = "The two don't match."; return; }
      }
    };
    $("pass-form").onsubmit = check;
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
  const profile = JSON.parse(profileText);
  const pass = await askPass("create");
  if (!pass) return;
  session = { profile, vault: null, name: "My taxes.taxonymous", handle: null, passphrase: pass.passphrase, dirty: true };
  showFile();
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
      $("engine").textContent = e.message;
      if (!KEYFILE.isProtected(text)) throw e;
      alertInDialog(e.message);
    }
  }
  session = { profile: contents.profile, vault: contents.vault, name, handle, passphrase, dirty: false };
  $("results").innerHTML = "";
  showFile();
}

function alertInDialog(message) {  // shown at the top of the next passphrase prompt
  setTimeout(() => { $("pass-error").textContent = message; }, 0);
}

async function save(as = false) {
  const text = await KEYFILE.seal({ profile: session.profile, vault: session.vault }, session.passphrase);
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
  $("results").innerHTML = "";
  showFile();
}

window.addEventListener("beforeunload", (e) => {
  if (session?.dirty) { e.preventDefault(); e.returnValue = ""; }
});

async function removeAll() {
  for (const k of await caches.keys()) if (k.startsWith("taxonymous-")) await caches.delete(k);
  for (const r of await navigator.serviceWorker.getRegistrations()) await r.unregister();
  location.reload();
}

// ---- the engine
function engine() {
  if (!workerReady) {
    worker = new Worker("worker.js");
    $("engine").textContent = "Starting the engine…";
    workerReady = new Promise((resolve, reject) => {
      worker.onmessage = (e) => {
        if (e.data.type === "ready") {
          $("engine").textContent = `Engine ready in ${e.data.seconds}s (PyMuPDF ${e.data.pymupdf}, in this page).`;
          resolve();
        } else if (e.data.type === "fatal") reject(new Error(e.data.error));
      };
    });
  }
  return workerReady;
}

let nextId = 0;
function clean(profile, vault, name, bytes) {
  return new Promise((resolve) => {
    const id = nextId++;
    const listen = (e) => {
      if (e.data.id !== id) return;
      worker.removeEventListener("message", listen);
      resolve(e.data.result);
    };
    worker.addEventListener("message", listen);
    worker.postMessage({ id, profile, vault, name, bytes }, [bytes.buffer]);
  });
}

async function run(docs) {
  $("results").innerHTML = "";
  try {
    await engine();
    for (const { name, bytes } of docs) {
      const card = document.createElement("div");
      card.className = "result";
      card.textContent = `${name}: cleaning…`;
      $("results").append(card);
      const r = await clean(JSON.stringify(session.profile), session.vault, name, bytes);
      if (r.vault && r.vault !== session.vault) {  // new labels: kept in memory until you save
        session.vault = r.vault;
        changed();
      }
      show(card, r);
    }
  } catch (e) {
    $("engine").textContent = `That didn't work: ${e.message}`;
  }
}

function show(card, r) {
  card.innerHTML = "";
  if (r.error) { card.textContent = r.error; return; }
  const checked = r.passed && !r.pictures_unread;
  const title = document.createElement("strong");
  title.textContent = r.name;
  const status = document.createElement("p");
  status.className = checked ? "ok" : "warn";
  status.textContent = !r.passed ? `Not checked: ${r.issues.join(", ")}`
    : r.pictures_unread ? `Cleaned, but ${r.pictures_unread} picture${r.pictures_unread === 1 ? "" : "s"} weren't read (OCR isn't in this test). Don't share this copy.`
      : `Cleaned: ${r.labels.length} kind${r.labels.length === 1 ? "" : "s"} of detail removed · Checked again: none left`;
  const meta = document.createElement("p");
  meta.className = "muted";
  meta.textContent = `Labels: ${r.labels.join(" ")} · cleaned in ${r.clean_s}s, checked in ${r.check_s}s`;
  const row = document.createElement("div");
  row.className = "row";
  const dl = document.createElement("a");
  dl.className = "button";
  dl.textContent = checked ? "Download the safe copy" : "Download (not fully checked)";
  dl.href = URL.createObjectURL(new Blob([r.pdf], { type: "application/pdf" }));
  dl.download = r.name.replace(/\.pdf$/i, "") + ".redacted.pdf";
  const textBtn = document.createElement("button");
  textBtn.textContent = "Show the text an AI reads";
  const pre = document.createElement("pre");
  pre.hidden = true;
  pre.textContent = r.text;
  textBtn.onclick = () => { pre.hidden = !pre.hidden; };
  row.append(dl, textBtn);
  card.append(title, status, meta, row, pre);
}

$("setup-go").onclick = () => setUp().catch((e) => { $("progress-text").textContent = `Setup stopped: ${e.message}. Try again to resume.`; $("setup-go").hidden = false; });
$("remove").onclick = removeAll;
$("open-file").onclick = () => openFile().catch((e) => { if (e.name !== "AbortError") $("engine").textContent = e.message; });
$("new-from-sample").onclick = async () => newFrom(await (await fetch("samples/household.json")).text());
$("new-from-profile").onchange = async (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) newFrom(await f.text()); };
$("save").onclick = () => save().catch((e) => { if (e.name !== "AbortError") $("engine").textContent = e.message; });
$("save-as").onclick = () => save(true).catch((e) => { if (e.name !== "AbortError") $("engine").textContent = e.message; });
$("close-file").onclick = closeFile;
$("samples").onclick = async () => {
  const names = ["w2.pdf", "1099_int.pdf", "k1_statement.pdf", "brokerage_consolidated.pdf"];
  run(await Promise.all(names.map(async (name) => ({ name, bytes: new Uint8Array(await (await fetch(`samples/${name}`)).arrayBuffer()) }))));
};
$("pdfs").onchange = async (e) => {
  run(await Promise.all([...e.target.files].map(async (f) => ({ name: f.name, bytes: new Uint8Array(await f.arrayBuffer()) }))));
  e.target.value = "";
};
start();
