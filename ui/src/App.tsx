import { useCallback, useEffect, useState } from "react";
import type { DragEvent } from "react";
import { api, busy, token } from "./api";
import type { DocSummary } from "./api";
import { Details } from "./Details";
import { ProgressBar, Viewer } from "./Viewer";

const ACCEPT = ".pdf,.jpg,.jpeg,.png,.tif,.tiff,.heic,.heif,.webp";

export function App() {
  const [docs, setDocs] = useState<DocSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [hasProfile, setHasProfile] = useState<boolean | null>(null);
  const [view, setView] = useState<"documents" | "details">("documents");

  // until your details are saved there is nothing to redact against: start there
  useEffect(() => {
    api.profile().then((p) => {
      setHasProfile(p.exists);
      if (!p.exists) setView("details");
    }).catch((err) => setError((err as Error).message));
  }, []);

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

  const upload = async (files: FileList | File[]) => {
    const errors: string[] = [];
    for (const f of Array.from(files)) {
      try {
        await api.upload(f);
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
  const runAll = async () => {
    try {
      await api.runAll();
      setView("documents");
    } catch (err) {
      setError((err as Error).message);
    }
    refresh();
  };
  const counts = (d: DocSummary) => Object.values(d.redactions).reduce((a, b) => a + b, 0);

  return (
    <div className={`app${dragging ? " dragging" : ""}`} onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
         onDragLeave={(e) => e.currentTarget === e.target && setDragging(false)} onDrop={onDrop}>
      <aside className="sidebar">
        <div className="brand">Taxonymous</div>
        <button type="button" className={`side-details${view === "details" ? " active" : ""}`}
                onClick={() => setView("details")}>
          Your details
          {hasProfile === false && <span className="badge needed">needed</span>}
        </button>
        <div className="side-actions">
          <label className="button">
            Add files
            <input type="file" multiple accept={ACCEPT} hidden
                   onChange={(e) => e.target.files && upload(e.target.files)} />
          </label>
          <button type="button" onClick={runAll} disabled={!docs?.length || !hasProfile}
                  title={hasProfile ? "Redact and verify every document" : "Add your details first"}>Run all</button>
        </div>
        {error && <div className="side-error">{error}</div>}
        <ul className="docs" data-testid="documents">
          {docs?.map((d) => (
            <li key={d.id}>
              <button type="button" className={d.id === selected && view === "documents" ? "active" : ""}
                      onClick={() => { setSelected(d.id); setView("documents"); }}>
                <span className="name">{d.name}</span>
                <span className="meta">
                  <span className={`badge ${d.status}`}>{d.status}</span>
                  {d.exported && <span className="badge exported">exported</span>}
                  {d.version > 0 && <span className="muted">{counts(d)} redacted</span>}
                </span>
                {d.progress && <ProgressBar p={d.progress} compact />}
              </button>
            </li>
          ))}
          {docs && !docs.length && <li className="muted hint">Drop PDFs or photos here, or use Add files.</li>}
        </ul>
        <div className="foot muted">Everything stays on this computer.</div>
      </aside>
      <main>
        {view === "details" ? <Details onSaved={() => setHasProfile(true)} onRunAll={runAll} />
          : current ? <Viewer key={current.id} summary={current} onChanged={refresh} />
          : <div className="viewer empty">{docs ? "No document selected" : "Loading…"}</div>}
      </main>
    </div>
  );
}
