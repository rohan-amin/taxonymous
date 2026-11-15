import { useEffect, useRef, useState } from "react";
import { HOST, useHost } from "./host";

// Your Taxonymous file, in the browser app's top bar: its name (a menu: Save as…, Close) and
// whether it's saved. In Chrome and Edge it saves itself once it has a place on disk: "Saved".
// Otherwise (a new file, Safari, Firefox) Save, also ⌘S / Ctrl+S, with when it was last saved.

const ago = (t: number, now: number) => {
  const min = Math.floor((now - t) / 60_000);
  return min < 1 ? "just now" : min < 60 ? `${min} min ago` : min < 60 * 24 ? `${Math.floor(min / 60)} h ago`
    : new Date(t).toLocaleDateString();
};
export function FileControls({ say }: { say: (text: string) => void }) {
  const s = useHost();
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [closing, setClosing] = useState(false);
  const keepRef = useRef<HTMLButtonElement>(null);
  const file = s?.file;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  const save = async (as = false) => {
    setMenu(null);
    try {
      const wasAuto = HOST!.state.file?.auto;
      const r = await HOST!.save(as);
      if (r?.downloaded) say(`Downloaded ${r.name}. Keep it in place of your old copy.`);
      else if (r && (as || !wasAuto)) say(`Saved ${r.name}. From now on, changes are saved as you go.`);
    } catch (e) {
      if ((e as Error).name !== "AbortError") say(`Your file wasn't saved: ${(e as Error).message}`);
    }
  };
  const saveRef = useRef(save);
  saveRef.current = save;
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setMenu(null); setClosing(false); return; }
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "s") {
        e.preventDefault();
        saveRef.current(e.shiftKey);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  useEffect(() => { if (closing) keepRef.current?.focus(); }, [closing]);

  if (!file) return null;
  const close = async () => {
    setMenu(null);
    if (file.dirty && file.auto) await save();  // about to save itself anyway: save, then close
    if (HOST!.state.file?.dirty) setClosing(true);
    else HOST!.close();
  };
  return (
    <>
      <span className="ws-keyfile">
        <button type="button" className="wbtn quiet ws-keyfile-name" aria-haspopup="menu" title="Your Taxonymous file"
                onClick={(e) => {
                  const r = e.currentTarget.getBoundingClientRect();
                  setMenu({ x: r.left, y: r.bottom + 4 });
                }}>
          {file.name}{file.protected ? "" : " (no passphrase)"} <span aria-hidden="true">▾</span>
        </button>
        {(() => {
          const when = file.savedAt ? new Date(file.savedAt).toLocaleString() : "";
          if (file.saving || (file.auto && file.dirty)) return <span className="ws-keyfile-dirty">Saving…</span>;
          if (file.auto) {
            return <span className="ws-keyfile-dirty" title={`Saved at ${when}. Changes are saved as you go.`}>Saved</span>;
          }
          const status = file.error ? "Couldn't save"
            : !file.savedAt ? "Not saved yet"
            : file.dirty ? `Last saved ${ago(file.savedAt, now)}` : "Saved";
          return (
            <>
              <span className="ws-keyfile-dirty" title={file.error || (when && `Saved at ${when}`)}>{status}</span>
              {(file.dirty || file.error) && (
                <button type="button" className="wbtn primary" onClick={() => save()}
                        title={file.inPlace ? "Save your Taxonymous file (⌘S)" : "Downloads your Taxonymous file (⌘S)"}>
                  {HOST!.folder && !file.inPlace ? "Save…" : "Save"}
                </button>
              )}
            </>
          );
        })()}
      </span>
      {menu && (
        <div className="ws-menu-scrim" onClick={() => setMenu(null)} onContextMenu={(e) => { e.preventDefault(); setMenu(null); }}>
          <div className="ws-menu" role="menu" style={{ left: menu.x, top: menu.y }} onClick={(e) => e.stopPropagation()}>
            <button type="button" role="menuitem" onClick={() => save(true)}>Save as…</button>
            <button type="button" role="menuitem" onClick={close}>Close file</button>
          </div>
        </div>
      )}
      {closing && (
        <div className="ws-scrim center" onClick={(e) => e.currentTarget === e.target && setClosing(false)}>
          <div className="ws-confirm" role="alertdialog" aria-label="Close without saving">
            <strong>Close {file.name} without saving?</strong>
            <p>Changes since you last saved it will be lost: details you added, boxes you drew. The documents
              close with it; your original files aren't touched.</p>
            <div className="ws-confirm-foot">
              <button type="button" className="wbtn" onClick={() => HOST!.close()}>Close without saving</button>
              <button ref={keepRef} type="button" className="wbtn primary"
                      onClick={async () => { setClosing(false); await save(); if (!HOST!.state.file?.dirty) HOST!.close(); }}>
                Save and close
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
