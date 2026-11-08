// The sidebar's document list: inbox/'s folders as headings you can fold, each with its
// documents (and folders) under it, then the documents in inbox/ itself.
import { useCallback, useEffect, useState } from "react";
import type { DragEvent } from "react";
import { api, busy } from "./api";
import type { DocSummary, MergeState } from "./api";
import { ProgressBar } from "./Viewer";

export interface Folder {
  path: string; // "2024/brokerage"; "" for inbox/ itself
  name: string; // "brokerage"
  folders: Folder[];
  docs: DocSummary[];
}

export function tree(docs: DocSummary[]): Folder {
  const root: Folder = { path: "", name: "", folders: [], docs: [] };
  for (const d of docs) {
    let at = root;
    for (const part of d.folder ? d.folder.split("/") : []) {
      const path = at.path ? `${at.path}/${part}` : part;
      let next = at.folders.find((f) => f.path === path);
      if (!next) {
        next = { path, name: part, folders: [], docs: [] };
        at.folders.push(next);
      }
      at = next;
    }
    at.docs.push(d);
  }
  return root;
}

export function allDocs(f: Folder): DocSummary[] {
  return [...f.folders.flatMap(allDocs), ...f.docs];
}

interface Props {
  root: Folder;
  selected: string | null;
  active: boolean; // the documents view is showing (not Your details)
  picked: Set<string>;
  collapsed: Set<string>;
  target: string; // where Add files puts new files
  canRun: boolean;
  onSelect: (d: DocSummary) => void;
  onPick: (ids: string[], on: boolean) => void;
  onFold: (path: string) => void;
  onTarget: (path: string) => void;
  onRun: (path: string) => void;
  onDropInto: (path: string, files: FileList) => void;
}

const exportable = (d: DocSummary) => d.status === "passed";
const counts = (d: DocSummary) => Object.values(d.redactions).reduce((a, b) => a + b, 0);

export function DocTree(props: Props) {
  return <>{body(props.root, 0, props)}</>;
}

function body(f: Folder, depth: number, p: Props) {
  return (
    <>
      {f.folders.map((sub) => <FolderItem key={sub.path} f={sub} depth={depth} p={p} />)}
      {f.docs.map((d) => <DocItem key={d.id} d={d} depth={depth} p={p} />)}
    </>
  );
}

function FolderItem({ f, depth, p }: { f: Folder; depth: number; p: Props }) {
  const docs = allDocs(f);
  const passed = docs.filter(exportable);
  const chosen = passed.filter((d) => p.picked.has(d.id));
  const failed = docs.filter((d) => d.status === "failed" || d.status === "error").length;
  const working = docs.some((d) => busy(d.status));
  const open = !p.collapsed.has(f.path);
  const [merging, setMerging] = useState(false);
  const onDrop = (e: DragEvent) => {
    if (!e.dataTransfer.files.length) return;
    e.preventDefault();
    e.stopPropagation();
    p.onDropInto(f.path, e.dataTransfer.files);
  };
  return (
    <>
      <li className={`folder${p.target === f.path ? " target" : ""}`} style={{ paddingLeft: depth * 14 }}
          data-testid="folder" data-folder={f.path} onDrop={onDrop}>
        <input type="checkbox" className="pick" aria-label={`Pick everything that passed in ${f.name} for export`}
               checked={passed.length > 0 && chosen.length === passed.length} disabled={!passed.length}
               ref={(el) => { if (el) el.indeterminate = chosen.length > 0 && chosen.length < passed.length; }}
               title={passed.length ? "Pick everything in this folder that passed" : "Nothing in this folder has passed yet"}
               onChange={() => p.onPick(passed.map((d) => d.id), chosen.length !== passed.length)} />
        <button type="button" className="fold" aria-expanded={open}
                aria-label={`${f.name}: ${docs.length} document${docs.length === 1 ? "" : "s"}, ${passed.length} passed`
                  + (failed ? `, ${failed} failed` : "")}
                onClick={() => { p.onFold(f.path); p.onTarget(f.path); }}
                title={`${open ? "Fold" : "Open"} ${f.name}; Add files puts new files here`}>
          <span className="chev" aria-hidden="true">{open ? "▾" : "▸"}</span>
          <span className="name">{f.name}</span>
          <span className="muted count">{docs.length}</span>
          <span className="tally">
            {passed.length > 0 && <span className="ok" title={`${passed.length} passed`}>✓ {passed.length}</span>}
            {failed > 0 && <span className="bad" title={`${failed} failed or could not be read`}>✗ {failed}</span>}
            {working && <span className="badge running">running</span>}
          </span>
        </button>
        <button type="button" className="run-folder" onClick={() => p.onRun(f.path)} disabled={!p.canRun}
                aria-label={`Run ${f.name}`}
                title={p.canRun ? `Redact and verify everything in ${f.name}` : "Add your details first"}>Run</button>
        <button type="button" className={`run-folder${merging ? " on" : ""}`} onClick={() => setMerging((m) => !m)}
                aria-label={`Merge ${f.name}`} aria-expanded={merging}
                title={`Merge what passed in ${f.name} into one PDF, for an upload that takes only so many files`}>
          Merge
        </button>
      </li>
      {merging && <MergePanel f={f} depth={depth} passed={passed.length} left={docs.length - passed.length}
                              canRun={p.canRun} onClose={() => setMerging(false)} />}
      {open && body(f, depth + 1, p)}
    </>
  );
}

// the size limit last used: a convenience of this browser
const MAX_MB = "taxonymous-merge-max-mb";

function loadMaxMb(): string {
  try {
    return localStorage.getItem(MAX_MB) ?? "";
  } catch {
    return "";
  }
}

function MergePanel({ f, depth, passed, left, canRun, onClose }:
  { f: Folder; depth: number; passed: number; left: number; canRun: boolean; onClose: () => void }) {
  const [maxMb, setMaxMb] = useState(loadMaxMb);
  const [state, setState] = useState<MergeState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => api.mergeState(f.path).then(setState).catch((e) => setError((e as Error).message)),
    [f.path]);
  useEffect(() => {
    load();
  }, [load]);
  const working = !!state?.state;
  useEffect(() => {
    if (!working) return;
    const t = setInterval(load, 1000);
    return () => clearInterval(t);
  }, [working, load]);

  const limit = maxMb.trim() ? Number(maxMb) : null;
  const badLimit = limit !== null && !(limit >= 0.1);
  const start = async () => {
    setError(null);
    try {
      localStorage.setItem(MAX_MB, maxMb.trim());
    } catch {
      /* not kept: fine */
    }
    try {
      await api.merge(f.path, limit);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const last = state?.last;
  const issues = last?.parts.flatMap((part) => part.issues) ?? [];
  return (
    <li className="merge-panel" style={{ marginLeft: depth * 14 }} data-testid="merge-panel">
      <div>
        Merge the <strong>{passed}</strong> document{passed === 1 ? "" : "s"} in {f.name} that passed into one PDF,
        with a contents page.{left > 0 && ` ${left} not passed will be left out.`}
      </div>
      <label className="merge-limit">
        Split into files of at most
        <input type="number" min="0.1" step="any" value={maxMb} placeholder="no limit" aria-label="Size limit in MB"
               onChange={(e) => setMaxMb(e.target.value)} />
        MB
      </label>
      <div className="merge-actions">
        <button type="button" className="primary" onClick={start} disabled={!passed || !canRun || working || badLimit}>
          {working ? (state?.state === "queued" ? "Waiting…" : "Merging…") : "Merge"}
        </button>
        <button type="button" onClick={onClose}>Close</button>
      </div>
      {state?.progress && <div className="muted">{state.progress.label}…</div>}
      {error && <div className="side-error">{error}</div>}
      {state?.error && <div className="side-error">{state.error}</div>}
      {!working && last && (last.passed ? (
        <div className="side-ok" data-testid="merge-result">
          In ready-to-upload: {last.files.map((file) => file.split("/").pop()).join(", ")}
          {" "}({last.parts.reduce((n, part) => n + part.pages, 0)} pages)
          {last.too_big.length > 0 && <div className="side-error">
            Part {last.too_big.join(", ")} is over the limit: one document bigger than that on its own.</div>}
        </div>
      ) : (
        <div className="side-error" data-testid="merge-result">
          {last.parts.length ? `Not merged: the check found ${[...new Set(issues.map((i) => `${i.kind}${i.page ? ` (page ${i.page})` : ""}`))].join(", ")}.`
            : "Not merged: nothing here has passed yet."}
        </div>
      ))}
    </li>
  );
}

function DocItem({ d, depth, p }: { d: DocSummary; depth: number; p: Props }) {
  return (
    <li style={{ paddingLeft: depth * 14 }} data-folder={d.folder}>
      <input type="checkbox" className="pick" aria-label={`Pick ${d.name} for export`}
             checked={exportable(d) && p.picked.has(d.id)} disabled={!exportable(d)}
             title={exportable(d) ? "Pick for Export selected" : "Only a document that passed can be exported"}
             onChange={() => p.onPick([d.id], !p.picked.has(d.id))} />
      <button type="button" className={d.id === p.selected && p.active ? "active" : ""} onClick={() => p.onSelect(d)}>
        <span className="name">{d.name}</span>
        <span className="meta">
          <span className={`badge ${d.status}`}>{d.status}</span>
          {d.exported && <span className="badge exported">exported</span>}
          {d.version > 0 && <span className="muted">{counts(d)} redacted</span>}
        </span>
        {d.progress && <ProgressBar p={d.progress} compact />}
      </button>
    </li>
  );
}
