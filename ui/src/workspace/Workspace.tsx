// The workspace: files on the left, the document in the middle, My profile on the right.
// Talks to the same local API as the classic review screen (../App.tsx).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, DragEvent, KeyboardEvent, PointerEvent as RPointerEvent } from "react";
import { api, pageImage } from "../api";
import type { Box, DocDetail, DocSummary, FoundEntries, Profile } from "../api";
import { Details } from "../Details";
import { AddForm, WordLayer, popoverPlace, usePageWords } from "./ClickToAdd";
import type { Picked } from "./ClickToAdd";
import { DOT, checked, plain, removedList, timeLeft } from "./messages";
import type { Tone } from "./messages";
import "./workspace.css";

const ACCEPT = ".pdf,.jpg,.jpeg,.png,.tif,.tiff,.heic,.heif,.webp";
// zoom: a scale, or fit the page's width or the whole page to the window
export type Zoom = number | "width" | "page";
const STEPS = [0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4];
const clampScale = (k: number) => Math.max(0.25, Math.min(4, k));
export const zoomStep = (k: number, up: boolean) =>
  up ? STEPS.find((s) => s > k * 1.01) ?? 4 : [...STEPS].reverse().find((s) => s < k * 0.99) ?? 0.25;
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
  { tone: ["look", "stop"], title: "Needs review" },
  { tone: ["work"], title: "Working" },
  { tone: ["new"], title: "Not cleaned yet" },
  { tone: ["ok"], title: "Checked" },
];

// in the Mac app's window (tools/native_window.py): its toolbar has the buttons and drags
// the window, so the page's top band holds nothing clickable. Kept for this tab, like the token.
const NATIVE = (() => {
  const asked = new URL(window.location.href).searchParams.get("native") === "1";
  try {
    if (asked) sessionStorage.setItem("taxonymous-native", "1");
    return asked || sessionStorage.getItem("taxonymous-native") === "1";
  } catch {
    return asked;
  }
})();

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
    ["Tell it what to protect, once", "Add a document and click your name, SSN and account numbers on it. Or type them into My profile."],
    ["Removed from every document", "Each document you add is cleaned of them automatically, however they're printed, scans and photos included."],
    ["Review", "Spot something else of yours? Click to remove it everywhere, or draw a box to remove it from that document only."],
    ["Checked again", "Every copy is read again from scratch, the way a person or an AI would. It's only ready when none of your details turn up."],
    ["Share the safe copies", "Save them and drag them into your AI assistant. Your originals never change."],
  ];
  return (
    <div className={`ws-welcome${NATIVE ? " native" : ""}`} role="dialog" aria-label="Welcome to Taxonymous">
      <div className="ws-welcome-inner">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <h1>Share your tax documents with AI, without sharing who you are.</h1>
          <p style={{ fontSize: 15, color: "var(--w-muted)" }}>
            Remove your identity. Preserve your financial context.
          </p>
        </div>
        <div className="ws-local-banner">
          <span className="ws-lock" aria-hidden="true">
            <svg width="24" height="24" viewBox="0 0 16 16">
              <rect x="3" y="7" width="10" height="7" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
              <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" fill="none" stroke="currentColor" strokeWidth="1.3" />
            </svg>
          </span>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <strong>Everything stays on this computer</strong>
            <p>Your documents and details are never uploaded. You can even turn off your Wi-Fi and Taxonymous will still work.</p>
            <p className="strong">You're always in control of your sensitive information: only the safe copies you choose to share ever leave this computer.</p>
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

// where one profile entry was removed: chosen in My profile, outlined in the document
export interface Highlight {
  entry: string;
  label: string;
  placeholders: string[];
}

type Entry = [string, string, string]; // label, value (masked), entry id ("person:0", "acct:1")

function Where({ entry, label, found, onShow }: {
  entry: string; label: string; found: FoundEntries | null; onShow: (h: Highlight, doc: string) => void;
}) {
  const f = found?.[entry];
  if (!found) return <div className="ws-where">Looking…</div>;
  if (!f || !f.total) return <div className="ws-where">Not removed from any document yet.</div>;
  const docs = f.documents.length;
  return (
    <div className="ws-where" data-testid="where">
      <span>Removed from {docs} document{docs === 1 ? "" : "s"} · {f.total} place{f.total === 1 ? "" : "s"}</span>
      {f.documents.map((d) => (
        <button type="button" key={d.id} className="ws-where-doc"
                onClick={() => onShow({ entry, label, placeholders: f.placeholders }, d.id)}>
          <span className="name">{d.name}</span>
          <small>{d.places} · page{d.pages.length === 1 ? "" : "s"} {pageList(d.pages)}</small>
        </button>
      ))}
    </div>
  );
}

const pageList = (ps: number[]) => {
  const out: string[] = [];
  for (let i = 0; i < ps.length; i++) {
    let j = i;
    while (j + 1 < ps.length && ps[j + 1] === ps[j] + 1) j++;
    out.push(j > i ? `${ps[i]}–${ps[j]}` : `${ps[i]}`);
    i = j;
  }
  return out.join(", ");
};

function ProfileDrawer({ profile, found, onOpen, onShow }: {
  profile: Profile | null; found: FoundEntries | null; onOpen: () => void; onShow: (h: Highlight, doc: string) => void;
}) {
  const groups: { title: string; items: Entry[]; person?: boolean }[] = [];
  if (profile) {
    profile.people.forEach((p, i) => {
      const e = `person:${i}`;
      const items: Entry[] = [["Name", p.name, e]];
      if (p.ssn) items.push(["SSN", mask(p.ssn), e]);
      if (p.dob) items.push(["Born", mask(p.dob), e]);
      if (p.employer) items.push(["Employer", p.employer, e]);
      groups.push({ title: ({ taxpayer: "You", spouse: "Spouse", dependent: "Dependent" } as Record<string, string>)[p.role] ?? "Person", items, person: true });
    });
    if (profile.addresses.length) {
      groups.push({ title: "Addresses", items: profile.addresses.map((a, i) => ["Address", [a.street, a.city, a.state, a.zip].filter(Boolean).join(", "), `addr:${i}`]) });
    }
    if (profile.accounts.length) {
      groups.push({ title: "Accounts", items: profile.accounts.map((a, i) => [a.institution || "Account", mask(a.number), `acct:${i}`]) });
    }
    if (profile.phones.length) groups.push({ title: "Phones", items: profile.phones.map((x, i) => ["Phone", x, `phone:${i}`]) });
    if (profile.emails.length) groups.push({ title: "Emails", items: profile.emails.map((x, i) => ["Email", x, `email:${i}`]) });
    if (profile.cards.length) groups.push({ title: "Cards", items: profile.cards.map((c, i) => [c.description || "Card", mask(c.number), `card:${i}`]) });
    if (profile.eins.length) groups.push({ title: "Your EINs", items: profile.eins.map((e, i) => [e.description || "EIN", mask(e.ein), `ein:${i}`]) });
    if (profile.numbers.length) groups.push({ title: "Other numbers", items: profile.numbers.map((x, i) => [x.description || "Number", mask(x.number), `id:${i}`]) });
    if (profile.preparers.length) groups.push({ title: "Tax preparer", items: profile.preparers.map((x, i) => ["Preparer", x.name || x.firm, `preparer:${i}`]) });
  }
  const n = groups.reduce((a, g) => a + g.items.length, 0);
  const [all, setAll] = useState(() => store.get("profile-all", false));
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => store.set("profile-all", all), [all]);
  const places = (entry: string) => found?.[entry]?.total ?? 0;
  const toggle = (entry: string) => setOpen(open === entry ? null : entry);
  return (
    <>
      <div className="ws-drawer-scroll" style={{ padding: "4px 8px 16px" }}>
        <p className="ws-note">Removed from <strong>all documents</strong>, in any format it's printed. Click one to see where.</p>
        {!n && <p className="ws-note" style={{ border: "1px dashed var(--w-line-strong)", borderRadius: 8, padding: 12, margin: "8px 6px" }}>
          Nothing yet. Choose Click a value and click your name on a document, or open My profile to type it.</p>}
        {n > 0 && !all && (
          <div className="ws-psummary" data-testid="profile-summary">
            {groups.map((g, i) => {
              const one = g.person || g.items.length === 1;
              const entry = g.items[0][2];
              return (
                <div key={`${g.title}${i}`}>
                  <button type="button" className={`ws-prow${one && open === entry ? " on" : ""}`}
                          onClick={() => (one ? toggle(entry) : setAll(true))} aria-expanded={one ? open === entry : undefined}>
                    <span>{g.title}</span>
                    <strong>{one ? g.items[0][1] : `${g.items.length} ${g.title.toLowerCase()}`}</strong>
                    {one && found && <em>{places(entry)}</em>}
                  </button>
                  {one && open === entry && <Where entry={entry} label={g.items[0][1]} found={found} onShow={onShow} />}
                </div>
              );
            })}
            <button type="button" className="wbtn quiet" onClick={() => setAll(true)}>Show all {n} details</button>
          </div>
        )}
        {n > 0 && all && <button type="button" className="wbtn quiet" style={{ margin: "2px 0 0" }} onClick={() => setAll(false)}>Show less</button>}
        {all && groups.map((g, i) => (
          <div className="ws-pgroup" key={`${g.title}${i}`}>
            <span>{g.title}</span>
            {(g.person ? [g.items] : g.items.map((x) => [x])).map((rows, j) => {
              const entry = rows[0][2];
              return (
                <div key={j}>
                  <button type="button" className={`ws-entry${open === entry ? " on" : ""}`} onClick={() => toggle(entry)}
                          aria-expanded={open === entry}>
                    <span className="rows">{rows.map(([k, v]) => <span key={k} className="row"><span className="k">{k}</span><span className="v">{v}</span></span>)}</span>
                    {found && <em>{places(entry)}</em>}
                  </button>
                  {open === entry && <Where entry={entry} label={rows[0][1]} found={found} onShow={onShow} />}
                </div>
              );
            })}
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

function DocView({ summary, hasProfile, zoom, setZoom, scaleNow, side, setSide, onChanged, onAdded,
                   allDone, onFinish, finishLabel, highlight, onClearHighlight, showName }: {
  summary: DocSummary; hasProfile: boolean; zoom: Zoom; setZoom: (z: Zoom) => void;
  scaleNow: { current: number };
  side: "redacted" | "original"; setSide: (s: "redacted" | "original") => void;
  onChanged: () => void; onAdded: (queued: string[]) => void;
  allDone: boolean; onFinish: () => void; finishLabel: string; // all checked: the next step is saving them
  highlight: Highlight | null; onClearHighlight: () => void; // a profile entry's boxes, outlined
  showName: boolean; // the file name, only while the documents list (which shows it) is hidden
}) {
  const [detail, setDetail] = useState<DocDetail | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [picking, setPicking] = useState(false);
  const [labelsKnown, setLabelsKnown] = useState(() => store.get("labels-known", false));
  const [picked, setPicked] = useState<Picked | null>(null);
  const [pending, setPending] = useState<Box[]>([]);
  const [drag, setDrag] = useState<{ page: number; x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const id = summary.id;

  useEffect(() => {
    let live = true;
    api.document(id).then((d) => live && setDetail(d)).catch((e) => live && setError((e as Error).message));
    return () => { live = false; };
  }, [id, summary.version, summary.status]);
  useEffect(() => { setPending([]); setDrawing(false); setPicking(false); setPicked(null); }, [id]);
  const { words, reading, failed } = usePageWords(id, detail?.pages.length ?? 0, picking);
  useEffect(() => { if (failed) setError(failed); }, [failed]);

  // a click picks a word (the engine grows it to the whole value); shift-click adds the words up to it
  const pick = async (page: number, i: number, extend: boolean) => {
    let chosen = [i];
    if (extend && picked && picked.page === page) {
      const all = [...picked.words, i];
      const lo = Math.min(...all), hi = Math.max(...all);
      chosen = Array.from({ length: hi - lo + 1 }, (_, k) => lo + k);
    }
    setPicked({ page, words: chosen, suggestion: null });
    try {
      const s = await api.suggest(id, page, chosen);
      setPicked((p) => (p && p.page === page && p.words.join() === chosen.join()
        ? { page, words: s.words, suggestion: s } : p));
    } catch (e) {
      setPicked(null);
      setError((e as Error).message);
    }
  };

  const msg = plain(summary, hasProfile);
  const processed = summary.version > 0;
  const showing = processed ? side : "original";
  // the scale pages are drawn at: fitted to the viewer's size, or as chosen
  const viewer = useRef<HTMLDivElement>(null);
  const [room, setRoom] = useState<[number, number]>([0, 0]);
  useEffect(() => {
    const el = viewer.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setRoom([el.clientWidth, el.clientHeight]));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const widest = Math.max(1, ...(detail?.pages ?? []).map(([w]) => w));
  const tallest = Math.max(1, ...(detail?.pages ?? []).map(([, h]) => h));
  const fitWidth = room[0] ? Math.min(2, (room[0] - 48) / widest) : 1;
  const fitPage = room[1] ? Math.min(fitWidth, (room[1] - 40) / tallest) : 1;
  const scale = clampScale(zoom === "width" ? fitWidth : zoom === "page" ? fitPage : zoom);
  const scaleRef = useRef(scale);
  scaleRef.current = scale;
  scaleNow.current = scale;
  // page images follow the scale once it settles (a pinch changes it many times a second)
  const [settled, setSettled] = useState(scale);
  useEffect(() => {
    const t = setTimeout(() => setSettled(scale), 250);
    return () => clearTimeout(t);
  }, [scale]);
  const dpi = Math.min(288, Math.max(72, Math.round((72 * settled * (window.devicePixelRatio || 1)) / 24) * 24));

  // trackpad pinch (and ⌘ or ctrl with the scroll wheel) zooms the document, around the pointer
  useEffect(() => {
    const el = viewer.current;
    if (!el) return;
    const zoomAt = (k: number, x: number, y: number) => {
      const before = scaleRef.current, after = clampScale(k);
      const r = el.getBoundingClientRect();
      const px = x - r.left, py = y - r.top;
      const cx = (el.scrollLeft + px) / before, cy = (el.scrollTop + py) / before;
      scaleRef.current = after;
      setZoom(after);
      requestAnimationFrame(() => { el.scrollLeft = cx * after - px; el.scrollTop = cy * after - py; });
    };
    const wheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      zoomAt(scaleRef.current * Math.exp(-e.deltaY * 0.01), e.clientX, e.clientY);
    };
    // Safari and the Mac app's web view report a pinch as gesture events
    let start = 1;
    type Gesture = Event & { scale: number; clientX: number; clientY: number };
    const gstart = (e: Event) => { e.preventDefault(); start = scaleRef.current; };
    const gchange = (e: Event) => {
      e.preventDefault();
      const g = e as Gesture;
      zoomAt(start * g.scale, g.clientX, g.clientY);
    };
    el.addEventListener("wheel", wheel, { passive: false });
    el.addEventListener("gesturestart", gstart);
    el.addEventListener("gesturechange", gchange);
    return () => {
      el.removeEventListener("wheel", wheel);
      el.removeEventListener("gesturestart", gstart);
      el.removeEventListener("gesturechange", gchange);
    };
  }, [setZoom]);

  // the highlighted entry's boxes in this document, and stepping through them
  const marks = useMemo(() => (highlight && detail ? detail.regions.filter((r) => highlight.placeholders.includes(r.placeholder))
    .sort((a, b) => a.page - b.page || a.rect[1] - b.rect[1]) : []), [highlight, detail]);
  const at = useRef(-1);
  const jump = useCallback((step: number) => {
    const el = viewer.current;
    if (!el || !marks.length) return;
    at.current = (at.current + step + marks.length) % marks.length;
    const m = marks[at.current];
    const page = el.querySelector<HTMLElement>(`[data-page="${m.page}"]`);
    if (page) el.scrollTo({ top: page.offsetTop + m.rect[1] * scaleRef.current - el.clientHeight / 3, behavior: "smooth" });
  }, [marks]);
  useEffect(() => { at.current = -1; if (marks.length) requestAnimationFrame(() => jump(1)); }, [marks, jump]);
  useEffect(() => { if (highlight) { setPicking(false); setDrawing(false); setPicked(null); } }, [highlight]);

  const toPoint = (e: RPointerEvent<HTMLDivElement>, w: number) => {
    const r = e.currentTarget.getBoundingClientRect();
    const k = r.width / w;
    return [(e.clientX - r.left) / k, (e.clientY - r.top) / k];
  };
  const apply = async () => {
    try {
      await api.addBoxes(id, pending);
      // the saved boxes first, then the dashed ones go: never a moment without the box
      setDetail(await api.document(id));
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
          <div style={{ minWidth: 0, flex: 1, display: "flex", flexDirection: "column", gap: 6 }}>
            {showName && <span className="file">{summary.folder ? `${summary.folder.replaceAll("/", " / ")} / ` : ""}{summary.name}</span>}
            <div className={`ws-banner compact ${msg.tone}`} role="status" data-testid="status">
              <span className={`ws-dot ${DOT[msg.tone]}`} aria-hidden="true" />
              <div style={{ flex: 1, minWidth: 0 }}>
                <strong>{msg.title}</strong>
                <span className="sub">{msg.body}</span>
                {summary.status === "passed" && detail?.report && (
                  <details className="ws-checked" data-testid="checked">
                    <summary>What was checked</summary>
                    <ul>{checked(detail.report, detail.redactions).map(([b, rest]) =>
                      <li key={b + rest}>{b && <strong>{b}</strong>} {rest}</li>)}</ul>
                    <p>Only what's in My profile, and anything shaped like an SSN, is removed. Look the copy over
                      before you share it.</p>
                  </details>
                )}
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
            {allDone && <button type="button" className="wbtn primary" onClick={onFinish}>{finishLabel}</button>}
          </div>
        </div>
        <div className="ws-tools">
          <div className="ws-modes" role="group" aria-label="Remove more">
            <button type="button" className={`ws-mode${picking ? " on" : ""}`} aria-pressed={picking}
                    disabled={!detail?.pages.length}
                    onClick={() => { setPicking(!picking); setPicked(null); setDrawing(false); }}>
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2l8 6-3.5.8L10.5 13l-1.8.8-2-4.2L4 12z" fill="currentColor" /></svg>
              <span><strong>Click a value</strong><small>From all documents</small></span>
            </button>
            <button type="button" className={`ws-mode${drawing ? " on" : ""}`}
                    onClick={() => { setDrawing(!drawing); setPicking(false); setPicked(null); }}
                    disabled={!processed} aria-pressed={drawing}>
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="4" width="11" height="8" rx="1" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="2.5 1.5" /></svg>
              <span><strong>Draw a box</strong><small>This document only</small></span>
            </button>
          </div>
          <span style={{ flex: 1 }} />
          <div className="seg" role="group" aria-label="Show">
            <button type="button" className={showing === "original" ? "on" : ""} onClick={() => setSide("original")}>Original</button>
            <button type="button" className={showing === "redacted" ? "on" : ""} onClick={() => setSide("redacted")} disabled={!processed}>Safe copy</button>
          </div>
          <div className="seg" role="group" aria-label="Fit">
            <button type="button" className={zoom === "width" ? "on" : ""} onClick={() => setZoom("width")}>Fit width</button>
            <button type="button" className={zoom === "page" ? "on" : ""} onClick={() => setZoom("page")}>Fit page</button>
          </div>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
            <button type="button" className="wbtn icon" aria-label="Zoom out" onClick={() => setZoom(zoomStep(scale, false))}>−</button>
            <span style={{ minWidth: 40, textAlign: "center", fontSize: 12, fontWeight: 600 }} aria-label="Zoom">{Math.round(scale * 100)}%</span>
            <button type="button" className="wbtn icon" aria-label="Zoom in" onClick={() => setZoom(zoomStep(scale, true))}>+</button>
          </div>
        </div>
        {error && <div className="ws-banner stop" role="alert"><div><strong>That didn't work</strong><span>{error}</span></div>
          <button type="button" className="wbtn quiet" onClick={() => setError(null)} style={{ marginLeft: "auto" }}>OK</button></div>}
      </div>
      {marks.length > 0 && highlight && (
        <div className="ws-hintbar where" role="status" data-testid="highlight">
          <span>Outlined: where <strong>{highlight.label}</strong> was removed in this document, {marks.length} box{marks.length === 1 ? "" : "es"}.</span>
          <button type="button" className="wbtn quiet" onClick={() => jump(1)}>Next ›</button>
          <button type="button" className="wbtn quiet" onClick={onClearHighlight}>Clear</button>
        </div>
      )}
      {(drawing || picking) && (
        <div className="ws-hintbar" role="status">
          <span>{drawing ? "Drag over anything. It's blacked out on this document only."
            : reading !== null && !words.has(reading) ? `Reading page ${reading + 1}…`
              : "Click your name, an account number, or anything else of yours. Shift-click adds the next word."}</span>
          {pending.length > 0 && <>
            <button type="button" className="wbtn primary" onClick={apply}>Apply {pending.length} box{pending.length === 1 ? "" : "es"} and check again</button>
            <button type="button" className="wbtn quiet" onClick={() => setPending(pending.slice(0, -1))}>Undo</button>
          </>}
          <button type="button" className="wbtn quiet" onClick={() => { setDrawing(false); setPicking(false); setPicked(null); }}>Done</button>
        </div>
      )}
      <div className="ws-viewer" ref={viewer}>
        {!detail && <div className="ws-empty">Loading…</div>}
        {detail?.pages.map(([w, h], n) => {
          // drawn boxes as saved, not as the last run drew them: they stay while it runs again
          const local = detail.added.filter((r) => r.page === n);
          const others = showing === "original" ? detail.regions.filter((r) => r.page === n && r.source !== "manual") : [];
          const box = (rect: number[]): CSSProperties => ({
            left: `${(rect[0] / w) * 100}%`, top: `${(rect[1] / h) * 100}%`,
            width: `${((rect[2] - rect[0]) / w) * 100}%`, height: `${((rect[3] - rect[1]) / h) * 100}%`,
          });
          return (
            <div key={n} data-page={n} className={`ws-page${drawing ? " drawing" : ""}${picking ? " picking" : ""}`}
                 style={{ width: w * scale, height: h * scale }}
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
              {marks.filter((r) => r.page === n).map((r, i) => <div key={`h${i}`} className="ws-mark" style={box(r.rect)} />)}
              {pending.filter((b) => b.page === n).map((b, i) => <div key={`p${i}`} className="ws-local pending" style={box(b.rect)}>THIS DOC ONLY</div>)}
              {drag && drag.page === n && <div className="ws-drag" style={box([Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1),
                Math.max(drag.x0, drag.x1), Math.max(drag.y0, drag.y1)])} />}
              {picking && words.has(n) && <WordLayer words={words.get(n)!} size={[w, h]}
                regions={detail.regions.filter((r) => r.page === n)}
                picked={picked?.page === n ? picked.words : []} onPick={(i, extend) => pick(n, i, extend)} />}
              {picking && picked?.page === n && words.has(n) &&
                <AddForm key={picked.words.join()} picked={picked} docId={id}
                         style={popoverPlace(words.get(n)!, picked.words, [w, h])}
                         onCancel={() => setPicked(null)}
                         onAdded={(queued) => { setPicked(null); onAdded(queued); }} />}
            </div>
          );
        })}
        {detail && (
          <div className="ws-legend">
            <span><span className="ws-swatch" style={{ background: "#1d1d1b" }} />From My profile: removed from all documents</span>
            <span><span className="ws-swatch" style={{ background: "repeating-linear-gradient(135deg,#1f4f80 0 3px,#3a7bc0 3px 6px)" }} />Drawn: this document only</span>
            <span style={{ flexBasis: "100%" }}>In the safe copy both are solid black. The stripes are only here, so you can tell them apart.</span>
            {!labelsKnown && <span className="ws-labels-note" style={{ flexBasis: "100%" }}>
              Each box in the safe copy carries a label like &lt;NAME_A&gt;. It tells your AI which details belong to the
              same person or account, across all your documents. Your real details aren't in it.
              <button type="button" className="wbtn quiet" onClick={() => { store.set("labels-known", true); setLabelsKnown(true); }}>Got it</button>
            </span>}
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
  const [rightW, setRightW] = useState(() => store.get("right-w", 260));
  const [leftOpen, setLeftOpen] = useState(() => store.get("left-open", true));
  // My profile starts hidden on a narrower window, so the document has the room; the edge shows its count
  const [rightOpen, setRightOpen] = useState(() => store.get("right-open", window.innerWidth >= 1280));
  const [zoom, setZoom] = useState<Zoom>(() => store.get<Zoom>("zoom-v2", "width"));
  const scaleNow = useRef(1); // the open document's scale, for the menu's zoom in and out
  const [side, setSide] = useState<"redacted" | "original">("redacted");
  const [q, setQ] = useState("");
  const [safeOpen, setSafeOpen] = useState(false);
  const [welcome, setWelcome] = useState(() => !store.get("welcome-hidden", false));
  const [dropping, setDropping] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [found, setFound] = useState<FoundEntries | null>(null);
  const [highlight, setHighlight] = useState<Highlight | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => store.set("left-w", leftW), [leftW]);
  useEffect(() => store.set("right-w", rightW), [rightW]);
  useEffect(() => store.set("left-open", leftOpen), [leftOpen]);
  useEffect(() => store.set("right-open", rightOpen), [rightOpen]);
  useEffect(() => store.set("zoom-v2", zoom), [zoom]);

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
  // where each profile entry was removed: read again whenever a document's output changes
  const outputs = (docs ?? []).map((d) => `${d.id}:${d.version}:${d.status}`).join("|");
  useEffect(() => {
    api.found().then((f) => setFound(f.entries)).catch(() => undefined);
  }, [outputs, profile]);
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
    switch (c) {
      case "add": fileInput.current?.click(); break;
      case "toggle-files": setLeftOpen((o) => !o); break;
      case "toggle-profile": setRightOpen((o) => !o); break;
      case "zoom-in": setZoom(zoomStep(scaleNow.current, true)); break;
      case "zoom-out": setZoom(zoomStep(scaleNow.current, false)); break;
      case "zoom-reset": setZoom("width"); break;
      case "next": if (next) setSel(next.id); break;
      case "prev": if (prev) setSel(prev.id); break;
      case "original": setSide("original"); break;
      case "safe": setSide("redacted"); break;
      case "safe-copies": setWelcome(false); setSafeOpen(true); break;
      // My Profile goes to the profile from wherever you are: over the welcome screen or a
      // sheet it would only change what's behind them
      case "profile": setWelcome(false); setSafeOpen(false); setView("profile"); loadProfile(); break;
      case "profile-toggle":
        setSafeOpen(false);
        setView((v) => (v === "profile" && !welcome ? "work" : "profile"));
        setWelcome(false);
        loadProfile();
        break;
      case "welcome": setSafeOpen(false); setWelcome(true); break;
      case "documents": setWelcome(false); setSafeOpen(false); setView("work"); break;
      case "added": // files the native File menu copied into the inbox: clean the new ones
        api.documents().then(async (list) => {
          setDocs(list);
          if (hasProfile) for (const d of list.filter((x) => x.status === "new")) await api.run(d.id).catch(() => undefined);
          refresh();
        }).catch(() => undefined);
        break;
    }
  }, [next, prev, hasProfile, refresh, loadProfile, welcome]);
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
  const profileCount = profile ? profile.people.length + profile.addresses.length + profile.accounts.length
    + profile.phones.length + profile.emails.length + profile.cards.length + profile.eins.length
    + profile.numbers.length + profile.preparers.length : 0;
  const total = docs?.length ?? 0;
  const nChecked = ready.length;
  const allDone = total > 0 && nChecked === total;
  // the finish, in one place: save the checked copies (after a look at the summary), then find them
  const unsaved = ready.filter((d) => !d.exported).length;
  const copies = (n: number) => `${n} safe cop${n === 1 ? "y" : "ies"}`;
  const finishLabel = unsaved ? `Save ${copies(unsaved)}…` : `${nChecked} saved · Show in Finder`;
  const finish = () => (unsaved ? setSafeOpen(true)
    : api.reveal().catch(() => setToast("Choose File › Show Safe Copies in Finder.")));
  const removedAll = removedList(ready.reduce((acc, d) => {
    for (const [k, n] of Object.entries(d.redactions ?? {})) acc[k] = (acc[k] ?? 0) + n;
    return acc;
  }, {} as Record<string, number>));
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
      {NATIVE ? null : (
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
      )}
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
                  <strong>Documents</strong>
                  <button type="button" className="wbtn quiet" onClick={() => setLeftOpen(false)} aria-label="Hide the document list">‹ Hide</button>
                </div>
                <div className="ws-drawer-tools">
                  <button type="button" className="wbtn primary" onClick={() => fileInput.current?.click()}>Add documents…</button>
                  <input className="search" type="search" placeholder="Filter by file name" aria-label="Filter by file name" value={q} onChange={(e) => setQ(e.target.value)} />
                  {batch ? <div className="ws-progress" data-testid="batch">
                    <span>Cleaning · {batch.finished} of {batch.jobs} done{batch.left ? ` · ${batch.left}` : ""}</span>
                    <div className="ws-bar work" role="progressbar" aria-label="How far cleaning has got"
                         aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(batch.fraction * 100)}>
                      <div style={{ width: `${batch.fraction * 100}%` }} />
                    </div>
                  </div> : total > 0 && (
                    // where things stand; it opens the summary (saving is the document header's button)
                    <button type="button" className={`ws-progress ws-status${allDone ? " done" : ""}`} data-testid="done"
                            onClick={() => setSafeOpen(true)} disabled={!nChecked}>
                      <span>{allDone && <span className="ws-dot ok" aria-hidden="true" />}
                        {allDone ? (total === 1 ? "Checked" : `All ${total} checked`) : `${nChecked} of ${total} checked`}
                        {nChecked > unsaved && ` · ${unsaved ? `${nChecked - unsaved} saved` : "all saved"}`}</span>
                      {!allDone && <div className="ws-bar"><div style={{ width: `${(nChecked / total) * 100}%` }} /></div>}
                    </button>
                  )}
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
                            {d.exported && <span className="ws-tag">Saved</span>}
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
                     scaleNow={scaleNow}
                     side={side} setSide={setSide}
                     allDone={allDone} onFinish={finish} finishLabel={finishLabel}
                     highlight={highlight} onClearHighlight={() => setHighlight(null)} showName={!leftOpen}
                     onChanged={refresh}
                     onAdded={(queued) => {
                       loadProfile();
                       refresh();
                       const others = queued.length - 1;
                       setToast(`Added to My profile. Cleaning this document again${others > 0
                         ? `, then ${others} other${others === 1 ? "" : "s"}` : ""}.`);
                     }} />
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
                <ProfileDrawer profile={profile} found={found} onOpen={() => setView("profile")}
                               onShow={(h, doc) => { setHighlight(h); setSel(doc); setSide("original"); }} />
              </aside>
            </>
          ) : (
            <div className="ws-rail right">
              <button type="button" className="wbtn quiet icon" onClick={() => setRightOpen(true)} aria-label="Show My profile">‹</button>
              <span>My<br />profile{profileCount > 0 && <><br /><strong>{profileCount}</strong></>}</span>
            </div>
          )}
        </div>
      )}

      {safeOpen && (
        <div className={`ws-scrim${NATIVE ? " native" : ""}`} onClick={(e) => e.currentTarget === e.target && setSafeOpen(false)}>
          <div className="ws-sheet" role="dialog" aria-label="Safe copies">
            <div className="ws-drawer-head" style={{ padding: "14px 10px 10px 16px", borderBottom: "1px solid var(--w-line)" }}>
              <div><strong style={{ fontSize: 15 }}>{allDone ? `All ${total} documents checked` : "Safe copies"}</strong>
                <div style={{ color: "var(--w-muted)", fontSize: 12 }}>{allDone
                  ? "Every copy was read again from scratch: none of your details were found."
                  : `${ready.length} of ${total} documents checked: none of your details found`}</div></div>
              <button type="button" className="wbtn quiet" onClick={() => setSafeOpen(false)}>Close</button>
            </div>
            <div className="ws-finish">
              {removedAll && <p>Removed across them: {removedAll}.</p>}
              <p className="small">Only what's in My profile, and anything shaped like an SSN, is removed. Have a quick look
                at each copy before you share it.</p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {unsaved ? (
                  <button type="button" className="wbtn primary" data-testid="save-all"
                          onClick={async () => {
                            const todo = ready.filter((x) => !x.exported);
                            let failed = 0;
                            for (const d of todo) await api.export(d.id).catch(() => { failed += 1; });
                            setToast(failed ? `${failed} couldn't be saved. Open them to see why.`
                              : `Saved ${todo.length} safe cop${todo.length === 1 ? "y" : "ies"}.`);
                            refresh();
                          }}>Save {ready.filter((d) => !d.exported).length} safe
                    cop{ready.filter((d) => !d.exported).length === 1 ? "y" : "ies"}</button>
                ) : ready.length > 0 && <>
                  <span className="ws-saved"><span className="ws-dot ok" aria-hidden="true" /> All saved</span>
                  <button type="button" className="wbtn primary"
                          onClick={() => api.reveal().catch(() => setToast("Choose File › Show Safe Copies in Finder."))}>
                    Show in Finder</button>
                </>}
              </div>
            </div>
            <div className="ws-drawer-scroll" style={{ padding: "4px 10px 20px" }}>
              <span className="ws-group-title">Checked · {ready.length}</span>
              {ready.map((d) => (
                <button type="button" key={d.id} className="ws-file ws-ready" onClick={() => { setSel(d.id); setSafeOpen(false); }}>
                  <span className="ws-dot ok" aria-hidden="true" />
                  <span className="name">{d.name}<small>{removedList(d.redactions) ? `Removed ${removedList(d.redactions)}` : "Nothing of yours on it"}</small></span>
                  <span style={{ fontSize: 11, color: "var(--w-muted)" }}>{d.exported ? "Saved" : "Not saved yet"}</span>
                </button>
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
      {welcome && <Welcome onClose={(hide) => { store.set("welcome-hidden", hide); setWelcome(false); setView("work"); }} />}
      {dropping && <div className="ws-dropping">Drop to add these documents</div>}
      {toast && <div className="ws-toast" role="status"><span>{toast}</span>
        <button type="button" className="wbtn quiet" style={{ color: "inherit" }} onClick={() => setToast(null)}>OK</button></div>}
    </div>
  );
}
