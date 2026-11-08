// Client for the local taxonymous API (src/taxonymous/api.py).

export type Status = "new" | "queued" | "running" | "passed" | "failed" | "error";
export type Rect = [number, number, number, number]; // page points: x0, y0, x1, y1

export interface Issue {
  check: string;
  kind: string;
  page: number | null;
  detail: string;
}

// what the running job has finished: pages of its current stage, and its place in a Run all
export interface Progress {
  stage: "reading" | "redacting" | "checking" | null;
  label: string;
  done: number;
  total: number;
  job: number;
  jobs: number;
}

export interface DocSummary {
  id: string; // its path in inbox/, as an output stem: "w2", "2024/w2"
  name: string;
  folder: string; // the folder in inbox/ it is in ("2024", "2024/brokerage"), "" for inbox/ itself
  status: Status;
  version: number;
  exported: boolean;
  redactions: Record<string, number>;
  issues: Issue[];
  error: string | null;
  progress: Progress | null;
}

export interface Region {
  page: number;
  rect: Rect;
  placeholder: string;
  kind: string;
  source: string;
}

export interface Shown {
  page: number;
  rect: Rect;
  placeholder: string;
}

export interface DocDetail extends DocSummary {
  pages: [number, number][];
  regions: Region[];
  report: { passed: boolean; issues: Issue[]; pages: number; notes?: string[] } | null;
  kept: string[];
  shown: Shown[]; // single boxes left in the clear
  added: { page: number; rect: Rect }[];
}

export interface Box {
  page: number;
  rect: Rect;
}

// The token comes once in the URL that `taxonymous serve` opens. Keep it for this tab
// only, and take it out of the address bar so it doesn't end up in history or a screenshot.
function loadToken(): string {
  const url = new URL(window.location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    sessionStorage.setItem("taxonymous-token", fromUrl);
    url.searchParams.delete("token");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    return fromUrl;
  }
  return sessionStorage.getItem("taxonymous-token") ?? "";
}

export const token = loadToken();

export class ApiError extends Error {
  constructor(public status: number, message: string, public fields: Record<string, string> = {}) {
    super(message);
  }
}

export interface Person {
  name: string;
  ssn: string;
  dob: string;
  role: string;
  deceased: boolean;
  dod: string; // date of death: kept readable in documents
  occupation: string;
  employer: string;
}

export interface Address {
  street: string;
  city: string;
  state: string;
  zip: string;
  school_district: string;
}

export interface Account {
  institution: string;
  number: string;
  routing: string;
  name: string;
}

export interface Ein {
  ein: string;
  description: string;
}

export interface Card {
  number: string;
  description: string;
}

export interface OtherNumber {
  number: string;
  description: string;
}

export interface Preparer {
  name: string;
  firm: string;
  street: string;
  city: string;
  state: string;
  zip: string;
  phone: string;
  ptin: string;
  ein: string;
}

export interface Profile {
  people: Person[];
  addresses: Address[];
  phones: string[];
  emails: string[];
  accounts: Account[];
  eins: Ein[];
  cards: Card[];
  numbers: OtherNumber[];
  preparers: Preparer[];
}

// what can be added to your details from a document in one step (profile.ITEMS)
export type DetailKind = "person" | "address" | "account" | "phone" | "email" | "ein" | "card" | "number"
  | "preparer";

export interface ProfileState {
  exists: boolean;
  profile: Profile;
  errors: Record<string, string>;
  path: string;
  processed?: string[]; // documents redacted before this save
}

async function call<T>(method: string, path: string, body?: unknown, form?: FormData): Promise<T> {
  const headers: Record<string, string> = { "X-Taxonymous-Token": token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers,
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    if (detail && typeof detail === "object" && "message" in detail) {
      const d = detail as { message: string; fields?: Record<string, string> };
      throw new ApiError(res.status, d.message, d.fields ?? {});
    }
    throw new ApiError(res.status, String(detail));
  }
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("json") ? res.json() : res.text()) as Promise<T>;
}

const doc = (id: string) => `/api/documents/${id.split("/").map(encodeURIComponent).join("/")}`;

export const api = {
  profile: () => call<ProfileState>("GET", "/api/profile"),
  saveProfile: (p: Profile) => call<ProfileState>("PUT", "/api/profile", p),
  addDetail: (kind: DetailKind, item: Record<string, string>) => call("POST", "/api/profile/items", { kind, item }),
  documents: () => call<DocSummary[]>("GET", "/api/documents"),
  document: (id: string) => call<DocDetail>("GET", doc(id)),
  text: (id: string) => call<string>("GET", `${doc(id)}/text`),
  upload: (file: File, folder = "") => {
    const form = new FormData();
    form.append("file", file);
    form.append("folder", folder);
    return call<DocSummary>("POST", "/api/documents", undefined, form);
  },
  runAll: (folder = "") => call("POST", folder ? `/api/run?folder=${encodeURIComponent(folder)}` : "/api/run"),
  run: (id: string) => call("POST", `${doc(id)}/run`),
  addBoxes: (id: string, boxes: Box[]) => call("POST", `${doc(id)}/redactions`, { boxes }),
  removeRedaction: (id: string, placeholder: string) =>
    call("DELETE", `${doc(id)}/redactions?placeholder=${encodeURIComponent(placeholder)}`),
  showOne: (id: string, box: Box) => call("POST", `${doc(id)}/shown`, box),
  unshow: (id: string, index: number) => call("DELETE", `${doc(id)}/shown/${index}`),
  unkeep: (id: string, placeholder: string) =>
    call("DELETE", `${doc(id)}/kept?placeholder=${encodeURIComponent(placeholder)}`),
  verify: (id: string) => call("POST", `${doc(id)}/verify`),
  export: (id: string) => call<{ released: string[] }>("POST", `${doc(id)}/export`),
};

export function pageImage(id: string, page: number, which: "original" | "redacted", dpi: number,
                          version: number): string {
  const q = new URLSearchParams({ dpi: String(dpi), v: String(version), token });
  return `${doc(id)}/pages/${page}/${which}.png?${q}`;
}

export const busy = (s: Status) => s === "queued" || s === "running";

// how a document is named where its folder matters: "2024 / W-2.pdf"
export const label = (d: { name: string; folder: string }) => (d.folder ? `${d.folder.replaceAll("/", " / ")} / ${d.name}` : d.name);

export const SOURCE_LABEL: Record<string, string> = {
  profile: "your profile",
  pattern: "its shape",
  label: "a nearby label",
  ner: "the name model",
  repeat: "appearing elsewhere in the document",
  manual: "you",
};
