# Taxonymous build plan

Status: draft, 2026-10-16. See `CLAUDE.md` for settled decisions.

## Goal

Take a folder of tax documents and produce, for each one, a redacted PDF plus placeholder text that can be uploaded to Claude. Identity is removed, the numbers that matter stay, and every output passes an automated leak check before it reaches `ready-to-upload/`.

## Document-agnostic by design

The engine doesn't know or care what a document is. It finds PII wherever it appears, using the same detectors on every page. There are no per-form layout definitions and no form classifier.

The forms we expect (W-2, 1099 family incl. 1099-G, brokerage consolidated statements, K-1s and attached statements, draft Schedule H, IRA statements/5498, DAF documentation, receipts) are the **test corpus** and nothing more. They were chosen because together they cover every input shape the engine must handle:

| Input shape | Covered by |
|---|---|
| Dense boxed forms (value far from its label) | W-2, 1099s, K-1, Schedule H, 5498 |
| Long multi-page PDFs with repeating headers/footers | Brokerage consolidated, IRA statements |
| Free-form prose | DAF letters, K-1 footnote statements |
| Photos / scans, no structure | Receipts, scanned K-1s |
| Third-party PII (not the user's own) | Schedule H employees, IRA beneficiaries |

A new document type should need no code change. If one leaks, the fix goes into a generic detector, not a special case.

## What gets redacted

Redacted: SSN/ITIN/EIN, state tax IDs, account/routing/card numbers (including masked and last-4 forms), W-2 control numbers, names of people (taxpayer, spouse, dependents, employees, beneficiaries), street addresses, DOB, phone numbers, emails.

Kept: dollar amounts, box numbers/labels, tax year, transaction dates, security names/CUSIPs, share counts, percentages, form names.

Organization names: banks, brokerages, charities and merchants are kept; employers and small private partnerships are redacted by default (toggle per document).

## Approach

Each document goes through **ingest → locate → detect → redact → verify → export**.

- **Ingest:** use the text layer if the PDF has one; otherwise render the page and OCR it with Tesseract. Every word gets a bounding box either way. Handle EXIF rotation, deskewing, and HEIC/JPG/PNG input for photos.
- **Detect** (three layers whose results are combined):
  1. **Known values (profile):** the user enters their own identifiers once (names, SSNs, addresses, DOB, spouse/dependents, household employees, account numbers). Matching is exact and also allows for formatting differences (dashes, spaces, case) and common OCR mistakes (0/O, 1/l/I, 5/S). This is the most reliable way to catch names.
  2. **Patterns:** regexes for SSN, EIN, ITIN, routing numbers (with ABA checksum), card numbers (Luhn), masked account numbers, phones, emails, and ZIP codes next to a street address.
  3. **Generic label proximity:** a shared vocabulary of PII labels (*social security number*, *TIN*, *account number*, *name*, *address*, *date of birth*, *beneficiary*, *employee*, *recipient*, *payer*…). Any text near one of these labels (right of it, below it, or inside the same drawn box) becomes a candidate for redaction. The same rule runs on every page of every document.
  4. **Repetition:** a value that both looks like an identifier and repeats in the same spot across pages (headers, footers) is redacted everywhere it appears.
- **Redact:** text PDFs go through PyMuPDF redaction annotations followed by `apply_redactions()`, which actually removes the underlying text, images and vector content. Then the PDF is stripped of metadata, XMP, annotations, form fields, attachments, JavaScript and embedded files. Scans are rasterized and black boxes are burned into the pixels, and the result is exported as a fresh PDF. No original image layer survives.
- **Placeholders:** values are numbered consistently within a run (`<SSN_A>`, `<NAME_2>`, `<ACCT_1>`) so Claude can still tell that two documents refer to the same account. The placeholder→value map goes in `vault/`.
- **Verify (fails closed):** on the output file, re-extract text **and** OCR a fresh rendering. Fail if any profile value (normalized and fuzzed) or any pattern match is still present, or if any metadata or annotations remain. Only files that pass get copied to `ready-to-upload/`.

## Workspace layout

```
<workspace>/
  inbox/              originals (never modified)
  ready-to-upload/    verified outputs only (PDF + .txt)
  vault/              profile + placeholder maps, never uploaded
  work/               intermediates, safe to delete
```

The workspace lives in Dropbox at a configurable path (see Resolved).

## Milestones

Each milestone ends with its acceptance tests passing in CI-style `uv run pytest`.

### M0 — Scaffolding
- `uv` project, `pyproject.toml`, ruff, pytest, AGPL `LICENSE`, README, `.gitignore`
- Synthetic fixture generator (`tools/fixtures/`): fill IRS fillable PDFs (W-2, 1099-INT/DIV/B/MISC/NEC/R/G, K-1s, Schedule H, 5498) with fake data. Brokerage statements, IRA statements, DAF letters and receipts have no IRS form to fill, so generate those directly. Fake SSNs only from the SSA advertising range `987-65-4320`–`4329`; EINs with an invalid `00-` prefix.
- "Scan" variants: render, rotate ±3°, add noise/blur/JPEG artifacts, photo-style perspective for receipts.
- **Accept:** `uv run pytest` passes on a clean clone; the fixture generator is deterministic (seeded).

### M1 — Text-PDF engine
- Ingest, profile matching, patterns, generic label proximity, true redaction, metadata scrub, placeholder text, vault
- CLI: `taxonymous redact <file> --profile vault/profile.json`
- **Accept:** for every text-PDF fixture (all forms, not just a subset), all seeded identifiers are gone from the output text layer; all seeded dollar amounts are still present and unchanged; `pdfinfo`/PyMuPDF shows no metadata, annotations or form fields.

### M2 — Verification gate
- Text re-extract + OCR pass + metadata checks; machine-readable report per file
- **Accept:** a deliberately broken redactor (e.g. one that only draws boxes) is caught on 100% of fixtures. A test also confirms that a value hidden under a drawn rectangle is detected.

### M3 — Scans and photos
- Tesseract ingest, preprocessing, pixel burn-in, rasterized output, using the same detectors as M1
- **Accept:** on degraded scan fixtures, zero seeded identifiers in the OCR of the output. Measure amount retention: ≥ 98% of amounts are still readable by OCR (OCR-noise limit, tracked rather than hard-gated).

### M4 — Long and free-form documents
- Repetition detector, page streaming for big files, name detection in prose (profile plus a local NER model such as spaCy, so it catches names that aren't in the profile, like third parties)
- **Accept:** a 150-page fixture finishes in < 60 s on an M-series Mac and passes verification; transaction-row count and totals match the original. Third-party names and SSNs not in the profile are caught. Includes a **held-out test:** a document type the detectors were never tuned on (e.g. a 1098 or a pay stub) must also pass, which shows the engine generalizes.

### M5 — Local API
- FastAPI bound to `127.0.0.1` only: upload, run, status, fetch original/redacted page images, add/remove a redaction, re-verify, export
- **Accept:** a test asserts the server refuses non-loopback binds. A test monkeypatches `socket` to fail on any outbound connection and the full pipeline still runs.

### M6 — Review UI
- React + TS (Vite): document list with pass/fail, side-by-side original/redacted viewer, click-to-redact / click-to-unredact, re-verify, export
- **Accept:** Playwright test: load fixture → add a manual redaction → verify → file appears in `ready-to-upload/`; a failed verification blocks export in the UI.

### M7 — Hardening
- Fuzz the redactor with odd PDFs (rotated pages, Type3 fonts, text drawn as vector paths, incremental updates, encrypted PDFs)
- Native wrapper spike (pywebview vs Tauri)

## Resolved (2026-10-16)

1. **Organization names:** keep financial institutions, charities and merchants; redact employer names and small private partnerships by default; allow a per-document toggle in the UI.
2. **Workspace location:** in Dropbox, alongside the user's existing tax files. The path is configurable. Preferably it sits outside the repo folder; if it's inside, `.gitignore` covers it.
3. **Profile storage:** plain JSON at `vault/profile.json`. It is never committed; `.gitignore` blocks `vault/`, `profile.json` and `*.profile.json` at any depth.
4. **Masked last-4 on receipts:** always redacted.

## Git safety

`.gitignore` blocks the workspace folders (`inbox/`, `ready-to-upload/`, `vault/`, `work/`), any `profile.json`, and **every PDF/image file anywhere in the repo** except under `tests/fixtures/`. M0 adds a pre-commit check that rejects staged files containing SSN-shaped strings outside the reserved fake range.
