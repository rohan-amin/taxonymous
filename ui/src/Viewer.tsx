import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { api, ApiError, busy, pageImage, SOURCE_LABEL } from "./api";
import type { Box, DocDetail, DocSummary, Progress, Rect, Region } from "./api";

const GAP = 16; // px between pages
const PAD = 16; // px around the page column
const MIN_MARK = 3; // points: smaller drags are clicks, not marks
const ZOOMS = [0.5, 0.67, 0.8, 1, 1.25, 1.5, 2, 3];

interface Mark extends Box {
  id: number;
}

type Side = "original" | "redacted";

const same = (a: Rect, b: Rect) => a.every((v, i) => Math.abs(v - b[i]) < 0.6);

function issueText(d: DocDetail): string {
  const issues = d.report?.issues ?? d.issues;
  if (d.error) return d.error;
  if (!issues.length) return "";
  const counts = new Map<string, number>();
  for (const i of issues) {
    const key = `${i.kind}${i.page ? ` on page ${i.page}` : ""} (${i.check}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts].map(([key, n]) => `${key}${n > 1 ? ` ×${n}` : ""})`).join(", ");
}

export function Viewer({ summary, onChanged }: { summary: DocSummary; onChanged: () => void }) {
  const [detail, setDetail] = useState<DocDetail | null>(null);
  const [fit, setFit] = useState(1);
  const [zoomStep, setZoomStep] = useState(ZOOMS.indexOf(1));
  const [page, setPage] = useState(0);
  const [marking, setMarking] = useState(false);
  const [marks, setMarks] = useState<Mark[]>([]);
  const [draft, setDraft] = useState<Mark | null>(null);
  const [scrubbed, setScrubbed] = useState<Rect[] | null>(null); // the last final scrub, for the preview
  const [selected, setSelected] = useState<Region | null>(null);
  const [selectedShown, setSelectedShown] = useState<number | null>(null); // index into detail.shown
  const [showOnOriginal, setShowOnOriginal] = useState(true);
  const [message, setMessage] = useState<{ tone: "ok" | "bad" | "info"; text: string } | null>(null);
  const panes = { original: useRef<HTMLDivElement>(null), redacted: useRef<HTMLDivElement>(null) };
  const syncing = useRef<Side | null>(null);
  const nextId = useRef(1);
  const id = summary.id;

  const load = useCallback(async () => {
    const d = await api.document(id);
    setDetail(d);
    return d;
  }, [id]);

  // a new document: start clean
  useEffect(() => {
    setDetail(null);
    setMarks([]);
    setScrubbed(null);
    setSelected(null);
    setSelectedShown(null);
    setMessage(null);
    setPage(0);
    load();
  }, [id, load]);

  // the list noticed a change (a run finished elsewhere): refresh
  useEffect(() => {
    load();
  }, [summary.version, summary.status, load]);

  // while the worker has this document, poll it
  useEffect(() => {
    if (!detail || !busy(detail.status)) return;
    const t = setInterval(async () => {
      const d = await load();
      if (!busy(d.status)) onChanged();
    }, 700);
    return () => clearInterval(t);
  }, [detail, load, onChanged]);

  // "Verifying…" is over (even if it finished before the first poll): show the outcome
  useEffect(() => {
    if (detail && !busy(detail.status)) setMessage((m) => (m?.tone === "info" ? null : m));
  }, [detail]);

  // after a final scrub, report how it went
  useEffect(() => {
    if (!detail || !scrubbed || busy(detail.status)) return;
    const n = scrubbed.length;
    const areas = `${n} area${n === 1 ? "" : "s"}`;
    if (detail.status === "passed") {
      setMessage({ tone: "ok", text: `Final scrub done: ${areas} removed and verified. Ready to export.` });
    } else {
      setMessage({ tone: "bad", text: `Final scrub ran, but verification failed: ${issueText(detail)}` });
    }
  }, [detail, scrubbed]);

  const sizes = detail?.pages ?? [];
  const maxWidth = Math.max(1, ...sizes.map(([w]) => w));
  const zoom = fit * ZOOMS[zoomStep];

  // fit the widest page to the pane
  useLayoutEffect(() => {
    const el = panes.original.current;
    if (!el) return;
    const measure = () => setFit(Math.max(0.2, (el.clientWidth - 2 * PAD) / maxWidth));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [maxWidth, detail === null]);

  const tops = useMemo(() => {
    const out: number[] = [];
    let y = PAD;
    for (const [, h] of sizes) {
      out.push(y);
      y += h * zoom + GAP;
    }
    return out;
  }, [sizes, zoom]);

  const dpi = Math.min(288, Math.max(48, Math.round((72 * zoom * (window.devicePixelRatio || 1)) / 24) * 24));

  // ------------------------------------------------------------ mirrored scrolling

  const onScroll = (side: Side) => {
    const self = panes[side].current;
    const other = panes[side === "original" ? "redacted" : "original"].current;
    if (!self) return;
    if (syncing.current && syncing.current !== side) return; // this scroll is the mirror
    syncing.current = side;
    if (other) {
      other.scrollTop = self.scrollTop;
      other.scrollLeft = self.scrollLeft;
    }
    requestAnimationFrame(() => (syncing.current = null));
    const probe = self.scrollTop + self.clientHeight / 3;
    let current = 0;
    tops.forEach((t, i) => t <= probe && (current = i));
    setPage(current);
  };

  const goTo = (n: number) => {
    const target = Math.max(0, Math.min(sizes.length - 1, n));
    for (const ref of Object.values(panes)) {
      if (ref.current) ref.current.scrollTop = tops[target] - PAD;
    }
    setPage(target);
  };

  // keep the same spot in view when zooming
  const zoomTo = (step: number) => {
    const el = panes.original.current;
    const ratio = el ? el.scrollTop / Math.max(1, el.scrollHeight) : 0;
    setZoomStep(Math.max(0, Math.min(ZOOMS.length - 1, step)));
    requestAnimationFrame(() => {
      for (const ref of Object.values(panes)) {
        if (ref.current) ref.current.scrollTop = ratio * ref.current.scrollHeight;
      }
    });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowRight" || e.key === "]") goTo(page + 1);
      else if (e.key === "ArrowLeft" || e.key === "[") goTo(page - 1);
      else if (e.key === "m") setMarking((m) => !m);
      else if (e.key === "Escape") {
        setMarking(false);
        setSelected(null);
        setSelectedShown(null);
        setDraft(null);
      } else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ------------------------------------------------------------ marking

  const toPoints = (e: ReactPointerEvent, el: HTMLElement): [number, number] => {
    const r = el.getBoundingClientRect();
    return [(e.clientX - r.left) / zoom, (e.clientY - r.top) / zoom];
  };

  const startMark = (e: ReactPointerEvent<HTMLDivElement>, n: number) => {
    if (!marking || e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    const [x, y] = toPoints(e, e.currentTarget);
    setDraft({ id: nextId.current++, page: n, rect: [x, y, x, y] });
  };

  const moveMark = (e: ReactPointerEvent<HTMLDivElement>, n: number) => {
    if (!draft || draft.page !== n) return;
    const [x, y] = toPoints(e, e.currentTarget);
    setDraft({ ...draft, rect: [draft.rect[0], draft.rect[1], x, y] });
  };

  const endMark = () => {
    if (!draft) return;
    const [ax, ay, bx, by] = draft.rect;
    const [w, h] = sizes[draft.page];
    const rect: Rect = [
      Math.max(0, Math.min(ax, bx)), Math.max(0, Math.min(ay, by)),
      Math.min(w, Math.max(ax, bx)), Math.min(h, Math.max(ay, by)),
    ];
    if (rect[2] - rect[0] >= MIN_MARK && rect[3] - rect[1] >= MIN_MARK) {
      setMarks((m) => [...m, { ...draft, rect }]);
      setScrubbed(null);
      setMessage(null);
    }
    setDraft(null);
  };

  const finalScrub = async () => {
    if (!marks.length) return;
    setMessage({ tone: "info", text: "Scrubbing: removing the marked areas, then verifying the whole document…" });
    try {
      await api.addBoxes(id, marks.map(({ page, rect }) => ({ page, rect })));
      setScrubbed(marks.map((m) => m.rect));
      setMarks([]);
      setMarking(false);
      await load();
      onChanged();
    } catch (err) {
      setMessage({ tone: "bad", text: `Scrub failed: ${(err as Error).message}` });
    }
  };

  // ------------------------------------------------------------ other actions

  const act = async (what: () => Promise<unknown>, info: string) => {
    setMessage({ tone: "info", text: info });
    setSelected(null);
    setSelectedShown(null);
    setScrubbed(null);
    try {
      await what();
      await load();
      onChanged();
    } catch (err) {
      setMessage({ tone: "bad", text: (err as Error).message });
    }
  };

  const doExport = async () => {
    try {
      const r = await api.export(id);
      setScrubbed(null); // the scrub report is done; this message replaces it
      setMessage({ tone: "ok", text: `Exported to ready-to-upload/: ${r.released.join(", ")}` });
      onChanged();
      load();
    } catch (err) {
      const e = err as ApiError;
      setMessage({ tone: "bad", text: `Not exported: ${e.message}` });
    }
  };

  if (!detail) return <div className="viewer empty">Loading…</div>;

  const working = busy(detail.status);
  const processed = detail.version > 0;
  const exportBlock = working ? "Wait for the run to finish"
    : marks.length ? "Apply your marks with Final scrub first"
    : detail.status !== "passed" ? "Only a document that passed verification can be exported"
    : "";
  const sameCount = selected ? detail.regions.filter((r) => r.placeholder === selected.placeholder).length : 0;
  const newBoxes = scrubbed
    ? detail.regions.filter((r) => r.source === "manual" && scrubbed.some((s) => same(s, r.rect)))
    : [];

  const box = (rect: Rect) => ({
    left: rect[0] * zoom, top: rect[1] * zoom,
    width: (rect[2] - rect[0]) * zoom, height: (rect[3] - rect[1]) * zoom,
  });

  const pane = (side: Side) => (
    <div className={`pane ${side}${marking ? " marking" : ""}`} ref={panes[side]} onScroll={() => onScroll(side)}
         data-testid={`pane-${side}`}>
      <div className="column" style={{ padding: PAD, gap: GAP }}>
        {sizes.map(([w, h], n) => (
          <div key={n} className="page" style={{ width: w * zoom, height: h * zoom }} data-page={n}
               onPointerDown={(e) => startMark(e, n)} onPointerMove={(e) => moveMark(e, n)}
               onPointerUp={endMark}>
            {side === "redacted" && !processed ? (
              <div className="not-yet">{working ? "Redacting…" : "Not redacted yet"}</div>
            ) : (
              <img src={pageImage(id, n, side, dpi, detail.version)} alt={`${side} page ${n + 1}`}
                   loading="lazy" draggable={false} />
            )}
            {(side === "redacted" || showOnOriginal) && detail.regions.filter((r) => r.page === n).map((r, i) => (
              <button key={i} type="button"
                      className={`region ${side === "original" ? "ghost" : ""} ${selected === r ? "selected" : ""}`}
                      style={box(r.rect)} title={`${r.placeholder} · ${r.kind} · found by ${SOURCE_LABEL[r.source] ?? r.source}`}
                      onClick={(e) => {
                        if (!marking) { e.stopPropagation(); setSelected(r); setSelectedShown(null); }
                      }}
                      tabIndex={side === "redacted" ? 0 : -1} />
            ))}
            {(side === "redacted" || showOnOriginal) && detail.shown.map((s, i) => s.page === n && (
              <button key={`shown${i}`} type="button" data-testid="shown"
                      className={`region shown ${selectedShown === i ? "selected" : ""}`}
                      style={box(s.rect)} title={`${s.placeholder} · left readable here by you`}
                      onClick={(e) => {
                        if (!marking) { e.stopPropagation(); setSelectedShown(i); setSelected(null); }
                      }}
                      tabIndex={side === "redacted" ? 0 : -1} />
            ))}
            {side === "redacted" && newBoxes.filter((r) => r.page === n).map((r, i) => (
              <div key={`new${i}`} className="scrubbed" style={box(r.rect)} />
            ))}
            {[...marks, ...(draft ? [draft] : [])].filter((m) => m.page === n).map((m) => {
              const [ax, ay, bx, by] = m.rect;
              const r: Rect = [Math.min(ax, bx), Math.min(ay, by), Math.max(ax, bx), Math.max(ay, by)];
              return (
                <div key={m.id} className="mark" style={box(r)} data-testid="mark">
                  {m !== draft && (
                    <button type="button" className="unmark" title="Remove this mark"
                            onPointerDown={(e) => e.stopPropagation()}
                            onClick={() => setMarks((ms) => ms.filter((x) => x.id !== m.id))}>×</button>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );

  return (
    <div className="viewer">
      <div className="toolbar">
        <div className="title">
          <strong>{detail.name}</strong>
          <span className={`badge ${detail.status}`} data-testid="status">{detail.status}</span>
          {detail.exported && <span className="badge exported">exported</span>}
        </div>
        <div className="group">
          <button type="button" onClick={() => goTo(page - 1)} disabled={page === 0} title="Previous page ([ or ←)">‹</button>
          <span className="pageno" data-testid="page">{sizes.length ? page + 1 : 0} / {sizes.length}</span>
          <button type="button" onClick={() => goTo(page + 1)} disabled={page >= sizes.length - 1}
                  title="Next page (] or →)">›</button>
        </div>
        <div className="group">
          <button type="button" onClick={() => zoomTo(zoomStep - 1)} title="Zoom out">−</button>
          <span className="zoom">{Math.round(ZOOMS[zoomStep] * 100)}%</span>
          <button type="button" onClick={() => zoomTo(zoomStep + 1)} title="Zoom in">+</button>
          <label className="check" title="Outline what is redacted on the original too">
            <input type="checkbox" checked={showOnOriginal} onChange={(e) => setShowOnOriginal(e.target.checked)} />
            boxes on original
          </label>
        </div>
        <div className="group actions">
          {!processed || detail.status === "error" ? (
            <button type="button" onClick={() => act(() => api.run(id), "Redacting and verifying…")} disabled={working}>
              Run
            </button>
          ) : (
            <>
              <button type="button" className={marking ? "on" : ""} onClick={() => setMarking(!marking)}
                      disabled={working} title="Drag over anything that still needs to go (M)">
                {marking ? "Marking…" : "Mark areas"}
              </button>
              <button type="button" className="primary" onClick={finalScrub} disabled={!marks.length || working}
                      title="Remove every marked area, then verify the whole document again">
                Final scrub{marks.length ? ` (${marks.length})` : ""}
              </button>
              {marks.length > 0 && <button type="button" onClick={() => setMarks([])}>Clear marks</button>}
              <button type="button" onClick={() => act(() => api.verify(id), "Verifying…")} disabled={working}>
                Re-verify
              </button>
              <button type="button" onClick={() => act(() => api.run(id), "Redacting and verifying…")} disabled={working}
                      title="Redact this document again from the original, keeping your marks and shown boxes">
                Run again
              </button>
            </>
          )}
          <button type="button" className="export" onClick={doExport} disabled={!!exportBlock}
                  title={exportBlock || "Copy to ready-to-upload/"}>
            Export
          </button>
        </div>
      </div>

      {(message || working || detail.status === "failed" || detail.status === "error") && (
        <div className={`banner ${working ? "info" : message?.tone ?? "bad"}`} data-testid="banner">
          {working ? (
            <>
              {scrubbed ? "Final scrub: removing the marked areas and re-verifying. "
                        : detail.status === "queued" ? "Queued: waiting for the documents ahead of it." : ""}
              {detail.progress && <ProgressBar p={detail.progress} />}
            </>
          ) : message?.text ?? `Verification ${detail.status === "error" ? "could not run" : "failed"}: ${issueText(detail)}. Export is blocked.`}
        </div>
      )}

      {!working && detail.report?.notes?.map((note) => (
        <div key={note} className="note" data-testid="note">{note}</div>
      ))}

      {(detail.kept.length > 0 || detail.shown.length > 0) && (
        <div className="kept">
          Readable in this document:{" "}
          {detail.kept.map((ph) => (
            <span key={ph} className="chip">
              <code>{ph}</code> everywhere
              <button type="button" onClick={() => act(() => api.unkeep(id, ph), `Redacting ${ph} again…`)}
                      title="Redact it again everywhere in this document">redact again</button>
            </span>
          ))}
          {detail.shown.map((s, i) => (
            <span key={`shown${i}`} className="chip">
              <code>{s.placeholder}</code> one box, page {s.page + 1}
              <button type="button" onClick={() => act(() => api.unshow(id, i), `Redacting that ${s.placeholder} again…`)}
                      title="Redact this box again">redact again</button>
            </span>
          ))}
        </div>
      )}

      <div className="panes">
        <div className="pane-head">Original</div>
        <div className="pane-head">Redacted {processed ? "" : "(not yet)"}</div>
        {pane("original")}
        {pane("redacted")}
      </div>

      {selected && (
        <div className="popover" role="dialog">
          <div><strong>{selected.placeholder}</strong> · {selected.kind}</div>
          <div className="muted">Page {selected.page + 1}, found by {SOURCE_LABEL[selected.source] ?? selected.source}</div>
          <div className="popover-actions">
            {selected.source === "manual" ? (
              <button type="button"
                      onClick={() => act(() => api.removeRedaction(id, selected.placeholder), "Removing your box…")}>
                Remove my box
              </button>
            ) : (
              <>
                <button type="button" title="Leave only this box readable; the same value stays redacted elsewhere"
                        onClick={() => act(() => api.showOne(id, { page: selected.page, rect: selected.rect }),
                                           `Showing this ${selected.placeholder}…`)}>
                  Show this box only
                </button>
                {sameCount > 1 && (
                  <button type="button" title="Leave this value readable everywhere in this document"
                          onClick={() => act(() => api.removeRedaction(id, selected.placeholder),
                                             `Showing every ${selected.placeholder} in this document…`)}>
                    Show it everywhere in this document ({sameCount} boxes)
                  </button>
                )}
              </>
            )}
            <button type="button" onClick={() => setSelected(null)}>Close</button>
          </div>
        </div>
      )}

      {selectedShown !== null && detail.shown[selectedShown] && (
        <div className="popover" role="dialog">
          <div><strong>{detail.shown[selectedShown].placeholder}</strong> · readable here</div>
          <div className="muted">Page {detail.shown[selectedShown].page + 1}, left readable by you.
            The same value is still redacted everywhere else.</div>
          <div className="popover-actions">
            <button type="button"
                    onClick={() => act(() => api.unshow(id, selectedShown), "Redacting this box again…")}>
              Redact this box again
            </button>
            <button type="button" onClick={() => setSelectedShown(null)}>Close</button>
          </div>
        </div>
      )}
    </div>
  );
}

export function ProgressBar({ p, compact }: { p: Progress; compact?: boolean }) {
  const pages = p.total ? `page ${p.done} of ${p.total}` : "";
  const of = p.jobs > 1 ? `Document ${p.job} of ${p.jobs} · ` : "";
  return (
    <div className={`progress${compact ? " compact" : ""}`} data-testid="progress">
      {!compact && <span className="progress-text">{of}{p.label}{pages && `: ${pages}`}</span>}
      <span className="progress-track" role="progressbar" aria-label={`${of}${p.label}`}
            aria-valuemin={0} aria-valuemax={p.total} aria-valuenow={p.done}>
        <span className="progress-fill" style={{ width: `${p.total ? (100 * p.done) / p.total : 0}%` }} />
      </span>
    </div>
  );
}
