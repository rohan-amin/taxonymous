import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api, ApiError } from "./api";
import type { Address, Person, Profile } from "./api";

type ListKey = "phones" | "emails";
type RecordKey = "accounts" | "eins" | "cards";
type Row = Record<string, string>;

const ROLES: [string, string][] = [
  ["taxpayer", "You"], ["spouse", "Spouse"], ["dependent", "Dependent"], ["other", "Other"],
];
const NEW_PERSON: Person = { name: "", ssn: "", dob: "", role: "taxpayer", deceased: false, dod: "" };
const NEW_ADDRESS: Address = { street: "", city: "", state: "", zip: "" };
const LISTS: { key: ListKey; title: string; label: string; add: string; placeholder: string }[] = [
  { key: "phones", title: "Phone numbers", label: "Phone number", add: "Add a phone number",
    placeholder: "e.g. (555) 555-0100" },
  { key: "emails", title: "Email addresses", label: "Email address", add: "Add an email address",
    placeholder: "e.g. you@example.com" },
];

// records with one identifier (redacted) and labels that are only for you
const RECORDS: { key: RecordKey; title: string; noun: string; add: string; hint: string; blank: Row;
                 fields: { f: string; label: string; placeholder: string; secret?: boolean; wide?: boolean }[] }[] = [
  { key: "accounts", title: "Accounts", noun: "account", add: "Add an account", blank: { institution: "", number: "", name: "" },
    hint: "Bank, brokerage, IRA, 529, HSA, loan and mortgage accounts. Only the number is removed from documents.",
    fields: [
      { f: "institution", label: "Institution", placeholder: "e.g. Contoso Bank", wide: true },
      { f: "number", label: "Account number", placeholder: "Full number", secret: true },
      { f: "name", label: "Account name", placeholder: "e.g. Joint checking", wide: true },
    ] },
  { key: "eins", title: "EINs", noun: "EIN", add: "Add an EIN", blank: { ein: "", description: "" },
    hint: "Only ones that are yours, e.g. as a household employer, a business or a trust you manage.",
    fields: [
      { f: "ein", label: "EIN", placeholder: "e.g. 00-1234567", secret: true },
      { f: "description", label: "Description", placeholder: "e.g. Household employer", wide: true },
    ] },
  { key: "cards", title: "Credit and debit cards", noun: "card", add: "Add a card", blank: { number: "", description: "" },
    hint: "Cards that show up on receipts or statements. Only the number is removed.",
    fields: [
      { f: "number", label: "Card number", placeholder: "Full card number", secret: true },
      { f: "description", label: "Description", placeholder: "e.g. Travel rewards card", wide: true },
    ] },
];

// password managers and browsers shouldn't offer to fill or save any of this
const quiet = { autoComplete: "off", spellCheck: false, "data-1p-ignore": true, "data-lpignore": "true" };

function Field({ label, error, wide, children }: { label: string; error?: string; wide?: boolean;
                                                  children: ReactNode }) {
  return (
    <label className={`field${wide ? " wide" : ""}${error ? " invalid" : ""}`}>
      <span>{label}</span>
      {children}
      {error && <em className="field-error">{error}</em>}
    </label>
  );
}

export function Details({ onSaved, onRunAll }: { onSaved: () => void; onRunAll: () => void }) {
  const [form, setForm] = useState<Profile | null>(null);
  const [path, setPath] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [hide, setHide] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string; rerun?: boolean } | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.profile().then((s) => {
      const p = s.profile;
      setForm({ ...p, people: p.people.length ? p.people : [{ ...NEW_PERSON }],
                addresses: p.addresses.length ? p.addresses : [{ ...NEW_ADDRESS }] });
      setPath(s.path);
      setErrors(s.errors);
    });
  }, []);

  if (!form) return <div className="viewer empty">Loading…</div>;

  const change = (next: Profile) => {
    setForm(next);
    setMessage(null);
  };
  const setPerson = (i: number, patch: Partial<Person>) =>
    change({ ...form, people: form.people.map((p, j) => (j === i ? { ...p, ...patch } : p)) });
  const setAddress = (i: number, patch: Partial<Address>) =>
    change({ ...form, addresses: form.addresses.map((a, j) => (j === i ? { ...a, ...patch } : a)) });
  const setItem = (key: ListKey, i: number, v: string) =>
    change({ ...form, [key]: form[key].map((x, j) => (j === i ? v : x)) });
  const setRecord = (key: RecordKey, i: number, patch: Row) =>
    change({ ...form, [key]: (form[key] as unknown as Row[]).map((r, j) => (j === i ? { ...r, ...patch } : r)) });
  const remove = <K extends keyof Profile>(key: K, i: number) =>
    change({ ...form, [key]: (form[key] as unknown[]).filter((_, j) => j !== i) });

  const save = async () => {
    setSaving(true);
    try {
      const s = await api.saveProfile(form);
      setErrors({});
      const n = s.processed?.length ?? 0;
      setMessage(n ? { tone: "ok", rerun: true,
                       text: `Saved. ${n} document${n === 1 ? " was" : "s were"} redacted with your earlier details; run them again to use these.` }
                   : { tone: "ok", text: "Saved." });
      onSaved();
    } catch (err) {
      const e = err as ApiError;
      setErrors(e.fields ?? {});
      setMessage({ tone: "bad", text: e.message });
    } finally {
      setSaving(false);
    }
  };

  const secret = hide ? " secret" : "";

  return (
    <div className="details">
      <div className="details-head">
        <h1>Your details</h1>
        <p className="muted">
          Everything you enter here is removed from every document, in whatever format it appears
          (<code>987654323</code>, <code>XXX-XX-4323</code>, <code>ABERNATHY, AVERY</code>…). Other people's
          names and numbers are found without being listed.
        </p>
        <p className="muted small">Saved only on this computer, never uploaded: <code>{path}</code></p>
        <label className="check">
          <input type="checkbox" checked={hide} onChange={(e) => setHide(e.target.checked)} />
          hide numbers on screen
        </label>
      </div>

      <section>
        <h2>People</h2>
        <p className="muted">
          You, your spouse and your dependents: whoever appears on your return, including anyone who has
          died. A date of death is kept for reference and left readable in documents, since it matters for taxes.
        </p>
        {form.people.map((p, i) => (
          <div className="row person" key={i} data-testid="person">
            <Field label="Full name" error={errors[`people.${i}.name`]} wide>
              <input value={p.name} placeholder="e.g. First Middle Last" {...quiet}
                     onChange={(e) => setPerson(i, { name: e.target.value })} />
            </Field>
            <Field label="SSN or ITIN" error={errors[`people.${i}.ssn`]}>
              <input className={secret} value={p.ssn} placeholder="e.g. 987-65-4320" {...quiet}
                     onChange={(e) => setPerson(i, { ssn: e.target.value })} />
            </Field>
            <Field label="Date of birth" error={errors[`people.${i}.dob`]}>
              <input value={p.dob} placeholder="MM/DD/YYYY" {...quiet}
                     onChange={(e) => setPerson(i, { dob: e.target.value })} />
            </Field>
            <Field label="Who">
              <select value={p.role} onChange={(e) => setPerson(i, { role: e.target.value })}>
                {ROLES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
              </select>
            </Field>
            <label className="deceased">
              <input type="checkbox" checked={!!p.deceased}
                     onChange={(e) => setPerson(i, { deceased: e.target.checked, dod: e.target.checked ? p.dod : "" })} />
              Deceased
            </label>
            {p.deceased ? (
              <Field label="Date of death" error={errors[`people.${i}.dod`]}>
                <input value={p.dod ?? ""} placeholder="MM/DD/YYYY" {...quiet}
                       onChange={(e) => setPerson(i, { dod: e.target.value })} />
              </Field>
            ) : <div />}
            <button type="button" className="remove" title="Remove this person" aria-label="Remove this person"
                    onClick={() => remove("people", i)}>×</button>
          </div>
        ))}
        <button type="button" onClick={() => change({ ...form, people: [...form.people,
          { ...NEW_PERSON, role: form.people.length ? "dependent" : "taxpayer" }] })}>Add a person</button>
      </section>

      <section>
        <h2>Addresses</h2>
        <p className="muted">Where you live now, and anywhere you lived during the year.</p>
        {form.addresses.map((a, i) => (
          <div className="row" key={i} data-testid="address">
            <Field label="Street" error={errors[`addresses.${i}.street`]} wide>
              <input value={a.street} placeholder="e.g. 123 Main St Apt 4" {...quiet}
                     onChange={(e) => setAddress(i, { street: e.target.value })} />
            </Field>
            <Field label="City" error={errors[`addresses.${i}.city`]}>
              <input value={a.city} {...quiet} onChange={(e) => setAddress(i, { city: e.target.value })} />
            </Field>
            <Field label="State" error={errors[`addresses.${i}.state`]}>
              <input className="short" value={a.state} placeholder="e.g. WA" maxLength={2} {...quiet}
                     onChange={(e) => setAddress(i, { state: e.target.value })} />
            </Field>
            <Field label="ZIP" error={errors[`addresses.${i}.zip`]}>
              <input className="short" value={a.zip} placeholder="e.g. 98101" {...quiet}
                     onChange={(e) => setAddress(i, { zip: e.target.value })} />
            </Field>
            <button type="button" className="remove" title="Remove this address" aria-label="Remove this address"
                    onClick={() => remove("addresses", i)}>×</button>
          </div>
        ))}
        <button type="button" onClick={() => change({ ...form, addresses: [...form.addresses, { ...NEW_ADDRESS }] })}>
          Add an address
        </button>
      </section>

      {RECORDS.map(({ key, title, noun, add, hint, blank, fields }) => (
        <section key={key} data-testid={`records-${key}`}>
          <h2>{title}</h2>
          <p className="muted">{hint}</p>
          {(form[key] as unknown as Row[]).map((r, i) => (
            <div className="row" key={i} data-testid={`record-${key}`}>
              {fields.map(({ f, label, placeholder, secret: isSecret, wide }) => (
                <Field key={f} label={label} error={errors[`${key}.${i}.${f}`]} wide={wide}>
                  <input className={isSecret ? secret : ""} value={r[f] ?? ""} placeholder={placeholder} {...quiet}
                         onChange={(e) => setRecord(key, i, { [f]: e.target.value })} />
                </Field>
              ))}
              <button type="button" className="remove" title={`Remove this ${noun}`} aria-label={`Remove this ${noun}`}
                      onClick={() => remove(key, i)}>×</button>
            </div>
          ))}
          <button type="button" onClick={() => change({ ...form, [key]: [...form[key], { ...blank }] })}>{add}</button>
        </section>
      ))}

      {LISTS.map(({ key, title, label, add, placeholder }) => (
        <section key={key} data-testid={`list-${key}`}>
          <h2>{title}</h2>
          {form[key].map((v, i) => (
            <div className="row" key={i}>
              <Field label={`${label} ${i + 1}`} error={errors[`${key}.${i}`]} wide>
                <input value={v} placeholder={placeholder} {...quiet}
                       onChange={(e) => setItem(key, i, e.target.value)} />
              </Field>
              <button type="button" className="remove" title={`Remove this ${label.toLowerCase()}`}
                      aria-label={`Remove ${label.toLowerCase()} ${i + 1}`} onClick={() => remove(key, i)}>×</button>
            </div>
          ))}
          <button type="button" onClick={() => change({ ...form, [key]: [...form[key], ""] })}>
            {add}
          </button>
        </section>
      ))}

      <div className="details-foot">
        <button type="button" className="primary" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        {message && (
          <span className={`save-message ${message.tone}`} data-testid="details-message">
            {message.text}
            {message.rerun && <button type="button" onClick={onRunAll}>Run all again</button>}
          </span>
        )}
      </div>
    </div>
  );
}
