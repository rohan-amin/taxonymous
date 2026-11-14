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
  $("try").hidden = false;
}

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
function clean(profile, name, bytes) {
  return new Promise((resolve) => {
    const id = nextId++;
    const listen = (e) => {
      if (e.data.id !== id) return;
      worker.removeEventListener("message", listen);
      resolve(e.data.result);
    };
    worker.addEventListener("message", listen);
    worker.postMessage({ id, profile, name, bytes }, [bytes.buffer]);
  });
}

async function profileJson() {
  if ($("profile-choice").value === "sample") return (await fetch("samples/household.json")).text();
  const file = $("profile-file").files[0];
  if (!file) throw new Error("Choose your profile.json first.");
  return file.text();
}

async function run(docs) {
  $("results").innerHTML = "";
  try {
    const profile = await profileJson();
    await engine();
    for (const { name, bytes } of docs) {
      const card = document.createElement("div");
      card.className = "result";
      card.textContent = `${name}: cleaning…`;
      $("results").append(card);
      show(card, await clean(profile, name, bytes));
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
$("profile-choice").onchange = () => { $("profile-file").hidden = $("profile-choice").value !== "file"; };
$("samples").onclick = async () => {
  const names = ["w2.pdf", "1099_int.pdf", "k1_statement.pdf", "brokerage_consolidated.pdf"];
  run(await Promise.all(names.map(async (name) => ({ name, bytes: new Uint8Array(await (await fetch(`samples/${name}`)).arrayBuffer()) }))));
};
$("pdfs").onchange = async (e) => {
  run(await Promise.all([...e.target.files].map(async (f) => ({ name: f.name, bytes: new Uint8Array(await f.arrayBuffer()) }))));
  e.target.value = "";
};
start();
