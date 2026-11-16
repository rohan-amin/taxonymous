import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { HOST, useHost } from "./host";
import type { HostState, PassAnswer, PassMode } from "./host";
import { LocalBanner, WELCOME_STEPS, Workspace } from "./Workspace";
import "./workspace.css";

// The browser app: the start screen (set up on this computer, then open or start your
// Taxonymous file), then the workspace.
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
  const profileInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    HOST!.askPass = (mode, name, error) => new Promise((done) => setAsk({ mode, name, error, done }));
    return () => { HOST!.askPass = undefined; };
  }, []);
  const busy = s.stage === "starting";

  let card;
  if (s.stage === "checking") {
    card = <p className="ws-start-muted">Checking what's on this computer…</p>;
  } else if (s.stage === "setup" || s.stage === "downloading") {
    card = (
      <>
        <h2>Set up Taxonymous on this computer</h2>
        <p className="ws-start-muted">
          A one-time download of {s.size}, checked file by file. After that Taxonymous opens with no internet
          connection, and nothing you add to it ever leaves this computer.
        </p>
        {s.stage === "downloading" && s.progress ? (
          <div className="ws-start-progress">
            <div className="ws-bar work" role="progressbar" aria-label="Setting up" aria-valuemin={0} aria-valuemax={100}
                 aria-valuenow={Math.round(s.progress.fraction * 100)}>
              <div style={{ width: `${s.progress.fraction * 100}%` }} />
            </div>
            <span className="ws-start-muted">{s.progress.text}</span>
          </div>
        ) : (
          <div className="ws-start-row">
            <button type="button" className="wbtn primary big" onClick={() => HOST!.setUp()}>Download and set up</button>
          </div>
        )}
      </>
    );
  } else {
    card = (
      <>
        <h2>Your Taxonymous file</h2>
        <p className="ws-start-muted">
          Your details and the labels that stand for them live in one file you keep where you like, protected with a
          passphrase. Taxonymous reads it when you open it and writes it only when you save. Nothing is kept in
          this browser: close the page and your documents go with it.
        </p>
        <div className="ws-start-row">
          <button type="button" className="wbtn primary big" disabled={busy} onClick={() => HOST!.openFile()}>
            Open your file…
          </button>
          <button type="button" className="wbtn big" disabled={busy} onClick={() => HOST!.newFile("empty")}>
            Start a new file
          </button>
          {busy && <span className="ws-start-muted">Opening…</span>}
        </div>
        <p className="ws-start-muted small">
          Have a profile.json from the Mac app?{" "}
          <button type="button" className="ws-link" disabled={busy} onClick={() => profileInput.current?.click()}>
            Start from it
          </button>
          . Just looking?{" "}
          <button type="button" className="ws-link" disabled={busy} onClick={() => HOST!.newFile("sample")}>
            Try a sample household
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
            <p style={{ fontSize: 15, color: "var(--w-muted)" }}>Remove your identity. Preserve your financial context.</p>
          </div>
          <section className="ws-start-card" aria-live="polite">
            {card}
            {s.error && <p className="ws-start-error" role="alert">{s.error}</p>}
          </section>
          <LocalBanner />
          <ol className="ws-steps">
            {WELCOME_STEPS.map(([t, b], i) => (
              <li key={t}><span className="n">{i + 1}</span><strong>{t}</strong><p>{b}</p></li>
            ))}
          </ol>
          {(s.stage === "ready" || s.stage === "starting") && (
            <p className="ws-start-muted small ws-start-foot">
              {s.update ? (
                <>A new version is ready ({s.size}).{" "}
                  <button type="button" className="ws-link" onClick={() => HOST!.setUp()}>Update</button> · </>
              ) : <>Set up on this computer, works offline · </>}
              <button type="button" className="ws-link" onClick={() => HOST!.remove()}>Remove from this computer</button>
            </p>
          )}
          {s.stage === "downloading" && s.update && s.progress && (
            <p className="ws-start-muted small ws-start-foot">Updating: {s.progress.text}</p>
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
