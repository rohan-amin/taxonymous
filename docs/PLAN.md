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
| Third-party PII (not the user's own) | W-2s the household issues to its employees, IRA beneficiaries |

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

### M0 — Scaffolding ✅ (2026-10-16)
- `uv` project, `pyproject.toml`, ruff, pytest, AGPL `LICENSE`, README, `.gitignore`
- Synthetic fixture generator (`tools/fixtures/`): fill IRS fillable PDFs (W-2, 1099-INT/DIV/B/MISC/NEC/R/G, K-1s, Schedule H, 5498) with fake data. Brokerage statements, IRA statements, DAF letters and receipts have no IRS form to fill, so generate those directly. Fake SSNs only from the SSA advertising range `987-65-4320`–`4329`; EINs with an invalid `00-` prefix.
- "Scan" variants: render, rotate ±3°, add noise/blur/JPEG artifacts, photo-style perspective for receipts.
- **Accept:** `uv run pytest` passes on a clean clone; the fixture generator is deterministic (seeded).

### M1 — Text-PDF engine ✅ (2026-10-16)
- Ingest, profile matching, patterns, generic label proximity, true redaction, metadata scrub, placeholder text, vault
- CLI: `taxonymous redact <file> --profile vault/profile.json`
- **Accept:** for every text-PDF fixture (all forms, not just a subset), all seeded identifiers are gone from the output text layer; all seeded dollar amounts are still present and unchanged; `pdfinfo`/PyMuPDF shows no metadata, annotations or form fields.

### M2 — Verification gate ✅ (2026-10-16)
- Text re-extract + OCR pass + metadata checks; machine-readable report per file
- **Accept:** a deliberately broken redactor (e.g. one that only draws boxes) is caught on 100% of fixtures. A test also confirms that a value hidden under a drawn rectangle is detected.

### M3 — Scans and photos ✅ (2026-10-17)
- Tesseract ingest, preprocessing, pixel burn-in, rasterized output, using the same detectors as M1
- **Accept:** on degraded scan fixtures, zero seeded identifiers in the OCR of the output. Measure amount retention: ≥ 98% of amounts are still readable by OCR (OCR-noise limit, tracked rather than hard-gated).
- **Result:** 10 degraded fixtures per seed: 6 flatbed scans (W-2, K-1, 1099-INT, 1099-R, IRA statement, 2-page brokerage), 2 phone photos (receipt, DAF letter), a scanner-OCR PDF (scan + invisible text layer), and a text PDF with a scanned receipt attached. Seeds 7, 99, 2024, 31337: 0 leaks, 40/40 pass verification. Amount retention 98.6% on seed 7. A pixel check on the output (is a kept amount under a black box?) found one partly covered amount across all four seeds; every other unreadable amount is OCR noise.
- How it works:
  - Page routing: a page with visible text takes the M1 path; if it also has images, they are OCR'd and identifiers inside them are removed with `apply_redactions` (image pixels). A page with no text, or only a scanner's invisible OCR layer, is rendered at the scan's own resolution, OCR'd, burned and replaced by one fresh JPEG image.
  - Each image is read up to 4 times (as is and deskewed × Tesseract `--psm 3` and `--psm 11`); redaction uses the union. A value redacted in any read is redacted wherever it appears in every read.
  - OCR lines are split at column gaps and text-size jumps (form boxes), so a label's value never swallows the amount in the next column. Digit-like letters in number-like tokens are folded back (`634619IOO8` → `6346191008`). Burned boxes cover whole words plus padding.
  - Card numbers are found after a card brand (`VISA **##88*94444`) however OCR mangles the mask. An address block under a label ends at its city/state/ZIP line.
  - Photos: JPG/PNG/TIFF/WebP/HEIC, EXIF rotation applied, EXIF never copied. Output is `<name>.<ext>.redacted.pdf`.
  - Verification OCRs with both modes and tolerates OCR letter confusions in names (`Iyer`/`lyer`).
  - A file that can't be processed (password-protected, corrupt) is held back with an error report; the rest of the run continues.

### M4 — Long and free-form documents ✅ (2026-10-17)
- Repetition detector, page streaming for big files, name detection in prose (profile plus a local NER model such as spaCy, so it catches names that aren't in the profile, like third parties)
- **Accept:** a 150-page fixture finishes in < 60 s on an M-series Mac and passes verification; transaction-row count and totals match the original. Third-party names and SSNs not in the profile are caught. Includes a **held-out test:** a document type the detectors were never tuned on (e.g. a 1098 or a pay stub) must also pass, which shows the engine generalizes.
- **Result:**
  - 150-page brokerage statement: redact + verify in 47 s on an M4 Pro (14 cores); all 9,536 transaction rows and the proceeds total match. `uv run pytest -m slow` runs it.
  - Seeds 7, 99, 2024, 31337: 24 text documents and 11 scans/photos each, 0 leaks, all pass verification. Scan amount retention 98.7% (1,179/1,195) across the four seeds.
  - New fixtures: a K-1's prose statement (outsiders' names, an outsider's SSN in a sentence, an account number labelled on page 1 and bare on page 2, a private partnership's name as a heading), and its scan.
  - **Held-out:** Form 1098 passed on its first run and has not been tuned on. Three other types failed their first run, each exposing a generic gap that was then fixed: the pay stub (a value to the right of its label in a separate text object), a property tax bill (unlabelled parcel and tax account numbers), and a childcare provider's statement ("Provider: <name>" and the provider's surname in the business name). Those three are regular fixtures now; `tools/fixtures/heldout.py` explains the rule.
- How it works:
  - Names in prose: spaCy's transformer model `en_core_web_trf`, run locally on prose only (sentences, greetings, sign-offs). The smaller `sm`/`md`/`lg` models missed uncommon surnames. In forms and tables the model tags captions and security names as people, so those stay with the label detector. The model also finds private organizations in prose (a legal suffix like LLC/LP, and not a bank/broker/charity).
  - Repetition: everything any detector finds on any page (or any OCR read) is searched for on every other page. A full name passes its parts on, so "Oyelaran" alone gets the same placeholder as "Margarethe Oyelaran" (for names from a label, only when the model agrees the string is a person).
  - New generic rules: a value to the right of its label on the same row; a bare number of 7+ digits is an ID unless it is an amount, a date or a form's OMB number; a person's role followed by a colon ("Provider:", "Executor:") labels a name; a line that is only a private organization's legal name (not a table cell) is redacted.
  - Scans are read 8 pages at a time; page images are dropped once read and rendered again when the page is rebuilt.
  - Verification gives dense text pages (no images, > 1,500 characters) one OCR pass instead of two; that halves its time on long statements.
  - Cost: the model is 436 MB and pulls in PyTorch (the venv is ~1.2 GB). Loading it takes ~6 s and peaks at 2.7 GB of memory, 0.9 GB resident after. It loads only when a document has prose.

### M5 — Local API ✅ (2026-10-17)
- FastAPI bound to `127.0.0.1` only: upload, run, status, fetch original/redacted page images, add/remove a redaction, re-verify, export
- **Accept:** a test asserts the server refuses non-loopback binds. A test monkeypatches `socket` to fail on any outbound connection and the full pipeline still runs.
- **Result:** `taxonymous serve --workspace DIR` (`src/taxonymous/api.py` lists the endpoints). `tests/test_api.py` refuses every socket connection and name lookup for the whole module. Under that, it uploads a text PDF, a scan and a photo; processes them (OCR and the name model included); adds and removes boxes; keeps and un-keeps a detection; re-verifies; and exports. It then asserts that nothing tried to connect. Binding to `0.0.0.0`, `::`, LAN addresses or host names is refused before anything starts.
- How it works:
  - Access: loopback bind only; requests must name a loopback host (blocks DNS rebinding); every request needs the token printed at launch (header, or `?token=` for `<img>` URLs), so another site open in the same browser can't drive the API. No CORS, no docs endpoints.
  - One worker thread runs every redaction and verification, so the vault and outputs have a single writer. Every reviewer change re-runs the document; export is refused while it is queued or running, or if its last verification did not pass. The API never releases on its own, unlike `taxonymous run`.
  - Reviewer decisions live in `vault/overrides/<doc>.json` as placeholders and boxes, never values. A drawn box becomes `<REDACTED_n>`. The text under it (text layer, or what OCR read inside it) becomes a known value, so verification checks it is gone. A detected box can be left in the clear two ways. *Everywhere in this document* keeps the placeholder in the clear, and verification treats its values as allowed here (other documents still redact them). *This box only* records the box's page and position: only that occurrence is left, and verification blanks exactly that spot in a scratch copy (and allows the value once on that page of the .txt), so any other copy of the value is still a leak.
  - Boxes are listed per document in `work/<doc>.regions.json` (page, rectangle, placeholder, kind, detector) for the UI to draw. Original and redacted page images share page coordinates.

### M6 — Review UI ✅ (2026-10-17)
- React + TS (Vite): document list with pass/fail, side-by-side original/redacted viewer, click-to-redact / click-to-unredact, re-verify, export
- **Accept:** Playwright test: load fixture → add a manual redaction → verify → file appears in `ready-to-upload/`; a failed verification blocks export in the UI.
- **Result:** `taxonymous serve` opens the UI in the browser. `tests/test_ui.py` drives it in Chrome against a live server. It covers mirrored scrolling and paging; marking an amount, the final scrub, the preview and export, after which the released PDF no longer has the amount; a re-verify failure disabling Export until the document is run again; and leaving a detection readable, then redacting it again.
- How it works:
  - Original and redacted panes show every page at the same scale. Scrolling either one scrolls the other to the same spot; ‹ › (or ← → / [ ]) page both. Zoom keeps the same spot in view. What was redacted is outlined on the original too, so the two can be compared at a glance.
  - Mark areas (M) to drag boxes on either pane. Marks show on both panes and can be removed one by one. Final scrub sends them all at once: one re-run, then a full verification. The preview outlines the newly removed areas in green and says whether verification passed.
  - Clicking a black box shows its placeholder, kind and which detector found it. "Show this box only" leaves just that box readable; "Show it everywhere in this document" leaves every copy readable (offered when there is more than one). Both are listed under the toolbar with "redact again", and a box left readable is outlined so you can click it to redact it again. A box you drew can be removed. Marks never spread: a drawn box redacts only where it is drawn.
  - Export is disabled while there are unapplied marks, while the document is being processed, and unless its last verification passed.
  - The token arrives in the URL once and is moved to session storage, out of the address bar. Files can be added with the button or by dropping them on the window. The list refreshes itself, so runs started from the CLI show up.
  - Build with `cd ui && npm install && npm run build` (output in `ui/dist`, gitignored). `npm run dev` serves the UI on :5173 against `taxonymous serve --no-browser`.

### M7 — Hardening
- Fuzz the redactor with odd PDFs (rotated pages, Type3 fonts, text drawn as vector paths, incremental updates, encrypted PDFs)
- Native wrapper spike (pywebview vs Tauri)

## Known limitations after M6

- Scanned pages are output as images, so they are bigger than the original and have no text layer except placeholders; the `.txt` carries the (redacted) OCR text instead.
- OCR word boxes are sometimes taller than the text. The padded black box can then clip the top of the line below (seen once in 40 degraded fixtures).
- A value OCR can't read at all can't be detected. The verifier OCRs the output the same way, so it can't see it either. Low-quality scans should be reviewed by eye (the review UI is M6).
- Rotated pages (`/Rotate`), and pictures layered on top of other pictures in a text PDF, are untested (M7).
- A third party's name that is neither next to a label nor in prose (a name alone in a table cell, a heading that isn't a legal entity name) is not caught unless it also appears somewhere that is.
- The name model can still miss a name; a surname alone is caught reliably only when the full name also appears in the document.
- A bare 7+ digit number on a scan is treated as an ID, so an amount OCR misread without its decimal point (`15,158.60` read as `1515880`) is redacted. A few amounts per 300 on degraded scans.
- The 150-page timing is on a 14-core M4 Pro; verification is CPU-bound, so a Mac with fewer cores will be slower.
- Keeping a placeholder in the clear also keeps any shorter value that verification would otherwise flag inside it. It is a reviewer decision, and the review UI should make it a deliberate one.
- Every reviewer change re-runs the whole document, which is slow for a long scan (a few seconds per page).
- The UI renders every page image of a document (lazily); a several-hundred-page statement scrolls fine but zooming re-requests the images at the new resolution.
- A merchant's or payer's own street address and phone are redacted by the patterns even when the organization is public (e.g. on a receipt). "Show it everywhere in this document" handles it per document.
- The public vs. private organization split is a keyword heuristic (`bank`, `securities`, `charitable`… versus `LLC`, `LP`, `family trust`). The review UI (M6) is the backstop.
- Your first and last names are matched on their own anywhere in the text, so a first name that is also a common word (e.g. "Grant") will be over-redacted.
- The label detector was developed against the corpus layouts. Of four held-out types, one passed untouched and three needed generic fixes; expect real documents to find more gaps, which is what review (M6) is for.

## Resolved (2026-10-16)

1. **Organization names:** keep financial institutions, charities and merchants; redact employer names and small private partnerships by default; allow a per-document toggle in the UI.
2. **Workspace location:** in Dropbox, alongside the user's existing tax files. The path is configurable. Preferably it sits outside the repo folder; if it's inside, `.gitignore` covers it.
3. **Profile storage:** plain JSON at `vault/profile.json`. It is never committed; `.gitignore` blocks `vault/`, `profile.json` and `*.profile.json` at any depth.
4. **Masked last-4 on receipts:** always redacted.

## Git safety

`.gitignore` blocks the workspace folders (`inbox/`, `ready-to-upload/`, `vault/`, `work/`), any `profile.json`, and **every PDF/image file anywhere in the repo** except under `tests/fixtures/`. M0 adds a pre-commit check that rejects staged files containing SSN-shaped strings outside the reserved fake range.
