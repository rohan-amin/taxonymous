import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent, InputHTMLAttributes, KeyboardEvent, ReactNode } from "react";
import { ACCOUNT_TYPES } from "../fields";
import { HOST } from "./host";
import type { HostState } from "./host";

// Guided setup, for a new Taxonymous file: who the return is for, then each person, the home
// address, accounts (optional), and a passphrase, one question per screen, in large type.
// Setup's download carries on behind it. Nothing is kept anywhere until the last screen saves
// your Taxonymous file; the browser isn't asked to remember any of it (autocomplete off).

interface Who { id: string; name: string; ssn: string; dob: string; deceased: boolean; dod: string }
interface Acct { id: string; institution: string; number: string; type: string; owner: string }
type Errors = Record<string, string>;
type Step = "who" | "you" | "spouse" | "dependents" | "home" | "accounts" | "save";

const newId = () => "e" + [...crypto.getRandomValues(new Uint8Array(4))].map((b) => b.toString(16).padStart(2, "0")).join("");
const person = (): Who => ({ id: newId(), name: "", ssn: "", dob: "", deceased: false, dod: "" });
const account = (): Acct => ({ id: newId(), institution: "", number: "", type: "", owner: "" });
const digits = (s: string) => s.replace(/\D/g, "");
// 03-14-1952, 3.14.1952 as the app takes it; a two-digit year is the last 100 years (a birth date is past)
function date(s: string): string {
  const t = s.trim().replace(/\s*[-.\s]\s*/g, "/");
  const m = /^(\d{1,2})\/(\d{1,2})\/(\d{2})$/.exec(t);
  if (!m) return t;
  const y = Number(m[3]);
  return `${m[1]}/${m[2]}/${(y > new Date().getFullYear() % 100 ? 1900 : 2000) + y}`;
}

// As you type: an SSN gets its dashes (###-##-####), a date its slashes (03/14/1952). A separator
// appears when the next digit comes, so backspace never stops at one; slashes you type are kept
// (3/14/52), and a month from 2 to 9 or a day from 4 to 9 can't take a second digit (3141952).
const SEP = /[-/() ]/;
function ssnFormat(s: string): string {
  const d = s.replace(/\D/g, "").slice(0, 9);
  return d.length <= 3 ? d : d.length <= 5 ? `${d.slice(0, 3)}-${d.slice(3)}` : `${d.slice(0, 3)}-${d.slice(3, 5)}-${d.slice(5)}`;
}
// (555) 555-0188, or 1 (555) 555-0188: an area code never starts with 1, so a leading 1 is the country's
function phoneFormat(s: string): string {
  let d = s.replace(/\D/g, "");
  const one = d.startsWith("1") ? "1 " : "";
  d = d.slice(one ? 1 : 0, (one ? 1 : 0) + 10);
  const body = d.length <= 3 ? d : d.length <= 6 ? `(${d.slice(0, 3)}) ${d.slice(3)}` : `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`;
  return (one + body).trimEnd();
}
function dateFormat(s: string): string {
  const parts = [""];
  for (const ch of s.replace(/[-.\s]/g, "/")) {
    const i = parts.length - 1;
    if (ch === "/") { if (parts[i] && i < 2) parts.push(""); continue; }
    if (!/\d/.test(ch)) continue;
    if (i < 2 && parts[i].length === 2) parts.push(ch);
    else if (i === 2 && parts[i].length === 4) continue;
    else parts[i] += ch;
    const j = parts.length - 1;
    if (j < 2 && parts[j].length === 1 && Number(ch) > (j === 0 ? 1 : 3)) parts.push("");
  }
  return parts.join("/");
}
const count = (s: string) => s.replace(/\D/g, "").length;
function caretAfter(s: string, n: number): number {  // just past the nth digit, and any separator after it
  let i = 0;
  for (let seen = 0; n > 0 && i < s.length; i++) if (/\d/.test(s[i]) && ++seen === n) { i++; break; }
  while (n > 0 && i < s.length && SEP.test(s[i])) i++;
  return i;
}
// a whole date said back in words, "March 14, 1952", so a month/day mix-up shows
function spoken(s: string): string {
  if (!/\/\d{4}$/.test(s.trim()) || !realDate(s)) return "";
  const [mo, d, y] = date(s).split("/").map(Number);
  return new Date(y, mo - 1, d).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
}
const filled = (o: object, but: string[] = ["id", "type", "owner"]) =>
  Object.entries(o).some(([k, v]) => !but.includes(k) && typeof v === "string" && v.trim());

export const STATES: [string, string][] = [
  ["AL", "Alabama"], ["AK", "Alaska"], ["AZ", "Arizona"], ["AR", "Arkansas"], ["CA", "California"], ["CO", "Colorado"],
  ["CT", "Connecticut"], ["DE", "Delaware"], ["DC", "District of Columbia"], ["FL", "Florida"], ["GA", "Georgia"],
  ["HI", "Hawaii"], ["ID", "Idaho"], ["IL", "Illinois"], ["IN", "Indiana"], ["IA", "Iowa"], ["KS", "Kansas"],
  ["KY", "Kentucky"], ["LA", "Louisiana"], ["ME", "Maine"], ["MD", "Maryland"], ["MA", "Massachusetts"],
  ["MI", "Michigan"], ["MN", "Minnesota"], ["MS", "Mississippi"], ["MO", "Missouri"], ["MT", "Montana"],
  ["NE", "Nebraska"], ["NV", "Nevada"], ["NH", "New Hampshire"], ["NJ", "New Jersey"], ["NM", "New Mexico"],
  ["NY", "New York"], ["NC", "North Carolina"], ["ND", "North Dakota"], ["OH", "Ohio"], ["OK", "Oklahoma"],
  ["OR", "Oregon"], ["PA", "Pennsylvania"], ["RI", "Rhode Island"], ["SC", "South Carolina"], ["SD", "South Dakota"],
  ["TN", "Tennessee"], ["TX", "Texas"], ["UT", "Utah"], ["VT", "Vermont"], ["VA", "Virginia"], ["WA", "Washington"],
  ["WV", "West Virginia"], ["WI", "Wisconsin"], ["WY", "Wyoming"], ["PR", "Puerto Rico"], ["GU", "Guam"],
  ["VI", "U.S. Virgin Islands"], ["AS", "American Samoa"], ["MP", "Northern Mariana Islands"],
  ["AA", "Armed Forces Americas"], ["AE", "Armed Forces Europe"], ["AP", "Armed Forces Pacific"],
];

function realDate(s: string): boolean {
  const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(date(s));
  if (!m) return false;
  const [mo, d, y] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const t = new Date(y, mo - 1, d);
  return t.getFullYear() === y && t.getMonth() === mo - 1 && t.getDate() === d && t <= new Date() && y >= 1900;
}

// the same rules the app checks a profile with (taxonymous/profile.py clean), said plainly
function checkPerson(p: Who, at = ""): Errors {
  const e: Errors = {};
  if (p.name.trim().split(/\s+/).filter(Boolean).length < 2) e[`${at}name`] = "Enter a first and last name.";
  if (p.ssn.trim() && (digits(p.ssn).length !== 9 || /[a-z]/i.test(p.ssn))) {
    e[`${at}ssn`] = "A Social Security number has 9 digits.";
  }
  if (p.dob.trim() && !realDate(p.dob)) e[`${at}dob`] = "Write it as month/day/year, like 03/14/1952.";
  if (p.deceased) {
    if (!p.dod.trim()) e[`${at}dod`] = "Enter the date of death.";
    else if (!realDate(p.dod)) e[`${at}dod`] = "Write it as month/day/year, like 06/02/2025.";
    else if (realDate(p.dob)) {
      const t = (x: string) => { const [m, d, y] = date(x).split("/").map(Number); return new Date(y, m - 1, d).getTime(); };
      if (t(p.dod) < t(p.dob)) e[`${at}dod`] = "The date of death is before the date of birth.";
    }
  }
  return e;
}

function Field({ label, hint, error, value, onChange, format, below, ...rest }: {
  label: string; hint?: ReactNode; error?: string; value: string; onChange: (v: string) => void;
  format?: (s: string) => string; below?: ReactNode;
} & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  const id = useId();
  // formatted as typed, with the caret kept after the same digit
  const put = (el: HTMLInputElement, raw: string, n: number) => {
    const v = format!(raw), c = caretAfter(v, n);
    el.value = v;  // set here first, so React finds it already right and leaves the caret alone
    el.setSelectionRange(c, c);
    onChange(v);
  };
  const keys = (e: KeyboardEvent<HTMLInputElement>) => {  // a separator goes with the digit next to it
    const el = e.currentTarget, v = el.value, p = el.selectionStart ?? 0;
    if (p !== el.selectionEnd) return;
    let q = -1;
    if (e.key === "Backspace" && p > 0 && SEP.test(v[p - 1])) for (q = p - 2; q >= 0 && !/\d/.test(v[q]); q--);
    else if (e.key === "Delete" && p < v.length && SEP.test(v[p])) for (q = p + 1; q < v.length && !/\d/.test(v[q]); q++);
    else return;
    e.preventDefault();
    if (q >= 0 && q < v.length) put(el, v.slice(0, q) + v.slice(q + 1), count(v.slice(0, Math.min(p, q))));
  };
  return (
    <div className="ws-field">
      <label htmlFor={id}>{label}</label>
      {hint && <span className="hint" id={`${id}-hint`}>{hint}</span>}
      <input id={id} value={value} autoComplete="off" spellCheck={false}
             onChange={(e) => format ? put(e.target, e.target.value, count(e.target.value.slice(0, e.target.selectionStart ?? undefined)))
                                     : onChange(e.target.value)}
             onKeyDown={format ? keys : undefined}
             aria-invalid={!!error} aria-describedby={[hint && `${id}-hint`, below !== undefined && `${id}-below`, error && `${id}-err`].filter(Boolean).join(" ") || undefined}
             {...rest} />
      {below !== undefined && <span className="readback" id={`${id}-below`} aria-live="polite">{below}</span>}
      {error && <span className="error" id={`${id}-err`} role="alert">{error}</span>}
    </div>
  );
}

function Choice({ label, options, value, onChange, error }: {
  label: string; options: [string, string, string?][]; value: string; onChange: (v: string) => void; error?: string;
}) {
  return (
    <fieldset className="ws-choices">
      <legend className="ws-sr">{label}</legend>
      {options.map(([v, title, note]) => (
        <label key={v} className={value === v ? "on" : ""}>
          <input type="radio" name={label} value={v} checked={value === v} onChange={() => onChange(v)} />
          <span><strong>{title}</strong>{note && <small>{note}</small>}</span>
        </label>
      ))}
      {error && <span className="error" role="alert">{error}</span>}
    </fieldset>
  );
}

function PersonFields({ p, set, errors, at = "", you, died }: {
  p: Who; set: (p: Who) => void; errors: Errors; at?: string; you?: boolean;
  died?: string;  // "My spouse has died": asks for the date of death
}) {
  const whole = (k: "dob" | "dod") => () => { if (realDate(p[k]) && date(p[k]) !== p[k]) set({ ...p, [k]: date(p[k]) }); };
  return (
    <>
      <Field label={you ? "Your full name" : "Full name"} value={p.name} onChange={(name) => set({ ...p, name })}
             error={errors[`${at}name`]} autoFocus={!at || at.endsWith("0.")}
             hint="First and last name, the way your tax documents print it. Add a middle name or initial if they show one." />
      <Field label="Social Security number" value={p.ssn} onChange={(ssn) => set({ ...p, ssn })} error={errors[`${at}ssn`]}
             inputMode="numeric" className="short" format={ssnFormat}
             hint="9 digits; the dashes fill in. An ITIN works too. Used only to find and remove it from your documents." />
      <Field label="Date of birth (optional)" value={p.dob} onChange={(dob) => set({ ...p, dob })} error={errors[`${at}dob`]}
             inputMode="numeric" className="short" format={dateFormat} below={spoken(p.dob)} onBlur={whole("dob")}
             hint="Month/day/year, like 03/14/1952. A few forms print it." />
      {died && (
        <label className="ws-setup-check">
          <input type="checkbox" checked={p.deceased} onChange={(e) => set({ ...p, deceased: e.target.checked })} />
          {died}
        </label>
      )}
      {died && p.deceased && (
        <Field label="Date of death" value={p.dod} onChange={(dod) => set({ ...p, dod })} error={errors[`${at}dod`]}
               inputMode="numeric" className="short" format={dateFormat} below={spoken(p.dod)} onBlur={whole("dod")}
               hint="Month/day/year. It stays readable in your documents: it matters for your taxes." />
      )}
    </>
  );
}

// where the one-time download is, while you answer: quiet, unless it stopped
function Behind({ s }: { s: HostState }) {
  if (s.stage === "setup" && s.error) {
    return (
      <p className="ws-setup-behind problem" role="alert">
        The download stopped. Check your internet connection, then{" "}
        <button type="button" className="ws-link" onClick={() => HOST!.setUp()}>try again</button>.
      </p>
    );
  }
  if (s.stage === "downloading" && s.progress && !s.update) {
    return <p className="ws-setup-behind">Getting Taxonymous ready on your computer: {Math.round(s.progress.fraction * 100)}%</p>;
  }
  return null;
}

export function Setup({ s, onCancel }: { s: HostState; onCancel: () => void }) {
  const [step, setStep] = useState<Step>("who");
  const [who, setWho] = useState("");
  const [you, setYou] = useState(person);
  const [spouse, setSpouse] = useState(person);
  const [hasDeps, setHasDeps] = useState("");
  const [deps, setDeps] = useState<Who[]>(() => [person()]);
  const [home, setHome] = useState({ id: newId(), street: "", city: "", state: "", zip: "" });
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [accts, setAccts] = useState<Acct[]>(() => [account()]);
  const [pass, setPass] = useState({ p1: "", p2: "", show: false });
  const [errors, setErrors] = useState<Errors>({});
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState("");
  const carry = useRef<Errors | null>(null);  // what the app's check found, for the screen it's on
  const pane = useRef<HTMLDivElement>(null);

  const couple = who === "couple";
  const steps: Step[] = ["who", "you", ...(couple ? ["spouse" as Step] : []), "dependents", "home", "accounts", "save"];
  const at = steps.indexOf(step);
  useEffect(() => {  // a new screen: from its top, with only what the app's check found on it
    setErrors(carry.current ?? {});
    carry.current = null;
    setProblem("");
    pane.current?.scrollTo(0, 0);
  }, [step]);

  const check = (): Errors => {
    switch (step) {
      case "who": return who ? {} : { who: "Choose one." };
      case "you": return { ...checkPerson(you), ...(you.ssn.trim() ? {} : { ssn: "Enter your Social Security number." }) };
      case "spouse": return { ...checkPerson(spouse), ...(spouse.ssn.trim() ? {} : { ssn: "Enter your spouse's Social Security number." }) };
      case "dependents": {
        if (!hasDeps) return { has: "Choose one." };
        if (hasDeps === "no") return {};
        const kept = deps.filter((d) => filled(d));
        if (!kept.length) return { "deps.0.name": "Enter their first and last name." };
        return Object.assign({}, ...deps.map((d, i) => (filled(d) ? checkPerson(d, `deps.${i}.`) : {})));
      }
      case "home": {
        const e: Errors = {};
        if (!home.street.trim()) e.street = "Enter your street address.";
        if (!home.city.trim()) e.city = "Enter your city or town.";
        if (!home.state) e.state = "Choose your state.";
        if (!/^\d{5}(-?\d{4})?$/.test(home.zip.trim())) e.zip = "A ZIP code has 5 digits.";
        if (phone.trim() && digits(phone).replace(/^1(?=\d{10}$)/, "").length !== 10) e.phone = "Enter a 10-digit phone number.";
        if (email.trim() && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) e.email = "That doesn't look like an email address.";
        return e;
      }
      case "accounts": {
        const e: Errors = {};
        accts.forEach((a, i) => {
          if (!filled(a)) return;
          if (!a.number.trim()) e[`accts.${i}.number`] = "Enter the account number, or remove this account.";
          else if (a.number.replace(/[^A-Za-z0-9]/g, "").length < 4) e[`accts.${i}.number`] = "Enter at least 4 letters or digits.";
        });
        return e;
      }
      case "save": {
        if (pass.p1.length < 8) return { p1: "Use at least 8 characters." };
        if (!pass.show && pass.p1 !== pass.p2) return { p2: "The two don't match. Type it again." };
        return {};
      }
    }
  };

  const profile = () => {
    const people = [
      { ...you, role: "taxpayer" },
      ...(couple ? [{ ...spouse, role: "spouse" }] : []),
      ...(hasDeps === "yes" ? deps.filter((d) => filled(d)).map((d) => ({ ...d, role: "dependent" })) : []),
    ].map((p) => ({ id: p.id, name: p.name, ssn: p.ssn, dob: date(p.dob), role: p.role, deceased: p.deceased,
                    dod: p.deceased ? date(p.dod) : "",
                    occupation: "", employer: "" }));
    return {
      keep_state: true,
      people,
      addresses: [{ ...home, zip: home.zip.trim(), school_district: "", type: "home" }],
      phones: phone.trim() ? [phone] : [],
      emails: email.trim() ? [email.trim()] : [],
      accounts: accts.filter((a) => filled(a)).map((a) => ({
        id: a.id, institution: a.institution, number: a.number, routing: "", name: "", type: a.type,
        owner: a.owner || you.id,
      })),
      eins: [], cards: [], numbers: [], preparers: [],
    };
  };

  // the app's own check found something this screen's didn't: back to where it is
  const fromApp = (found: Errors) => {
    const nPeople = 1 + (couple ? 1 : 0);
    const kept = deps.map((d, i) => [d, i] as const).filter(([d]) => filled(d)).map(([, i]) => i);
    const keptAccts = accts.map((a, i) => [a, i] as const).filter(([a]) => filled(a)).map(([, i]) => i);
    const mapped: [Step, string, string][] = Object.entries(found).map(([k, v]) => {
      const [list, n, field] = k.split(".");
      const i = Number(n);
      if (list === "people") {
        if (i === 0) return ["you", field, v];
        if (couple && i === 1) return ["spouse", field, v];
        return ["dependents", `deps.${kept[i - nPeople]}.${field}`, v];
      }
      if (list === "accounts") return ["accounts", `accts.${keptAccts[i]}.${field}`, v];
      if (list === "phones") return ["home", "phone", v];
      if (list === "emails") return ["home", "email", v];
      return ["home", field ?? list, v];
    });
    const first = steps.find((st) => mapped.some(([m]) => m === st)) ?? "home";
    carry.current = Object.fromEntries(mapped.filter(([m]) => m === first).map(([, f, v]) => [f, v]));
    setStep(first);
  };

  const next = async (e: FormEvent) => {
    e.preventDefault();
    const found = check();
    setErrors(found);
    if (Object.keys(found).length) {
      (document.querySelector(".ws-setup [aria-invalid=true], .ws-setup .ws-choices input") as HTMLElement | null)?.focus();
      return;
    }
    if (step !== "save") return setStep(steps[at + 1]);
    setSaving(true);
    setProblem("");
    try {
      const r = await HOST!.create(profile(), pass.p1);
      if (r && "errors" in r) fromApp(r.errors);
    } catch (err) {
      setProblem(`Your file wasn't saved: ${(err as Error).message}`);
    } finally {
      setSaving(false);
    }
  };
  const back = () => (at === 0 ? onCancel() : setStep(steps[at - 1]));

  let title: string;
  let lede: ReactNode = null;
  let body: ReactNode;
  let go = "Continue";
  switch (step) {
    case "who":
      title = "Whose taxes are these?";
      lede = "Taxonymous removes the details of everyone you tell it about from your documents.";
      body = <Choice label="Whose taxes" value={who} onChange={setWho} error={errors.who} options={[
        ["me", "Just me"],
        ["couple", "Me and my spouse", "You file together, or your spouse's name is on your documents."],
      ]} />;
      break;
    case "you":
      title = "About you";
      lede = "Taxonymous removes these from every document you add.";
      body = <PersonFields p={you} set={setYou} errors={errors} you />;
      break;
    case "spouse":
      title = "About your spouse";
      body = <PersonFields p={spouse} set={setSpouse} errors={errors} died="My spouse has died" />;
      break;
    case "dependents":
      title = "Do you have any dependents?";
      lede = "Children, or anyone else you claim on your return, like a parent you support. Their names and Social Security numbers are removed too.";
      body = (
        <>
          <Choice label="Dependents" value={hasDeps} onChange={setHasDeps} error={errors.has}
                  options={[["no", "No"], ["yes", "Yes"]]} />
          {hasDeps === "yes" && deps.map((d, i) => (
            <fieldset className="ws-setup-group" key={d.id}>
              <legend>Dependent {i + 1}</legend>
              <PersonFields p={d} set={(p) => setDeps(deps.map((x, j) => (j === i ? p : x)))} errors={errors} at={`deps.${i}.`}
                            died="This dependent has died" />
              {deps.length > 1 && (
                <button type="button" className="ws-link" onClick={() => setDeps(deps.filter((_, j) => j !== i))}>
                  Remove dependent {i + 1}
                </button>
              )}
            </fieldset>
          ))}
          {hasDeps === "yes" && (
            <button type="button" className="wbtn big" onClick={() => setDeps([...deps, person()])}>Add another dependent</button>
          )}
        </>
      );
      break;
    case "home": {
      const id = "ws-setup-state";
      title = "Your home address";
      lede = "Your street, city and ZIP code are removed. Your state stays, so the AI knows which state's rules apply.";
      body = (
        <>
          <Field label="Street address" value={home.street} onChange={(street) => setHome({ ...home, street })}
                 error={errors.street} autoFocus hint="With the apartment or unit number, if you have one." />
          <Field label="City or town" value={home.city} onChange={(city) => setHome({ ...home, city })} error={errors.city} />
          <div className="ws-setup-row">
            <div className="ws-field">
              <label htmlFor={id}>State</label>
              <select id={id} value={home.state} onChange={(e) => setHome({ ...home, state: e.target.value })}
                      aria-invalid={!!errors.state}>
                <option value="">Choose…</option>
                {STATES.map(([c, n]) => <option key={c} value={c}>{n}</option>)}
              </select>
              {errors.state && <span className="error" role="alert">{errors.state}</span>}
            </div>
            <Field label="ZIP code" value={home.zip} onChange={(zip) => setHome({ ...home, zip })} error={errors.zip}
                   inputMode="numeric" className="short" />
          </div>
          <Field label="Phone number (optional)" value={phone} onChange={setPhone} error={errors.phone} inputMode="tel" format={phoneFormat}
                 className="short" hint="Some statements print it." />
          <Field label="Email address (optional)" value={email} onChange={setEmail} error={errors.email} inputMode="email" />
        </>
      );
      break;
    }
    case "accounts": {
      title = "Your bank and investment accounts";
      lede = <>Optional. Taxonymous removes only the account numbers you list, and their last 4 digits too. You can
        also add them later, by clicking them on a document.</>;
      const any = accts.some((a) => filled(a));
      go = any ? "Continue" : "Skip for now";
      const owners: [string, string][] = [[you.id, "Yours"], [spouse.id, "Your spouse's"], ["joint", "Joint"]];
      body = (
        <>
          {accts.map((a, i) => {
            const put = (patch: Partial<Acct>) => setAccts(accts.map((x, j) => (j === i ? { ...x, ...patch } : x)));
            const typeId = `ws-acct-type-${a.id}`;
            const ownerId = `ws-acct-owner-${a.id}`;
            return (
              <fieldset className="ws-setup-group" key={a.id}>
                <legend>Account {i + 1}</legend>
                <Field label="Bank or company" value={a.institution} onChange={(institution) => put({ institution })}
                       hint="Like Fidelity or Wells Fargo. It stays readable on your documents." />
                <Field label="Account number" value={a.number} onChange={(number) => put({ number })}
                       error={errors[`accts.${i}.number`]} hint="The full number, from a statement." />
                <div className="ws-setup-row">
                  <div className="ws-field">
                    <label htmlFor={typeId}>Kind of account (optional)</label>
                    <select id={typeId} value={a.type} onChange={(e) => put({ type: e.target.value })}>
                      {ACCOUNT_TYPES.map(([v, l]) => <option key={v} value={v}>{v ? l : "Not sure"}</option>)}
                    </select>
                  </div>
                  {couple && (
                    <div className="ws-field">
                      <label htmlFor={ownerId}>Whose</label>
                      <select id={ownerId} value={a.owner || you.id} onChange={(e) => put({ owner: e.target.value })}>
                        {owners.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      </select>
                    </div>
                  )}
                </div>
                {(accts.length > 1 || filled(a)) && (
                  <button type="button" className="ws-link"
                          onClick={() => setAccts(accts.length > 1 ? accts.filter((_, j) => j !== i) : [account()])}>
                    Remove account {i + 1}
                  </button>
                )}
              </fieldset>
            );
          })}
          <button type="button" className="wbtn big" onClick={() => setAccts([...accts, account()])}>Add another account</button>
        </>
      );
      break;
    }
    case "save": {
      const n = hasDeps === "yes" ? deps.filter((d) => filled(d)).length : 0;
      const nAccts = accts.filter((a) => filled(a)).length;
      const told = [
        `you (${you.name.trim()})`,
        ...(couple ? [`your spouse (${spouse.name.trim()})`] : []),
        ...(n ? [`${n} ${n === 1 ? "dependent" : "dependents"}`] : []),
        "your home address",
        ...(phone.trim() ? ["your phone number"] : []),
        ...(email.trim() ? ["your email address"] : []),
        ...(nAccts ? [`${nAccts} ${nAccts === 1 ? "account" : "accounts"}`] : []),
      ];
      const kind = pass.show ? "text" : "password";
      title = "Last step: protect and save";
      lede = <>Your Taxonymous file will hold what you told it: {told.slice(0, -1).join(", ")}
        {told.length > 1 ? " and " : ""}{told[told.length - 1]}. A passphrase keeps it unreadable to anyone else.</>;
      go = saving ? "Saving…" : "Save my Taxonymous file";
      body = (
        <>
          <input type="text" autoComplete="username" value="Taxonymous file" readOnly hidden aria-hidden="true" />
          <Field label="Choose a passphrase" type={kind} value={pass.p1} onChange={(p1) => setPass({ ...pass, p1 })}
                 error={errors.p1} autoFocus autoComplete="new-password"
                 hint="At least 8 characters. A few words you'll remember works well, like a short sentence." />
          {!pass.show && (
            <Field label="Type it again" type="password" value={pass.p2} onChange={(p2) => setPass({ ...pass, p2 })}
                   error={errors.p2} autoComplete="new-password" />
          )}
          <label className="ws-setup-check">
            <input type="checkbox" checked={pass.show} onChange={(e) => setPass({ ...pass, show: e.target.checked })} />
            Show my passphrase as I type
          </label>
          <div className="ws-setup-warn">
            <strong>Write your passphrase down</strong> and keep it with your tax papers. If it's lost, no one can
            open your file, not even Taxonymous: you'd have to set up again.
          </div>
          <p className="ws-setup-where">
            {HOST?.folder
              ? <>When you click Save, your computer asks where to keep your Taxonymous file. Your Documents folder
                is a good place. You'll open this file each time you use Taxonymous.</>
              : <>Your Taxonymous file will be saved to your Downloads folder. You'll open it each time you use
                Taxonymous, so move it somewhere you'll find it, like your Documents folder.</>}
          </p>
          {saving && s.stage === "downloading" && s.progress && (
            <p className="ws-setup-behind" role="status">
              Finishing setup on your computer first: {s.progress.text}. This happens only once.
            </p>
          )}
          {problem && <p className="error" role="alert">{problem}</p>}
        </>
      );
      break;
    }
  }

  return (
    <div className="ws-setup" ref={pane}>
      <form className="ws-setup-inner" onSubmit={next} noValidate key={step}>
        <div className="ws-setup-top">
          <span>Step {at + 1} of {steps.length}</span>
          <Behind s={s} />
        </div>
        <h1>{title}</h1>
        {lede && <p className="lede">{lede}</p>}
        <div className="ws-setup-body">{body}</div>
        <div className="ws-setup-foot">
          {step !== "who" && (
            <p className="ws-setup-local">
              <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M8 1a3.5 3.5 0 0 0-3.5 3.5V6H4a1.5 1.5 0 0 0-1.5 1.5v6A1.5 1.5 0 0 0 4 15h8a1.5 1.5 0 0 0 1.5-1.5v-6A1.5 1.5 0 0 0 12 6h-.5V4.5A3.5 3.5 0 0 0 8 1Zm2 5H6V4.5a2 2 0 1 1 4 0V6Z"/></svg>
              What you type stays on your computer. None of it is sent over the internet.
            </p>
          )}
          <button type="button" className="wbtn big" onClick={back} disabled={saving}>Back</button>
          <button type="submit" className="wbtn primary big" disabled={saving}>{go}</button>
        </div>
      </form>
    </div>
  );
}
