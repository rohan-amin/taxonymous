// The workspace: files on the left, the document in the middle, My profile on the right.
// Talks to the same local API as the classic review screen (../App.tsx).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, DragEvent, KeyboardEvent, PointerEvent as RPointerEvent } from "react";
import { api, pageImage } from "../api";
import type { Box, DocDetail, DocSummary, Profile } from "../api";
import { Details } from "../Details";
import { DOT, plain, timeLeft } from "./messages";
import type { Tone } from "./messages";
import "./workspace.css";

const ACCEPT = ".pdf,.jpg,.jpeg,.png,.tif,.tiff,.heic,.heif,.webp";
const ZOOMS = [0.75, 1, 1.25, 1.5, 2];
const store = {
  get<T>(k: string, d: T): T {
    try {
      const v = localStorage.getItem(`tx-${k}`);
      return v === null ? d : (JSON.parse(v) as T);
    } catch {
      return d;
    }
  },
  set(k: string, v: unknown) {
    try {
      localStorage.setItem(`tx-${k}`, JSON.stringify(v));
    } catch {
      /* not kept: fine */
    }
  },
};

const GROUPS: { tone: Tone[]; title: string }[] = [
  { tone: ["look", "stop"], title: "Needs you" },
  { tone: ["work"], title: "Working" },
  { tone: ["new"], title: "Not cleaned yet" },
  { tone: ["ok"], title: "Safe to share" },
];

const mask = (v: string) => v.replace(/[A-Za-z0-9](?=(?:[^A-Za-z0-9]*[A-Za-z0-9]){4})/g, "•");

// a drawer edge you drag (or move with the arrow keys) to set its width
function Grip({ label, width, setWidth, side }: { label: string; width: number; setWidth: (w: number) => void;
                                                   side: "left" | "right" }) {
  const start = useRef<{ x: number; w: number } | null>(null);
  const [active, setActive] = useState(false);
  const clamp = (w: number) => Math.max(200, Math.min(560, Math.round(w)));
  const down = (e: RPointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    start.current = { x: e.clientX, w: width };
    setActive(true);
  };
  const move = (e: RPointerEvent<HTMLDivElement>) => {
    if (!start.current) return;
    const dx = e.clientX - start.current.x;
    setWidth(clamp(start.current.w + (side === "left" ? dx : -dx)));
  };
  const up = () => {
    start.current = null;
    setActive(false);
  };
  const key = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = { ArrowLeft: -16, ArrowRight: 16 }[e.key];
    if (step) {
      e.preventDefault();
      setWidth(clamp(width + (side === "left" ? step : -step)));
    }
  };
  return <div className={`ws-grip${active ? " active" : ""}`} role="separator" aria-orientation="vertical"
              aria-label={label} aria-valuenow={width} aria-valuemin={200} aria-valuemax={560} tabIndex={0}
              onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onKeyDown={key} />;
}

function Welcome({ onClose }: { onClose: (hide: boolean) => void }) {
  const [hide, setHide] = useState(store.get("welcome-hidden", false));
  const steps: [string, string][] = [
    ["Add one document", "Any document works. Your W-2 or 1040 is a good start: most of your details are at the top."],
    ["Add your details", "Your name, SSN, address and account numbers go into My profile, and are removed from all documents."],
    ["Add the rest", "Your details are removed from each new document automatically. Draw a box over anything else, on that document only."],
    ["We double-check", "Every safe copy is read again, scans included. A copy is only ready when none of your details can be found."],
    ["Share the safe copies", "Drag them into your AI assistant. Your originals never change."],
  ];
  return (
    <div className="ws-welcome" role="dialog" aria-label="Welcome to Taxonymous">
      <div className="ws-welcome-inner">
        <div style={{ display: "flex", flexDirection: "column", gap: 8, maxWidth: 720 }}>
          <h1>Share your tax documents with AI, without sharing who you are.</h1>
          <p style={{ fontSize: 15, color: "var(--w-muted)" }}>
            Taxonymous removes your personal details and keeps the numbers an AI assistant needs.
          </p>
        </div>
        <div className="ws-local-banner">
          <svg width="30" height="30" viewBox="0 0 16 16" aria-hidden="true" style={{ flex: "none" }}>
            <rect x="3" y="7" width="10" height="7" rx="1.5" fill="none" stroke="#fff" strokeWidth="1.3" />
            <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" fill="none" stroke="#fff" strokeWidth="1.3" />
          </svg>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <strong>Everything stays on this computer</strong>
            <p>Your documents and details are never uploaded. You can even turn off your Wi-Fi and Taxonymous will still work.</p>
            <p style={{ color: "#fff", fontWeight: 600 }}>You're always in control of your sensitive information: only the safe copies you choose to share ever leave this computer.</p>
          </div>
        </div>
        <ol className="ws-steps">
          {steps.map(([t, b], i) => (
            <li key={t}><span className="n">{i + 1}</span><strong>{t}</strong><p>{b}</p></li>
          ))}
        </ol>
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
          <button type="button" className="wbtn primary" style={{ height: 34, padding: "0 20px", fontSize: 14 }}
                  onClick={() => onClose(hide)}>Get started</button>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={hide} onChange={(e) => setHide(e.target.checked)} />
            Don't show this when Taxonymous opens
          </label>
        </div>
        <p style={{ color: "var(--w-muted)", fontSize: 12 }}>You can see this again from How it works, at the top of the window.</p>
      </div>
    </div>
  );
}

function ProfileDrawer({ profile, onOpen }: { profile: Profile | null; onOpen: () => void }) {
  const groups: { title: string; items: [string, string][] }[] = [];
  if (profile) {
    for (const p of profile.people) {
      const items: [string, string][] = [["Name", p.name]];
      if (p.ssn) items.push(["SSN", mask(p.ssn)]);
      if (p.dob) items.push(["Born", mask(p.dob)]);
      if (p.employer) items.push(["Employer", p.employer]);
      groups.push({ title: ({ taxpayer: "You", spouse: "Spouse", dependent: "Dependent" } as Record<string, string>)[p.role] ?? "Person", items });
    }
    if (profile.addresses.length) {
      groups.push({ title: "Addresses", items: profile.addresses.map((a) => ["Address", [a.street, a.city, a.state, a.zip].filter(Boolean).join(", ")]) });
    }
    if (profile.accounts.length) {
      groups.push({ title: "Accounts", items: profile.accounts.map((a) => [a.institution || "Account", mask(a.number)]) });
    }
    const simple: [keyof Profile, string, string][] = [["phones", "Phones", "Phone"], ["emails", "Emails", "Email"]];
    for (const [k, title, one] of simple) {
      const xs = profile[k] as string[];
      if (xs.length) groups.push({ title, items: xs.map((x) => [one, x]) });
    }
    if (profile.cards.length) groups.push({ title: "Cards", items: profile.cards.map((c) => [c.description || "Card", mask(c.number)]) });
    if (profile.eins.length) groups.push({ title: "Your EINs", items: profile.eins.map((e) => [e.description || "EIN", mask(e.ein)]) });
    if (profile.numbers.length) groups.push({ title: "Other numbers", items: profile.numbers.map((n) => [n.description || "Number", mask(n.number)]) });
    if (profile.preparers.length) groups.push({ title: "Tax preparer", items: profile.preparers.map((p) => ["Preparer", p.name || p.firm]) });
  }
  const n = groups.reduce((a, g) => a + g.items.length, 0);
  return (
    <>
      <div className="ws-drawer-scroll" style={{ padding: "4px 8px 16px" }}>
        <p className="ws-note">Removed from <strong>all documents</strong>, in any format it's printed: XXX-XX-4323, ABERNATHY, AVERY.</p>
        {!n && <p className="ws-note" style={{ border: "1px dashed var(--w-line-strong)", borderRadius: 8, padding: 12, margin: "8px 6px" }}>
          Nothing yet. Open My profile to add your name, SSN and address.</p>}
        {groups.map((g, i) => (
          <div className="ws-pgroup" key={`${g.title}${i}`}>
            <span>{g.title}</span>
            {g.items.map(([k, v], j) => <div className="ws-entry" key={j}><span className="k">{k}</span><span className="v">{v}</span></div>)}
          </div>
        ))}
        <p className="ws-note" style={{ marginTop: 8 }}>Boxes you draw aren't listed here. They stay on their own document.</p>
      </div>
      <div className="ws-drawer-foot">
        <button type="button" className="wbtn" style={{ width: "100%" }} onClick={onOpen}>Open My profile to type or edit</button>
      </div>
    </>
  );
}

function DocView({ summary, hasProfile, zoom, setZoom, side, setSide, onNext, nextName, onChanged }: {
  summary: DocSummary; hasProfile: boolean; zoom: number; setZoom: (z: number) => void;
  side: "redacted" | "original"; setSide: (s: "redacted" | "original") => void;
  onNext: () => void; nextName: string | null; onChanged: () => void;
}) {
  const [detail, setDetail] = useState<DocDetail | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [pending, setPending] = useState<Box[]>([]);
  const [drag, setDrag] = useState<{ page: number; x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const id = summary.id;

  useEffect(() => {
    let live = true;
    api.document(id).then((d) => live && setDetail(d)).catch((e) => live && setError((e as Error).message));
    return () => { live = false; };
  }, [id, summary.version, summary.status]);
  useEffect(() => { setPending([]); setDrawing(false); }, [id]);

  const msg = plain(summary, hasProfile);
  const processed = summary.version > 0;
  const showing = processed ? side : "original";
  const dpi = Math.min(288, Math.max(72, Math.round((72 * zoom * (window.devicePixelRatio || 1)) / 24) * 24));

  const toPoint = (e: RPointerEvent<HTMLDivElement>, w: number) => {
    const r = e.currentTarget.getBoundingClientRect();
    const k = r.width / w;
    return [(e.clientX - r.left) / k, (e.clientY - r.top) / k];
  };
  const apply = async () => {
    try {
      await api.addBoxes(id, pending);
      setPending([]);
      setDrawing(false);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const clean = async () => {
    try {
      await api.run(id);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section className="ws-doc" aria-label="Document">
      <div className="ws-doc-head">
        <div className="ws-doc-title">
          <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
            <span className="file">{summary.folder ? `${summary.folder.replaceAll("/", " / ")} / ` : ""}{summary.name}</span>
            <div className={`ws-banner ${msg.tone}`} role="status" style={{ marginTop: 6 }}>
              <span className={`ws-dot ${DOT[msg.tone]}`} style={{ marginTop: 5 }} aria-hidden="true" />
              <div style={{ flex: 1 }}><strong>{msg.title}</strong><span>{msg.body}</span>
                {summary.status === "running" && summary.progress?.fraction != null &&
                  <div className="ws-bar work" role="progressbar" aria-label="How far cleaning has got"
                       aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(summary.progress.fraction * 100)}>
                    <div style={{ width: `${summary.progress.fraction * 100}%` }} />
                  </div>}
              </div>
            </div>
          </div>
          <div style={{ display: "flex", gap: 6, flex: "none" }}>
            {!processed && hasProfile && summary.status === "new" &&
              <button type="button" className="wbtn" onClick={clean}>Clean this document</button>}
            <button type="button" className="wbtn primary" onClick={onNext} disabled={!nextName}
                    title={nextName ?? "No other documents"}>Next document ›</button>
          </div>
        </div>
        <div className="ws-tools">
          <button type="button" className="wbtn ws-tool" disabled title="Needs word positions from the engine: the next step">
            <span>Click a value <span className="ws-soon">SOON</span></span><small>Removes it from all documents</small>
          </button>
          <button type="button" className={`wbtn ws-tool${drawing ? " on" : ""}`} onClick={() => setDrawing(!drawing)}
                  disabled={!processed} aria-pressed={drawing}>
            <span>Draw a box</span><small>This document only</small>
          </button>
          {pending.length > 0 && <>
            <button type="button" className="wbtn primary" onClick={apply}>Apply {pending.length} box{pending.length === 1 ? "" : "es"} and check again</button>
            <button type="button" className="wbtn quiet" onClick={() => setPending(pending.slice(0, -1))}>Undo</button>
          </>}
          <span className="hint">{drawing ? "Drag over anything. It's blacked out on this document only." : ""}</span>
          <div className="seg" role="group" aria-label="Show">
            <button type="button" className={showing === "original" ? "on" : ""} onClick={() => setSide("original")}>Original</button>
            <button type="button" className={showing === "redacted" ? "on" : ""} onClick={() => setSide("redacted")} disabled={!processed}>Safe copy</button>
          </div>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
            <button type="button" className="wbtn icon" aria-label="Zoom out" onClick={() => setZoom(ZOOMS[Math.max(0, ZOOMS.indexOf(zoom) - 1)])}>−</button>
            <span style={{ minWidth: 44, textAlign: "center", fontSize: 12, fontWeight: 600 }}>{Math.round(zoom * 100)}%</span>
            <button type="button" className="wbtn icon" aria-label="Zoom in" onClick={() => setZoom(ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(zoom) + 1)])}>+</button>
          </div>
        </div>
        {error && <div className="ws-banner stop" role="alert"><div><strong>That didn't work</strong><span>{error}</span></div>
          <button type="button" className="wbtn quiet" onClick={() => setError(null)} style={{ marginLeft: "auto" }}>OK</button></div>}
      </div>
      <div className="ws-viewer">
        {!detail && <div className="ws-empty">Loading…</div>}
        {detail?.pages.map(([w, h], n) => {
          const local = detail.regions.filter((r) => r.page === n && r.source === "manual");
          const others = showing === "original" ? detail.regions.filter((r) => r.page === n && r.source !== "manual") : [];
          const box = (rect: number[]): CSSProperties => ({
            left: `${(rect[0] / w) * 100}%`, top: `${(rect[1] / h) * 100}%`,
            width: `${((rect[2] - rect[0]) / w) * 100}%`, height: `${((rect[3] - rect[1]) / h) * 100}%`,
          });
          return (
            <div key={n} className={`ws-page${drawing ? " drawing" : ""}`} style={{ width: w * zoom, height: h * zoom }}
                 onPointerDown={(e) => {
                   if (!drawing) return;
                   e.currentTarget.setPointerCapture(e.pointerId);
                   const [x, y] = toPoint(e, w);
                   setDrag({ page: n, x0: x, y0: y, x1: x, y1: y });
                 }}
                 onPointerMove={(e) => {
                   if (!drag || drag.page !== n) return;
                   const [x, y] = toPoint(e, w);
                   setDrag({ ...drag, x1: x, y1: y });
                 }}
                 onPointerUp={() => {
                   if (!drag) return;
                   const r: [number, number, number, number] = [Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1),
                     Math.max(drag.x0, drag.x1), Math.max(drag.y0, drag.y1)];
                   if (r[2] - r[0] > 4 && r[3] - r[1] > 4) setPending([...pending, { page: n, rect: r }]);
                   setDrag(null);
                 }}>
              <img src={pageImage(id, n, showing, dpi, detail.version)} alt={`${showing === "original" ? "Original" : "Safe copy"}, page ${n + 1}`} draggable={false} />
              {others.map((r, i) => <div key={`o${i}`} className="ws-region mine" style={box(r.rect)} title={`Removed: ${r.kind}`} />)}
              {local.map((r, i) => <div key={`l${i}`} className="ws-local" style={box(r.rect)}>THIS DOC ONLY</div>)}
              {pending.filter((b) => b.page === n).map((b, i) => <div key={`p${i}`} className="ws-local pending" style={box(b.rect)}>THIS DOC ONLY</div>)}
              {drag && drag.page === n && <div className="ws-drag" style={box([Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1),
                Math.max(drag.x0, drag.x1), Math.max(drag.y0, drag.y1)])} />}
            </div>
          );
        })}
        {detail && (
          <div className="ws-legend">
            <span><span className="ws-swatch" style={{ background: "#1d1d1b" }} />From My profile: removed from all documents</span>
            <span><span className="ws-swatch" style={{ background: "repeating-linear-gradient(135deg,#1f4f80 0 3px,#3a7bc0 3px 6px)" }} />Drawn: this document only</span>
            <span style={{ flexBasis: "100%" }}>In the safe copy both are solid black. The stripes are only here, so you can tell them apart.</span>
          </div>
        )}
      </div>
    </section>
  );
}

export function Workspace() {
  const [docs, setDocs] = useState<DocSummary[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [hasProfile, setHasProfile] = useState(false);
  const [view, setView] = useState<"work" | "profile">("work");
  const [leftW, setLeftW] = useState(() => store.get("left-w", 280));
  const [rightW, setRightW] = useState(() => store.get("right-w", 300));
  const [leftOpen, setLeftOpen] = useState(() => store.get("left-open", true));
  const [rightOpen, setRightOpen] = useState(() => store.get("right-open", true));
  const [zoom, setZoom] = useState(() => store.get("zoom", 1.25));
  const [side, setSide] = useState<"redacted" | "original">("redacted");
  const [q, setQ] = useState("");
  const [safeOpen, setSafeOpen] = useState(false);
  const [welcome, setWelcome] = useState(() => !store.get("welcome-hidden", false));
  const [dropping, setDropping] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => store.set("left-w", leftW), [leftW]);
  useEffect(() => store.set("right-w", rightW), [rightW]);
  useEffect(() => store.set("left-open", leftOpen), [leftOpen]);
  useEffect(() => store.set("right-open", rightOpen), [rightOpen]);
  useEffect(() => store.set("zoom", zoom), [zoom]);

  const loadProfile = useCallback(() => api.profile().then((p) => {
    setProfile(p.profile);
    setHasProfile(p.exists);
  }).catch(() => undefined), []);
  const refresh = useCallback(async () => {
    try {
      const list = await api.documents();
      setDocs(list);
      setSel((s) => (s && list.some((d) => d.id === s) ? s : list[0]?.id ?? null));
    } catch (e) {
      setToast((e as Error).message);
    }
  }, []);
  useEffect(() => { loadProfile(); refresh(); }, [loadProfile, refresh]);
  const working = !!docs?.some((d) => d.status === "queued" || d.status === "running");
  useEffect(() => {
    const t = setInterval(refresh, working ? 1000 : 4000);
    return () => clearInterval(t);
  }, [working, refresh]);

  const tones = useMemo(() => new Map((docs ?? []).map((d) => [d.id, plain(d, hasProfile).tone])), [docs, hasProfile]);
  const order = useMemo(() => GROUPS.flatMap((g) => (docs ?? []).filter((d) => g.tone.includes(tones.get(d.id)!))), [docs, tones]);
  const current = docs?.find((d) => d.id === sel) ?? null;
  const next = useMemo(() => {
    if (!current) return null;
    const i = order.findIndex((d) => d.id === current.id);
    const rest = [...order.slice(i + 1), ...order.slice(0, i)];
    return rest.find((d) => tones.get(d.id) !== "ok") ?? rest[0] ?? null;
  }, [order, current, tones]);
  const prev = useMemo(() => {
    if (!current) return null;
    const i = order.findIndex((d) => d.id === current.id);
    return i > 0 ? order[i - 1] : null;
  }, [order, current]);
  const ready = (docs ?? []).filter((d) => d.status === "passed");

  const upload = useCallback(async (files: FileList | File[]) => {
    const failed: string[] = [];
    let added = 0;
    for (const f of Array.from(files)) {
      try {
        const d = await api.upload(f, "");
        added += 1;
        if (hasProfile) await api.run(d.id).catch(() => undefined);
      } catch (e) {
        const m = (e as Error).message;
        failed.push(m.includes("already in") ? `You already added ${f.name}.` : m.startsWith("only PDFs")
          ? `Taxonymous can't open ${f.name}. It works with PDFs and photos.` : `${f.name}: ${m}`);
      }
    }
    setToast([added ? `Added ${added} document${added === 1 ? "" : "s"}.` : "", ...failed].filter(Boolean).join(" "));
    refresh();
  }, [hasProfile, refresh]);

  // commands from the native menu bar (tools/native_window.py) and the keyboard
  const command = useCallback((c: string) => {
    const zi = ZOOMS.indexOf(zoom);
    switch (c) {
      case "add": fileInput.current?.click(); break;
      case "toggle-files": setLeftOpen((o) => !o); break;
      case "toggle-profile": setRightOpen((o) => !o); break;
      case "zoom-in": setZoom(ZOOMS[Math.min(ZOOMS.length - 1, zi + 1)]); break;
      case "zoom-out": setZoom(ZOOMS[Math.max(0, zi - 1)]); break;
      case "zoom-reset": setZoom(1.25); break;
      case "next": if (next) setSel(next.id); break;
      case "prev": if (prev) setSel(prev.id); break;
      case "original": setSide("original"); break;
      case "safe": setSide("redacted"); break;
      case "safe-copies": setSafeOpen(true); break;
      case "profile": setView("profile"); break;
      case "welcome": setWelcome(true); break;
      case "added": // files the native File menu copied into the inbox: clean the new ones
        api.documents().then(async (list) => {
          setDocs(list);
          if (hasProfile) for (const d of list.filter((x) => x.status === "new")) await api.run(d.id).catch(() => undefined);
          refresh();
        }).catch(() => undefined);
        break;
    }
  }, [zoom, next, prev, hasProfile, refresh]);
  useEffect(() => {
    const onToast = (e: Event) => setToast((e as CustomEvent<string>).detail || null);
    window.addEventListener("tx-toast", onToast);
    return () => window.removeEventListener("tx-toast", onToast);
  }, []);
  useEffect(() => {
    const onCmd = (e: Event) => command((e as CustomEvent<string>).detail);
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (!e.metaKey || e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      const map: Record<string, string> = { "=": "zoom-in", "+": "zoom-in", "-": "zoom-out", "0": "zoom-reset",
        "]": "next", "[": "prev", "1": "toggle-files", "2": "toggle-profile", o: "add" };
      const c = map[e.key];
      if (c) { e.preventDefault(); command(c); }
    };
    window.addEventListener("tx-command", onCmd);
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("tx-command", onCmd); window.removeEventListener("keydown", onKey); };
  }, [command]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDropping(false);
    if (e.dataTransfer.files.length) upload(e.dataTransfer.files);
  };
  const shown = (docs ?? []).filter((d) => !q.trim() || d.name.toLowerCase().includes(q.trim().toLowerCase()));
  const checked = ready.length;
  const total = docs?.length ?? 0;
  // a Run all in progress: the jobs in it, how far they have got together, and the time left
  const running = (docs ?? []).filter((d) => d.status === "running" && d.progress);
  const batch = running.length ? (() => {
    const jobs = Math.max(...running.map((d) => d.progress!.jobs));
    const queued = (docs ?? []).filter((d) => d.status === "queued").length;
    const finished = Math.max(0, jobs - queued - running.length);
    const sum = running.reduce((a, d) => a + (d.progress!.fraction ?? 0), 0);
    return { jobs, finished, fraction: (finished + sum) / jobs,
             left: timeLeft(running[0].progress!.batch_eta) };
  })() : null;

  return (
    <div className="ws" onDragOver={(e) => { e.preventDefault(); setDropping(true); }}
         onDragLeave={(e) => e.currentTarget === e.target && setDropping(false)} onDrop={onDrop}>
      <header className="ws-top">
        <div className="ws-brand">
          <svg width="18" height="18" viewBox="0 0 28 28" aria-hidden="true"><rect x="3" y="2" width="22" height="24" rx="4" fill="none" stroke="currentColor" strokeWidth="2.4" /><rect x="8" y="9" width="12" height="4" rx="1" fill="currentColor" /></svg>
          Taxonymous
        </div>
        <nav aria-label="App">
          <button type="button" className="wbtn quiet" onClick={() => setWelcome(true)}>How it works</button>
          <button type="button" className={`wbtn${view === "profile" ? " on" : ""}`} onClick={() => setView(view === "profile" ? "work" : "profile")}>My profile</button>
        </nav>
      </header>
      <input ref={fileInput} type="file" multiple accept={ACCEPT} hidden onChange={(e) => { if (e.target.files) upload(e.target.files); e.target.value = ""; }} />

      {view === "profile" ? (
        <div className="ws-profile-view">
          <div style={{ padding: "10px 16px", borderBottom: "1px solid var(--w-line)" }}>
            <button type="button" className="wbtn quiet" onClick={() => { setView("work"); loadProfile(); }}>‹ Back to documents</button>
          </div>
          <Details onSaved={loadProfile} onRunAll={() => { api.runAll().then(refresh); setView("work"); }} />
        </div>
      ) : (
        <div className="ws-body">
          {leftOpen ? (
            <>
              <nav className="ws-drawer left" style={{ width: leftW }} aria-label="Your documents">
                <div className="ws-drawer-head">
                  <strong>Documents <span className="count">{total}</span></strong>
                  <button type="button" className="wbtn quiet" onClick={() => setLeftOpen(false)} aria-label="Hide the document list">‹ Hide</button>
                </div>
                <div className="ws-drawer-tools">
                  <div style={{ display: "flex", gap: 6 }}>
                    <button type="button" className="wbtn primary" style={{ flex: 1 }} onClick={() => fileInput.current?.click()}>Add documents…</button>
                    <button type="button" className="wbtn" style={{ flex: 1 }} onClick={() => setSafeOpen(true)}>Safe copies · {checked}</button>
                  </div>
                  <input className="search" type="search" placeholder="Find a document" aria-label="Find a document" value={q} onChange={(e) => setQ(e.target.value)} />
                  {batch ? <div className="ws-progress" data-testid="batch">
                    <span>Cleaning · {batch.finished} of {batch.jobs} done{batch.left ? ` · ${batch.left}` : ""}</span>
                    <div className="ws-bar work" role="progressbar" aria-label="How far cleaning has got"
                         aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(batch.fraction * 100)}>
                      <div style={{ width: `${batch.fraction * 100}%` }} />
                    </div>
                  </div> : total > 0 && <div className="ws-progress">
                    <span>{checked} of {total} safe to share</span>
                    <div className="ws-bar"><div style={{ width: `${(checked / total) * 100}%` }} /></div>
                  </div>}
                </div>
                <div className="ws-drawer-scroll">
                  {GROUPS.map((g) => {
                    const rows = shown.filter((d) => g.tone.includes(tones.get(d.id)!));
                    return rows.length > 0 && (
                      <div key={g.title}>
                        <span className="ws-group-title">{g.title} · {rows.length}</span>
                        {rows.map((d) => (
                          <button type="button" key={d.id} className={`ws-file${d.id === sel ? " sel" : ""}`} onClick={() => setSel(d.id)}
                                  title={d.folder ? `${d.folder} / ${d.name}` : d.name} aria-current={d.id === sel}>
                            <span className={`ws-dot ${DOT[tones.get(d.id)!]}`} aria-hidden="true" />
                            <span className="name">{d.folder && <span className="folder">{d.folder} / </span>}{d.name}</span>
                          </button>
                        ))}
                      </div>
                    );
                  })}
                  {docs && !docs.length && <p className="ws-note" style={{ padding: 12 }}>No documents yet. Drop PDFs or photos anywhere in this window.</p>}
                </div>
              </nav>
              <Grip label="Resize the document list" width={leftW} setWidth={setLeftW} side="left" />
            </>
          ) : (
            <div className="ws-rail left">
              <button type="button" className="wbtn quiet icon" onClick={() => setLeftOpen(true)} aria-label="Show the document list">›</button>
              <span>{total}<br />docs</span>
            </div>
          )}

          {current ? (
            <DocView key={current.id} summary={current} hasProfile={hasProfile} zoom={zoom} setZoom={setZoom}
                     side={side} setSide={setSide} onNext={() => next && setSel(next.id)} nextName={next?.name ?? null}
                     onChanged={refresh} />
          ) : (
            <section className="ws-doc" style={{ justifyContent: "center" }}>
              <div className="ws-empty">
                <strong>{docs ? "Add your first document" : "Loading…"}</strong>
                {docs && <><span>Any document works. Your W-2 or 1040 is a good start: most of your details are at the top.</span>
                  <button type="button" className="wbtn primary" onClick={() => fileInput.current?.click()}>Add documents…</button>
                  <span style={{ fontSize: 12 }}>Or drop PDFs and photos anywhere in this window.</span></>}
              </div>
            </section>
          )}

          {rightOpen ? (
            <>
              <Grip label="Resize My profile" width={rightW} setWidth={setRightW} side="right" />
              <aside className="ws-drawer right" style={{ width: rightW }} aria-label="My profile">
                <div className="ws-drawer-head">
                  <strong>My profile</strong>
                  <button type="button" className="wbtn quiet" onClick={() => setRightOpen(false)} aria-label="Hide My profile">Hide ›</button>
                </div>
                <ProfileDrawer profile={profile} onOpen={() => setView("profile")} />
              </aside>
            </>
          ) : (
            <div className="ws-rail right">
              <button type="button" className="wbtn quiet icon" onClick={() => setRightOpen(true)} aria-label="Show My profile">‹</button>
              <span>My<br />profile</span>
            </div>
          )}
        </div>
      )}

      {safeOpen && (
        <div className="ws-scrim" onClick={(e) => e.currentTarget === e.target && setSafeOpen(false)}>
          <div className="ws-sheet" role="dialog" aria-label="Safe copies">
            <div className="ws-drawer-head" style={{ padding: "14px 10px 10px 16px", borderBottom: "1px solid var(--w-line)" }}>
              <div><strong style={{ fontSize: 15 }}>Safe copies</strong><div style={{ color: "var(--w-muted)", fontSize: 12 }}>{checked} of {total} documents are checked and safe to share</div></div>
              <button type="button" className="wbtn quiet" onClick={() => setSafeOpen(false)}>Close</button>
            </div>
            <div style={{ display: "flex", gap: 6, padding: "10px 16px", borderBottom: "1px solid var(--w-line)" }}>
              <button type="button" className="wbtn primary" disabled={!ready.some((d) => !d.exported)}
                      onClick={async () => {
                        for (const d of ready.filter((x) => !x.exported)) await api.export(d.id).catch(() => undefined);
                        setToast("Saved to your Safe copies folder. File › Show Safe Copies in Finder opens it.");
                        refresh();
                      }}>Save all to the Safe copies folder</button>
            </div>
            <div className="ws-drawer-scroll" style={{ padding: "4px 10px 20px" }}>
              <span className="ws-group-title">Ready to share · {ready.length}</span>
              {ready.map((d) => (
                <div key={d.id} className="ws-file" style={{ height: 32 }}>
                  <span className="ws-dot ok" aria-hidden="true" /><span className="name">{d.name}</span>
                  <span style={{ fontSize: 11, color: "var(--w-muted)" }}>{d.exported ? "Saved" : "Not saved yet"}</span>
                </div>
              ))}
              {(docs ?? []).length > ready.length && <>
                <span className="ws-group-title">Not ready yet · {(docs ?? []).length - ready.length}</span>
                {(docs ?? []).filter((d) => d.status !== "passed").map((d) => (
                  <button type="button" key={d.id} className="ws-file" onClick={() => { setSel(d.id); setSafeOpen(false); }}>
                    <span className={`ws-dot ${DOT[tones.get(d.id)!]}`} aria-hidden="true" /><span className="name">{d.name}</span>
                    <span style={{ fontSize: 11, color: "var(--w-muted)" }}>Open</span>
                  </button>
                ))}
                <p className="ws-note">Each document gets its safe copy as soon as it's checked. Nothing waits for the whole set.</p>
              </>}
            </div>
          </div>
        </div>
      )}
      {welcome && <Welcome onClose={(hide) => { store.set("welcome-hidden", hide); setWelcome(false); }} />}
      {dropping && <div className="ws-dropping">Drop to add these documents</div>}
      {toast && <div className="ws-toast" role="status"><span>{toast}</span>
        <button type="button" className="wbtn quiet" style={{ color: "inherit" }} onClick={() => setToast(null)}>OK</button></div>}
    </div>
  );
}
