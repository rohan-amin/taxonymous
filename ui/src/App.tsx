import { useCallback, useEffect, useState } from "react";
import type { DragEvent } from "react";
import { api, busy, label, token } from "./api";
import type { DocSummary, Profile } from "./api";
import { Details } from "./Details";
import { DocTree, tree } from "./DocTree";
import { Viewer } from "./Viewer";

const ACCEPT = ".pdf,.jpg,.jpeg,.png,.tif,.tiff,.heic,.heif,.webp";

// what Your details lists, for the sidebar card: "2 people · 1 address · 3 accounts"
const COUNTED: [keyof Profile, string, string][] = [
  ["people", "person", "people"], ["addresses", "address", "addresses"], ["accounts", "account", "accounts"],
  ["eins", "EIN", "EINs"], ["cards", "card", "cards"], ["phones", "phone", "phones"], ["emails", "email", "emails"],
];

// which folders are folded: a convenience of this browser, so it may not be there
const FOLDED = "taxonymous-folded";

function loadFolded(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(FOLDED) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

function saveFolded(s: Set<string>) {
  try {
    localStorage.setItem(FOLDED, JSON.stringify([...s]));
  } catch {
    /* not kept: fine */
  }
}

function listed(p: Profile): string[] {
  return COUNTED.flatMap(([key, one, many]) => {
    const n = (p[key] as unknown[] | undefined)?.length ?? 0;
    return n ? [`${n} ${n === 1 ? one : many}`] : [];
  });
}

export function App() {
  const [docs, setDocs] = useState<DocSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [hasProfile, setHasProfile] = useState<boolean | null>(null);
  const [summary, setSummary] = useState<string[]>([]);
  const [view, setView] = useState<"documents" | "details">("documents");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [exporting, setExporting] = useState(false);
  const [exported, setExported] = useState<string | null>(null);
  const [pendingMarks, setPendingMarks] = useState(0); // in the open document, not yet applied
  const [folded, setFolded] = useState<Set<string>>(loadFolded);
  const [target, setTarget] = useState(""); // the folder Add files puts new files in; "" is inbox/

  const loadProfile = useCallback(() => api.profile().then((p) => {
    setHasProfile(p.exists);
    setSummary(listed(p.profile));
    return p;
  }), []);

  // until your details are saved there is nothing to redact against: start there
  useEffect(() => {
    loadProfile().then((p) => {
      if (!p.exists) setView("details");
    }).catch((err) => setError((err as Error).message));
  }, [loadProfile]);

  const refresh = useCallback(async () => {
    try {
      const list = await api.documents();
      setDocs(list);
      setError(null);
      setSelected((s) => s ?? list[0]?.id ?? null);
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // keep the list current: often while anything is being worked on, and now and then
  // otherwise, so runs started elsewhere (the CLI, another tab) show up too
  const working = !!docs?.some((d) => busy(d.status));
  useEffect(() => {
    const t = setInterval(refresh, working ? 1000 : 4000);
    return () => clearInterval(t);
  }, [working, refresh]);

  const upload = async (files: FileList | File[], folder = target) => {
    const errors: string[] = [];
    for (const f of Array.from(files)) {
      try {
        await api.upload(f, folder);
      } catch (err) {
        errors.push(`${f.name}: ${(err as Error).message}`);
      }
    }
    setError(errors.length ? errors.join("; ") : null);
    await refresh();
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) upload(e.dataTransfer.files);
  };

  if (!token) {
    return (
      <div className="fatal">
        Open this page from the link that <code>taxonymous serve</code> prints, so it carries the access token.
      </div>
    );
  }

  const current = docs?.find((d) => d.id === selected) ?? null;
  const runAll = async (folder = "") => {
    try {
      await api.runAll(folder);
      setView("documents");
    } catch (err) {
      setError((err as Error).message);
    }
    refresh();
  };
  // only documents that passed can be exported: a pick that no longer has (a re-run) drops out
  const exportable = (d: DocSummary) => d.status === "passed";
  const passed = docs?.filter(exportable) ?? [];
  const chosen = passed.filter((d) => picked.has(d.id));
  const pick = (ids: string[], on: boolean) => setPicked((p) => {
    const next = new Set(p);
    for (const id of ids) {
      if (on) next.add(id); else next.delete(id);
    }
    return next;
  });
  const fold = (path: string) => setFolded((f) => {
    const next = new Set(f);
    if (next.has(path)) next.delete(path); else next.add(path);
    saveFolded(next);
    return next;
  });
  const exportChosen = async () => {
    setExporting(true);
    const done: string[] = [];
    const failed: string[] = [];
    for (const d of chosen) {
      if (d.id === selected && view === "documents" && pendingMarks) {
        failed.push(`${label(d)}: apply your marks with Final scrub first`);
        continue;
      }
      try {
        await api.export(d.id);
        done.push(d.id);
      } catch (err) {
        failed.push(`${label(d)}: ${(err as Error).message}`);
      }
    }
    setExporting(false);
    setPicked(new Set(failed.length ? chosen.filter((d) => !done.includes(d.id)).map((d) => d.id) : []));
    setExported(done.length ? `Exported ${done.length} to ready-to-upload/` : null);
    setError(failed.length ? `Not exported: ${failed.join("; ")}` : null);
    refresh();
  };

  return (
    <div className={`app${dragging ? " dragging" : ""}`} onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
         onDragLeave={(e) => e.currentTarget === e.target && setDragging(false)} onDrop={onDrop}>
      <aside className="sidebar">
        <div className="brand">Taxonymous</div>
        <button type="button" className={`side-details${view === "details" ? " active" : ""}`}
                onClick={() => setView("details")} data-testid="details-card"
                title="What is removed from every document">
          <span className="side-details-head">
            <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="5" r="3" /><path d="M2 15c0-3.3 2.7-5 6-5s6 1.7 6 5" /></svg>
            <strong>Your details</strong>
            {hasProfile === false ? <span className="badge needed">needed</span> : <span className="edit">Edit<span aria-hidden="true"> ›</span></span>}
          </span>
          <span className="side-details-sum">
            {summary.length ? summary.map((s, i) => <span key={s}>{i ? " · " : ""}<span className="nowrap">{s}</span></span>)
              : "Add yourself first: what you list is removed"}
          </span>
        </button>
        <div className="side-actions">
          <label className="button">
            Add files
            <input type="file" multiple accept={ACCEPT} hidden
                   onChange={(e) => e.target.files && upload(e.target.files)} />
          </label>
          <button type="button" onClick={() => runAll()} disabled={!docs?.length || !hasProfile}
                  title={hasProfile ? "Redact and verify every document" : "Add your details first"}>Run all</button>
        </div>
        {target && (
          <div className="side-target muted" data-testid="target">
            New files go into <strong>{target.replaceAll("/", " / ")}</strong>
            <button type="button" className="link" onClick={() => setTarget("")}
                    title="Put new files in inbox/ itself">inbox instead</button>
          </div>
        )}
        {passed.length > 0 && (
          <div className="side-export">
            <label title="Pick every document that passed verification">
              <input type="checkbox" checked={chosen.length === passed.length}
                     ref={(el) => { if (el) el.indeterminate = chosen.length > 0 && chosen.length < passed.length; }}
                     onChange={() => setPicked(new Set(chosen.length === passed.length ? [] : passed.map((d) => d.id)))} />
              All passed
            </label>
            <button type="button" className="export" onClick={exportChosen} disabled={!chosen.length || exporting}
                    title="Copy each picked document's redacted PDF to ready-to-upload/">
              {exporting ? "Exporting…" : `Export selected (${chosen.length})`}
            </button>
          </div>
        )}
        {exported && <div className="side-ok">{exported}</div>}
        {error && <div className="side-error">{error}</div>}
        <ul className="docs" data-testid="documents">
          {docs && <DocTree root={tree(docs)} selected={selected} active={view === "documents"} picked={picked}
                            collapsed={folded} target={target} canRun={!!hasProfile}
                            onSelect={(d) => { setSelected(d.id); setTarget(d.folder); setView("documents"); }}
                            onPick={pick} onFold={fold} onTarget={setTarget} onRun={runAll}
                            onDropInto={(path, files) => { setDragging(false); upload(files, path); }} />}
          {docs && !docs.length && <li className="muted hint">Drop PDFs or photos here, or use Add files.</li>}
        </ul>
        <div className="foot muted">Everything stays on this computer.</div>
      </aside>
      <main>
        {view === "details" ? <Details onSaved={loadProfile} onRunAll={runAll} />
          : current ? <Viewer key={current.id} summary={current} onChanged={refresh} onMarks={setPendingMarks}
                              onDetails={loadProfile} />
          : <div className="viewer empty">{docs ? "No document selected" : "Loading…"}</div>}
      </main>
    </div>
  );
}
