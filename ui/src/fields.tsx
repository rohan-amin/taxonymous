// The fields of Your details, shared by its form and by "Add to my details" on the review screen.
import type { ReactNode } from "react";
import type { DetailKind } from "./api";

export type ListKey = "phones" | "emails";
export type RecordKey = "accounts" | "eins" | "cards" | "numbers" | "preparers";
export type Row = Record<string, string>;

// who a person is: their labels in the safe copies say it (<TAXPAYER>, <SPOUSE>, <TRUSTEE_1>)
export const ROLES: [string, string][] = [
  ["taxpayer", "You (taxpayer)"], ["spouse", "Spouse"], ["dependent", "Dependent"],
  ["grantor", "Grantor"], ["trustee", "Trustee"], ["successor_trustee", "Successor trustee"],
  ["beneficiary", "Beneficiary"], ["executor", "Executor"], ["decedent", "Decedent"], ["other", "Other"],
];
export const LISTS: { key: ListKey; title: string; label: string; add: string; placeholder: string }[] = [
  { key: "phones", title: "Phone numbers", label: "Phone number", add: "Add a phone number",
    placeholder: "e.g. (555) 555-0100" },
  { key: "emails", title: "Email addresses", label: "Email address", add: "Add an email address",
    placeholder: "e.g. you@example.com" },
];

// records with identifiers (redacted) and labels that are only for you
export const RECORDS: { key: RecordKey; title: string; noun: string; add: string; hint: string; blank: Row;
                 fields: { f: string; label: string; placeholder?: string; secret?: boolean; wide?: boolean }[] }[] = [
  { key: "accounts", title: "Accounts", noun: "account", add: "Add an account",
    blank: { institution: "", number: "", routing: "", name: "", type: "", owner: "" },
    hint: "Bank, brokerage, IRA, 529, HSA, loan and mortgage accounts. The number is removed from documents, "
      + "and so are its last 4 digits wherever they appear on their own, and the routing number if you enter one.",
    fields: [
      { f: "institution", label: "Institution", placeholder: "e.g. Contoso Bank", wide: true },
      { f: "number", label: "Account number", placeholder: "Full number", secret: true },
      { f: "routing", label: "Routing number", placeholder: "Optional, 9 digits", secret: true },
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
  { key: "numbers", title: "Other numbers", noun: "number", add: "Add a number", blank: { number: "", description: "" },
    hint: "Any other ID of yours that documents print: an e-file signature PIN, an IP PIN, a client or "
      + "customer ID. Removed wherever it appears, however it is spaced.",
    fields: [
      { f: "number", label: "Number or ID", placeholder: "e.g. 12345", secret: true },
      { f: "description", label: "Description", placeholder: "e.g. Self-select PIN", wide: true },
    ] },
  { key: "preparers", title: "Tax preparer", noun: "preparer", add: "Add a preparer",
    blank: { name: "", firm: "", street: "", city: "", state: "", zip: "", phone: "", ptin: "", ein: "" },
    hint: "Your CPA or whoever prepared the return. Everything entered here is removed: the full name "
      + "(not a first name on its own), the firm, its address, phone, PTIN and EIN.",
    fields: [
      { f: "name", label: "Preparer's name", placeholder: "e.g. First Last", wide: true },
      { f: "firm", label: "Firm", placeholder: "e.g. Contoso CPAs LLP", wide: true },
      { f: "street", label: "Street", placeholder: "e.g. 100 Main St Suite 200", wide: true },
      { f: "city", label: "City" },
      { f: "state", label: "State", placeholder: "e.g. WA" },
      { f: "zip", label: "ZIP", placeholder: "e.g. 98101" },
      { f: "phone", label: "Phone", placeholder: "e.g. (555) 555-0100" },
      { f: "ptin", label: "PTIN", placeholder: "e.g. P01234567", secret: true },
      { f: "ein", label: "Firm EIN", placeholder: "e.g. 00-1234567", secret: true },
    ] },
];

// a new entry's permanent id, made here so it can be chosen (an account's owner) before saving;
// the same form the engine makes (taxonymous/profile.py new_id)
export const newId = () => "e" + Array.from(crypto.getRandomValues(new Uint8Array(4)), (b) => b.toString(16).padStart(2, "0")).join("");

// what an account or an address is: its label says it (<TAXPAYER_ROTH_IRA_1>, <HOME_ADDR>)
export const ACCOUNT_TYPES: [string, string, string][] = [  // value, label, group
  ["", "Not set", ""], ["checking", "Checking", "Banking"], ["savings", "Savings", "Banking"],
  ["cd", "CD / money market", "Banking"], ["brokerage", "Brokerage", "Investing"],
  ["traditional_ira", "Traditional IRA", "Retirement"], ["roth_ira", "Roth IRA", "Retirement"],
  ["sep_simple_ira", "SEP / SIMPLE IRA", "Retirement"], ["401k", "401(k) / 403(b) / 457", "Retirement"],
  ["roth_401k", "Roth 401(k)", "Retirement"], ["pension", "Pension / annuity", "Retirement"],
  ["hsa", "HSA", "Health and education"], ["529", "529 plan", "Health and education"],
  ["coverdell", "Coverdell ESA", "Health and education"], ["mortgage", "Mortgage", "Loans"],
  ["student_loan", "Student loan", "Loans"], ["loan", "Other loan", "Loans"],
];
export const OWNERS: [string, string][] = [["", "Not set"], ["joint", "Joint"], ["trust", "A trust"], ["estate", "An estate"]];
export const ADDRESS_TYPES: [string, string][] = [
  ["", "Other"], ["home", "Home"], ["prior_home", "Previous home"], ["rental", "Rental property"],
  ["vacation", "Vacation / second home"], ["business", "Business / office"],
];
export const ROLE_NAME: Record<string, string> = Object.fromEntries([
  ["taxpayer", "You"], ["spouse", "Spouse"], ["dependent", "Dependent"], ["grantor", "Grantor"], ["trustee", "Trustee"],
  ["successor_trustee", "Successor trustee"], ["beneficiary", "Beneficiary"], ["executor", "Executor"],
  ["decedent", "Decedent"], ["other", "Other"],
]);

// password managers and browsers shouldn't offer to fill or save any of this
export const quiet = { autoComplete: "off", spellCheck: false, "data-1p-ignore": true, "data-lpignore": "true" };

export function Field({ label, error, wide, className, children }: { label: string; error?: string; wide?: boolean;
                                                             className?: string; children: ReactNode }) {
  return (
    <label className={`field${wide ? " wide" : ""}${error ? " invalid" : ""}${className ? ` ${className}` : ""}`}>
      <span>{label}</span>
      {children}
      {error && <em className="field-error">{error}</em>}
    </label>
  );
}

export interface FieldDef {
  f: string;
  label: string;
  placeholder?: string;
  secret?: boolean;
  wide?: boolean;
  short?: boolean;
  options?: [string, string][]; // a select
}

// what "Add to my details" can add, with the same fields as a row of Your details
export const DETAIL_FORMS: { kind: DetailKind; label: string; fields: FieldDef[] }[] = [
  { kind: "person", label: "Person", fields: [
    { f: "name", label: "Full name", placeholder: "e.g. First Middle Last", wide: true },
    { f: "ssn", label: "SSN or ITIN", placeholder: "e.g. 987-65-4320", secret: true },
    { f: "dob", label: "Date of birth", placeholder: "MM/DD/YYYY" },
    { f: "occupation", label: "Occupation", placeholder: "As on the return" },
    { f: "employer", label: "Employer", placeholder: "As on the W-2", wide: true },
    { f: "role", label: "Who", options: ROLES },
  ] },
  { kind: "address", label: "Address", fields: [
    { f: "street", label: "Street", placeholder: "e.g. 123 Main St Apt 4", wide: true },
    { f: "city", label: "City" },
    { f: "state", label: "State", placeholder: "e.g. WA", short: true },
    { f: "zip", label: "ZIP", placeholder: "e.g. 98101", short: true },
    { f: "school_district", label: "School district", placeholder: "Optional" },
  ] },
  ...RECORDS.map(({ key, noun, fields }) => ({
    kind: ({ accounts: "account", eins: "ein", cards: "card", numbers: "number", preparers: "preparer" } as const)[key],
    label: noun === "EIN" ? "EIN" : noun[0].toUpperCase() + noun.slice(1),
    fields: key === "accounts"
      ? [...fields, { f: "type", label: "Type", options: ACCOUNT_TYPES.map(([v, l]) => [v, l] as [string, string]) }]
      : fields,
  })),
  ...LISTS.map(({ key, label, placeholder }) => ({
    kind: ({ phones: "phone", emails: "email" } as const)[key],
    label,
    fields: [{ f: "value", label, placeholder, wide: true }],
  })),
];

export const blankRow = (fields: FieldDef[]): Row =>
  Object.fromEntries(fields.map(({ f, options }) => [f, options ? (f === "role" ? "other" : options[0][0]) : ""]));
