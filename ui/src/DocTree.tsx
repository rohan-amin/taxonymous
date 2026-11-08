// The sidebar's document list: inbox/'s folders as headings you can fold, each with its
// documents (and folders) under it, then the documents in inbox/ itself.
import type { DragEvent } from "react";
import { busy } from "./api";
import type { DocSummary } from "./api";
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
      </li>
      {open && body(f, depth + 1, p)}
    </>
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
