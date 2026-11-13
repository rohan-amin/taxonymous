// Click a value on the page to add it to My profile: the words you can click, and the
// small form that says what the value is (suggested by the engine, yours to change).
import { useEffect, useRef, useState } from "react";
import type { CSSProperties, FormEvent } from "react";
import { api, ApiError } from "../api";
import type { DetailKind, PageWord, Rect, Region, Suggestion } from "../api";
import { DETAIL_FORMS, Field, blankRow, quiet } from "../fields";

// the field a value goes in, for each kind (when the reviewer picks another kind)
const MAIN: Record<DetailKind, string> = {
  person: "name", address: "street", account: "number", card: "number", number: "number", ein: "ein",
  phone: "value", email: "value", preparer: "name",
};

export interface Picked {
  page: number;
  words: number[];
  suggestion: Suggestion | null; // null while the engine thinks
}

// the words of each page, read one page at a time while clicking is on (OCR on a scan)
export function usePageWords(id: string, pages: number, on: boolean) {
  const [words, setWords] = useState<Map<number, PageWord[]>>(new Map());
  const [reading, setReading] = useState<number | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => { setWords(new Map()); setFailed(null); }, [id]);
  useEffect(() => {
    if (!on) return;
    let live = true;
    (async () => {
      for (let n = 0; n < pages && live; n++) {
        if (words.has(n)) continue;
        setReading(n);
        try {
          const got = (await api.words(id, n)).words;
          if (live) setWords((m) => new Map(m).set(n, got));
        } catch (e) {
          if (live) setFailed((e as Error).message);
          break;
        }
      }
      if (live) setReading(null);
    })();
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, pages, on]);
  return { words, reading, failed };
}

const overlapsMost = (a: Rect, b: Rect) => {
  const w = Math.min(a[2], b[2]) - Math.max(a[0], b[0]);
  const h = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
  return w > 0 && h > 0 && w * h > 0.5 * (a[2] - a[0]) * (a[3] - a[1]);
};

// a page's clickable words: the ones not already removed
export function WordLayer({ words, regions, picked, size, onPick }: {
  words: PageWord[]; regions: Region[]; picked: number[]; size: [number, number];
  onPick: (i: number, extend: boolean) => void;
}) {
  const [w, h] = size;
  const box = (r: Rect): CSSProperties => ({
    left: `${(r[0] / w) * 100}%`, top: `${(r[1] / h) * 100}%`,
    width: `${((r[2] - r[0]) / w) * 100}%`, height: `${((r[3] - r[1]) / h) * 100}%`,
  });
  return (
    <>
      {words.map((word, i) => {
        if (regions.some((g) => overlapsMost(word.rect, g.rect))) return null;
        const on = picked.includes(i);
        return (
          <button type="button" key={i} className={`ws-word${on ? " on" : ""}`} style={box(word.rect)}
                  aria-label={`Add ${word.text} to My profile`} aria-pressed={on}
                  onClick={(e) => { e.stopPropagation(); onPick(i, e.shiftKey); }} />
        );
      })}
    </>
  );
}

// where the form goes: under the value, or above it near the bottom of the page
export function popoverPlace(words: PageWord[], picked: number[], [w, h]: [number, number]): CSSProperties {
  const rs = picked.map((i) => words[i].rect);
  const x0 = Math.min(...rs.map((r) => r[0])), x1 = Math.max(...rs.map((r) => r[2]));
  const y0 = Math.min(...rs.map((r) => r[1])), y1 = Math.max(...rs.map((r) => r[3]));
  const style: CSSProperties = {};
  if (x0 / w > 0.5) style.right = `${(1 - x1 / w) * 100}%`;
  else style.left = `${(x0 / w) * 100}%`;
  if (y1 / h > 0.6) style.bottom = `calc(${(1 - y0 / h) * 100}% + 6px)`;
  else style.top = `calc(${(y1 / h) * 100}% + 6px)`;
  return style;
}

export function AddForm({ picked, docId, style, onCancel, onAdded }: {
  picked: Picked; docId: string; style: CSSProperties; onCancel: () => void;
  onAdded: (queued: string[]) => void;
}) {
  const s = picked.suggestion;
  const [kind, setKind] = useState<DetailKind>(s?.kind ?? "person");
  const [item, setItem] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [more, setMore] = useState(false);
  const form = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (!s) return;
    setKind(s.kind);
    setItem({ ...blankRow(DETAIL_FORMS.find((d) => d.kind === s.kind)!.fields), ...s.item });
    setErrors({});
    setError(null);
  }, [s]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onCancel]);
  useEffect(() => { form.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [s]);

  const pick = (k: DetailKind) => {
    setKind(k);
    setErrors({});
    setMore(false);
    setItem(k === s?.kind ? { ...blankRow(DETAIL_FORMS.find((d) => d.kind === k)!.fields), ...s.item }
      : { ...blankRow(DETAIL_FORMS.find((d) => d.kind === k)!.fields), [MAIN[k]]: s?.text ?? "" });
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      const r = await api.addDetail(kind, item, docId);
      onAdded(r.queued);
    } catch (err) {
      const ae = err as ApiError;
      setErrors(ae.fields ?? {});
      setError(Object.keys(ae.fields ?? {}).length ? null : ae.message);
    } finally {
      setSaving(false);
    }
  };

  const all = DETAIL_FORMS.find((d) => d.kind === kind)?.fields ?? [];
  // what the value filled in, and choices; the rest on request
  const fields = more ? all : all.filter(({ f, options }) => options || f === MAIN[kind] || item[f] || errors[f]);
  return (
    <form ref={form} className="ws-add" style={style} onSubmit={submit} onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()} data-testid="add-to-profile" aria-label="Add to My profile">
      <div className="ws-add-head">
        <strong>Add to My profile</strong>
        <span>Removed from all documents, in any format it's printed.</span>
      </div>
      {!s ? <p className="ws-add-wait">Looking at it…</p> : <>
        {s.note && <p className="ws-add-note" role="note">{s.note}</p>}
        <div className="ws-add-fields">
          <Field label="What is it">
            <select value={kind} onChange={(e) => pick(e.target.value as DetailKind)}>
              {DETAIL_FORMS.map(({ kind: k, label }) => <option key={k} value={k}>{label}</option>)}
            </select>
          </Field>
          {fields.map(({ f, label, placeholder, options }, i) => (
            <Field key={`${kind}.${f}`} label={label} error={errors[f]}>
              {options ? (
                <select value={item[f] ?? ""} onChange={(e) => setItem({ ...item, [f]: e.target.value })}>
                  {options.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
                </select>
              ) : (
                <input value={item[f] ?? ""} placeholder={placeholder} {...quiet} autoFocus={i === 0 && !item[f]}
                       onChange={(e) => { setItem({ ...item, [f]: e.target.value }); setErrors({ ...errors, [f]: "" }); }} />
              )}
            </Field>
          ))}
        </div>
        {fields.length < all.length &&
          <button type="button" className="wbtn quiet ws-add-more" onClick={() => setMore(true)}>
            More fields ({all.length - fields.length})</button>}
        {error && <p className="ws-add-error" role="alert">{error}</p>}
        <div className="ws-add-foot">
          <button type="submit" className="wbtn primary" disabled={saving}>{saving ? "Adding…" : "Add and clean again"}</button>
          <button type="button" className="wbtn quiet" onClick={onCancel}>Cancel</button>
          <span className="ws-add-tip">Shift-click to add the next word</span>
        </div>
      </>}
    </form>
  );
}
