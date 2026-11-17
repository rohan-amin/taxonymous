import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { HOST, useHost } from "./host";
import type { HostState, PassAnswer, PassMode } from "./host";
import { Setup } from "./Setup";
import { LocalBanner, WELCOME_STEPS, Workspace } from "./Workspace";
import "./workspace.css";

// The browser app: the start screen (a first visit: guided setup, its download behind it; after
// that: open your Taxonymous file), then the workspace.
export function Browser() {
  const s = useHost()!;
  return s.stage === "open" ? <Workspace /> : <Start s={s} />;
}

const Brand = () => (
  <div className="ws-brand">
    <svg width="18" height="18" viewBox="0 0 28 28" aria-hidden="true"><rect x="3" y="2" width="22" height="24" rx="4" fill="none" stroke="currentColor" strokeWidth="2.4" /><rect x="8" y="9" width="12" height="4" rx="1" fill="currentColor" /></svg>
    Taxonymous
  </div>
);

function Start({ s }: { s: HostState }) {
  const [ask, setAsk] = useState<{ mode: PassMode; name: string; error: string; done: (a: PassAnswer) => void } | null>(null);
  const [guide, setGuide] = useState(false);
  const profileInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    HOST!.askPass = (mode, name, error) => new Promise((done) => setAsk({ mode, name, error, done }));
    return () => { HOST!.askPass = undefined; };
  }, []);
  const busy = s.stage === "starting";
  const setUp = () => { if (s.stage === "setup") HOST!.setUp(); };  // the download, quietly, while you answer

  if (guide) {
    return (
      <div className="ws">
        <header className="ws-top"><Brand /></header>
        <Setup s={s} onCancel={() => setGuide(false)} />
      </div>
    );
  }

  let card;
  const first = (s.stage === "setup" || s.stage === "downloading") && !s.update;
  if (s.stage === "checking") {
    card = <p className="ws-start-muted">Getting ready…</p>;
  } else if (s.update && s.stage === "downloading" && s.progress) {
    card = (
      <>
        <h2>Updating Taxonymous</h2>
        <p className="ws-start-muted">A new version ({s.size}). Your Taxonymous file and documents aren't touched.</p>
        <div className="ws-start-progress">
          <div className="ws-bar work" role="progressbar" aria-label="Updating" aria-valuemin={0} aria-valuemax={100}
               aria-valuenow={Math.round(s.progress.fraction * 100)}>
            <div style={{ width: `${s.progress.fraction * 100}%` }} />
          </div>
          <span className="ws-start-muted">{s.progress.text}</span>
        </div>
      </>
    );
  } else if (first && s.stage === "downloading" && s.progress && !s.update) {
    card = (
      <>
        <h2>Getting Taxonymous ready on your computer</h2>
        <p className="ws-start-muted">
          A one-time download of {s.size}. After that, Taxonymous works with no internet connection.
        </p>
        <div className="ws-start-progress">
          <div className="ws-bar work" role="progressbar" aria-label="Setting up" aria-valuemin={0} aria-valuemax={100}
               aria-valuenow={Math.round(s.progress.fraction * 100)}>
            <div style={{ width: `${s.progress.fraction * 100}%` }} />
          </div>
          <span className="ws-start-muted">{s.progress.text}</span>
        </div>
        {s.waiting ? (
          <p>{s.waiting} will open as soon as this finishes.</p>
        ) : (
          <div className="ws-start-pair">
            <button type="button" className="wbtn primary big" onClick={() => setGuide(true)}>Set up a new file</button>
            <button type="button" className="wbtn primary big" onClick={() => HOST!.openFile()}>Open my Taxonymous file</button>
          </div>
        )}
      </>
    );
  } else if (first) {
    card = (
      <>
        <h2>Get started</h2>
        <p>
          New to Taxonymous? Setting up takes about five minutes. Have your Social Security number handy, and your
          spouse's if you file together. Already set up? Open the Taxonymous file you saved.
        </p>
        <div className="ws-start-pair">
          <button type="button" className="wbtn primary big" onClick={() => { setUp(); setGuide(true); }}>
            Set up a new file
          </button>
          <button type="button" className="wbtn primary big" onClick={() => { setUp(); HOST!.openFile(); }}>
            Open my Taxonymous file
          </button>
        </div>
      </>
    );
  } else {
    card = (
      <>
        <h2>Welcome back</h2>
        <p>
          Open the Taxonymous file you saved when you set up (in your Documents folder, unless you put it somewhere
          else), or set up a new one.
        </p>
        <div className="ws-start-pair">
          <button type="button" className="wbtn primary big" disabled={busy} onClick={() => HOST!.openFile()}>
            Open my Taxonymous file
          </button>
          <button type="button" className="wbtn primary big" disabled={busy} onClick={() => setGuide(true)}>
            Set up a new file
          </button>
        </div>
        {busy && <p className="ws-start-muted">Opening…</p>}
        <p className="ws-start-muted small">
          Just looking?{" "}
          <button type="button" className="ws-link" disabled={busy} onClick={() => HOST!.newFile("sample")}>
            Try a sample household
          </button>
          . Have a profile.json from the Mac app?{" "}
          <button type="button" className="ws-link" disabled={busy} onClick={() => profileInput.current?.click()}>
            Start from it
          </button>
          .
        </p>
        <input ref={profileInput} type="file" accept=".json,application/json" hidden onChange={async (e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) HOST!.newFile({ profile: await f.text() });
        }} />
      </>
    );
  }

  return (
    <div className="ws">
      <header className="ws-top"><Brand /></header>
      <div className="ws-start">
        <div className="ws-welcome-inner">
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h1>Share your tax documents with AI, without sharing who you are.</h1>
            <p className="ws-start-sub">Remove your identity. Preserve your financial context.</p>
          </div>
          <section className="ws-start-card" aria-live="polite">
            {card}
            {s.error && <p className="ws-start-error" role="alert">{s.error}</p>}
          </section>
          <LocalBanner />
          {first && (
            <ol className="ws-steps">
              {WELCOME_STEPS.map(([t, b], i) => (
                <li key={t}><span className="n">{i + 1}</span><strong>{t}</strong><p>{b}</p></li>
              ))}
            </ol>
          )}
          {(s.stage === "ready" || s.stage === "starting") && (
            <p className="ws-start-muted small ws-start-foot">
              {s.notice && <>{s.notice} · </>}
              Set up on your computer, works offline ·{" "}
              <button type="button" className="ws-link" onClick={() => HOST!.remove()}>Remove from your computer</button>
            </p>
          )}
          <p className="ws-start-muted small ws-start-foot">
            Beta ·{" "}
            <a className="ws-link" href="https://github.com/rohan-amin/taxonymous" target="_blank" rel="noopener noreferrer">
              Source code
            </a>{" "}
            (AGPL-3.0)
          </p>
        </div>
      </div>
      {ask && <PassPrompt {...ask} onDone={(a) => { setAsk(null); ask.done(a); }} />}
    </div>
  );
}

// a passphrase: to protect a new file, or to open a protected one
function PassPrompt({ mode, name, error, onDone }: { mode: PassMode; name: string; error: string;
                                                      onDone: (a: PassAnswer) => void }) {
  const [p1, setP1] = useState("");
  const [p2, setP2] = useState("");
  const [protect, setProtect] = useState(true);
  const [problem, setProblem] = useState(error);
  const first = useRef<HTMLInputElement>(null);
  useEffect(() => { first.current?.focus(); }, []);
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onDone(null); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onDone]);
  const create = mode === "create";
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (create && !protect) return onDone({ passphrase: "" });
    if (create && p1.length < 8) return setProblem("Use at least 8 characters.");
    if (create && p1 !== p2) return setProblem("The two don't match.");
    if (!p1) return setProblem("Enter the passphrase.");
    onDone({ passphrase: p1 });
  };
  return (
    <div className="ws-scrim center" onClick={(e) => e.currentTarget === e.target && onDone(null)}>
      <form className="ws-confirm ws-pass" role="dialog" aria-label={create ? "Protect your Taxonymous file" : `Open ${name}`}
            onSubmit={submit}>
        <input type="text" autoComplete="username" value="Taxonymous file" readOnly hidden aria-hidden="true" />
        <strong>{create ? "Protect your Taxonymous file" : `Open ${name}`}</strong>
        <p>{create
          ? "It will hold your SSN and account numbers. With a passphrase, a copy that gets out can't be read. "
            + "If you forget it, the file can't be opened: you'd start a new one."
          : "Enter the passphrase this file was saved with."}</p>
        <label>Passphrase
          <input ref={first} type="password" autoComplete={create ? "new-password" : "current-password"} value={p1}
                 disabled={create && !protect} onChange={(e) => { setP1(e.target.value); setProblem(""); }} />
        </label>
        {create && (
          <label>Again
            <input type="password" autoComplete="new-password" value={p2} disabled={!protect}
                   onChange={(e) => { setP2(e.target.value); setProblem(""); }} />
          </label>
        )}
        {create && (
          <label className="inline">
            <input type="checkbox" checked={protect} onChange={(e) => { setProtect(e.target.checked); setProblem(""); }} />
            Protect it with a passphrase (recommended)
          </label>
        )}
        {problem && <p className="ws-start-error" role="alert">{problem}</p>}
        <div className="ws-confirm-foot">
          <button type="button" className="wbtn" onClick={() => onDone(null)}>Cancel</button>
          <button type="submit" className="wbtn primary">{create ? "Continue" : "Open"}</button>
        </div>
      </form>
    </div>
  );
}
