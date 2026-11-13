// Plain-language status for a document: what happened, whose problem it is, what to do.
// The wording lives here, in one place, instead of the server's check names
// ("SSN on page 2 (ocr)", "xmp-metadata (structure)").
import type { DocSummary, Issue, Report } from "../api";

export type Tone = "ok" | "look" | "stop" | "work" | "new";
export interface Plain {
  tone: Tone;
  title: string;
  body: string;
}

const WHAT: Record<string, string> = {
  NAME: "your name",
  SSN: "your SSN",
  TIN: "a tax ID",
  ADDR: "your address",
  DOB: "your date of birth",
  ACCT: "an account number",
  ROUTING: "a routing number",
  CARD: "a card number",
  PHONE: "your phone number",
  EMAIL: "your email address",
  EIN: "your EIN",
  ID: "one of your ID numbers",
  OCCUPATION: "your occupation",
  ORG: "your employer",
};

// what was removed, counted: "2 names, 1 address and 2 account numbers"
const NOUNS: Record<string, [string, string]> = {
  NAME: ["name", "names"], SSN: ["SSN", "SSNs"], TIN: ["tax ID", "tax IDs"], ADDR: ["address", "addresses"],
  DOB: ["date of birth", "dates of birth"], ACCT: ["account number", "account numbers"],
  ROUTING: ["routing number", "routing numbers"], CARD: ["card number", "card numbers"],
  PHONE: ["phone number", "phone numbers"], EMAIL: ["email address", "email addresses"], EIN: ["EIN", "EINs"],
  ID: ["ID number", "ID numbers"], OCCUPATION: ["occupation", "occupations"], ORG: ["employer", "employers"],
  BARCODE: ["barcode", "barcodes"], REDACTED: ["box you drew", "boxes you drew"],
};
export function removedList(counts: Record<string, number>): string {
  const parts = Object.entries(counts).filter(([, n]) => n > 0)
    .sort(([a], [b]) => Object.keys(NOUNS).indexOf(a) - Object.keys(NOUNS).indexOf(b))
    .map(([k, n]) => `${n} ${(NOUNS[k] ?? ["other detail", "other details"])[n === 1 ? 0 : 1]}`);
  return parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0] ?? "";
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const what = (kind: string) => WHAT[kind] ?? "one of your details";
const onPage = (i: Issue) => (i.page ? ` on page ${i.page}` : "");

const LEAKS = new Set(["text", "txt", "ocr", "raw"]);

function failed(issues: Issue[]): Plain {
  const leak = issues.find((i) => LEAKS.has(i.check));
  if (leak) {
    const how = leak.check === "ocr"
      ? `This page is a scan, so after cleaning we read it again the way a person would, and could still make out ${what(leak.kind)}.`
      : leak.check === "text"
        ? "It's in the text behind the page, which an AI reads even where you can't see it."
        : "It's still in the document's text.";
    return { tone: "look", title: `${cap(what(leak.kind))} may still be readable${onPage(leak)}`,
             body: `${how} Draw a box over it and it's checked again.` };
  }
  const barcode = issues.find((i) => i.kind === "barcode");
  if (barcode) {
    return { tone: "look", title: `A barcode${onPage(barcode)} can still be scanned`,
             body: "State returns often store the whole return in a barcode, your SSN included. Draw a box over the barcode and it's checked again." };
  }
  return { tone: "stop", title: "We couldn't finish cleaning this file",
           body: "Some hidden information inside the PDF couldn't be removed. That's a problem in Taxonymous, not something you did, and nothing has been shared." };
}

function broken(error: string): Plain {
  const e = error.toLowerCase();
  if (e.includes("password")) {
    return { tone: "stop", title: "This PDF is locked with a password",
             body: "Open it in Preview with its password, choose File › Export as PDF, and add the new copy. Your original isn't changed." };
  }
  if (e.includes("tesseract")) {
    return { tone: "stop", title: "Part of Taxonymous is missing, so scans can't be checked",
             body: "Reinstall Taxonymous to fix it. PDFs with a text layer still work in the meantime." };
  }
  return { tone: "stop", title: "This file couldn't be opened",
           body: "It may be damaged or only partly downloaded. Download it again from where it came from, then add the new copy." };
}

export function plain(d: DocSummary, hasProfile: boolean): Plain {
  const removed = removedList(d.redactions ?? {});
  switch (d.status) {
    case "passed":
      return { tone: "ok", title: "Checked: none of your details were found",
               body: `${removed ? `Removed ${removed}. ` : "Nothing of yours was on it. "}Then the copy was read again from scratch, and nothing from My profile turned up.` };
    case "queued":
      return { tone: "work", title: "Waiting its turn…", body: "It's cleaned and checked as soon as the documents ahead of it are done." };
    case "running": {
      const left = timeLeft(d.progress?.eta);
      return { tone: "work", title: d.progress?.label ? `${d.progress.label}…` : "Cleaning and checking…",
               body: left ? `${left.charAt(0).toUpperCase()}${left.slice(1)}.` : "This takes a few seconds a page." };
    }
    case "failed":
      return failed(d.issues ?? []);
    case "error":
      return broken(d.error ?? "");
    default:
      return hasProfile
        ? { tone: "new", title: "Not cleaned yet", body: "Choose Clean this document, or it's cleaned when you add your details." }
        : { tone: "new", title: "Not cleaned yet", body: "Choose Click a value and click your name on the page, or open My profile to type your details. Then every document is cleaned and checked." };
  }
}

const pages = (n: number) => `${n} page${n === 1 ? "" : "s"}`;
const both = (xs: string[]) => xs.join(" and ");

// what checking a passed document did, for "What was checked": every claim is one the report backs
export function checked(r: Report): string[] {
  const out = [`The text of all ${pages(r.pages)} was read again: none of your details were in it.`];
  const engines = r.engines ?? [];
  const read = engines.filter((e) => e.length > 0);
  if (r.ocr === false || !read.length) {
    out.push("The pages weren't read as pictures (OCR was turned off), so text drawn as an image wasn't checked.");
  } else {
    const all = [...new Set(read.flat())];
    const everyPage = read.length === r.pages && read.every((e) => both(e) === both(read[0]));
    const pics = r.pictures?.length ?? 0;
    out.push(everyPage
      ? `Every page was also read as a picture, the way a person sees it, by ${both(all)}: none of your details turned up.`
      : `Every page was also read as a picture, the way a person sees it, by ${both(all.filter((e) => read.every((x) => x.includes(e))))}${pics ? `, and the ${pics === 1 ? "page with a picture on it" : `${pics} pages with pictures on them`} by ${both(all)} too` : ""}: none of your details turned up.`);
  }
  out.push("Every page was checked for barcodes: none can be scanned.");
  out.push("Hidden data was removed: the file's details, form fields, comments and attachments.");
  for (const note of r.notes ?? []) out.push(note);
  return out;
}

// "about 2 minutes left": rounded, so it doesn't flicker as it counts down
export function timeLeft(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  if (seconds < 10) return "a few seconds left";
  if (seconds < 60) return `about ${Math.ceil(seconds / 10) * 10} seconds left`;
  const m = Math.round(seconds / 60);
  return m <= 1 ? "about a minute left" : `about ${m} minutes left`;
}

export const DOT: Record<Tone, string> = { ok: "ok", look: "look", stop: "stop", work: "work", new: "new" };
