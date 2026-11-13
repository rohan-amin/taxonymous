// Plain-language status for a document: what happened, whose problem it is, what to do.
// The wording lives here, in one place, instead of the server's check names
// ("SSN on page 2 (ocr)", "xmp-metadata (structure)").
import type { DocSummary, Issue } from "../api";

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
  const removed = Object.values(d.redactions ?? {}).reduce((a, n) => a + n, 0);
  switch (d.status) {
    case "passed":
      return { tone: "ok", title: "This copy is safe to share",
               body: `${removed} item${removed === 1 ? "" : "s"} removed, then the copy was read again, scans included. None of your details were found.` };
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
        : { tone: "new", title: "Not cleaned yet", body: "Open My profile and add your details. Then every document is cleaned and checked." };
  }
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
