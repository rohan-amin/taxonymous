# Taxonymous

Open-source tool that scrubs sensitive information from tax documents (1099s, W-2s, etc.) locally, so they can be uploaded to Claude for review without exposing identity.

## Decisions so far (2026-10-16)

- **License:** AGPL-3.0 (lets us use PyMuPDF for true redaction).
- **Inputs:** mix of downloaded PDFs (text layer) and scans/photos, so local OCR (Tesseract) is in v1.
- **v1 scope:** redaction + verification only. No network calls, no Anthropic API. The user reviews output, then manually uploads files from `ready-to-upload/` into the Claude desktop app.
- **Outputs per document:** truly redacted PDF (text/pixels removed, metadata/annotations/form fields stripped) + placeholder text (`<SSN_A>`, `<ACCT_1>`). Only the PDF is exported to `ready-to-upload/` (user's choice, 2026-10-19), named with the user's details replaced by their kind (`Fidelity NAME Trust 1099.redacted.pdf`); the .txt stays in `work/` for verification and the UI. Placeholder→real mapping ("vault") is stored in a separate folder that is never uploaded.
- **Redact identifiers, not amounts:** SSN/ITIN/EIN, account/routing numbers, names, addresses, DOB. Dollar amounts and box numbers stay.
- **Redact only what you list (default since 2026-10-19, user's choice):** the values in Your details (names, SSNs incl. masked/last-4 forms, DOB, addresses, phones, emails, listed accounts/EINs/cards) plus anything SSN-shaped. Nothing else: an employer's name and EIN stay readable. Verification checks only those, and documents don't affect each other. Review catches misses; "Add to my details" on the review screen adds one and re-runs. The guessing detectors (labels, long numbers, name model, other documents' values) caused cascading false failures on real documents; they remain behind the profile's `find_others` setting ("Also try to find other names and numbers"), off by default. The test corpus turns it on.
- **Barcodes are blacked out, whatever they hold (2026-10-20, user's choice):** a state return's 2D barcode carries the whole return. Found by decoding each rendered page (zxing-cpp, `src/taxonymous/barcode.py`); pixels, drawn bars and barcode-font characters are removed. Verification fails a page with a barcode that still reads.
- **Verification is mandatory:** re-extract text and OCR the redacted output; fail if any known value or pattern survives.
- **Architecture:** Python core engine (no UI code) → local FastAPI server bound to 127.0.0.1 → React + TypeScript web UI with side-by-side original/redacted review. Designed so a native Mac app later wraps the same UI (pywebview/Tauri) without a rewrite.
- **Tooling:** Python with `uv`; React/TS (Vite) frontend.
- **Document-agnostic:** engine scrubs PII from any document with generic matching; no per-form layouts or special cases. Test corpus: W-2, 1099 family (incl. 1099-G), brokerage consolidated statements, K-1s (+ attached statements), draft Schedule H, IRA statements/5498, DAF documentation, receipts.
- **GitHub:** `git@github.com:rohan-amin/taxonymous.git` via single-repo SSH deploy key (`~/.ssh/taxonymous_deploy`, set in repo-local `core.sshCommand`). No `gh` CLI auth. Don't push without asking.
- **Build plan:** `docs/PLAN.md`.
- **Location:** stays in Dropbox. Dependency folders (`.venv`, `node_modules`) should be excluded from Dropbox sync.

## Commands

- `uv run pytest` / `uv run ruff check .`; `uv run pytest -m slow` adds the 150-page timing test and the whole corpus in one workspace
- `tools/fixtures/odd.py` builds odd PDFs (hidden text, broken fonts, rotation, encryption...) for `tests/test_odd_pdfs.py`; `src/taxonymous/sanitize.py` is what makes a PDF hold only what its pages show
- `uv run python -m tools.fixtures.generate` builds the fake corpus into `tests/fixtures/generated/` (gitignored); `manifest.json` there is the ground truth (`redact` / `keep` per document).
- `uv run taxonymous init|run --workspace DIR` (inbox → work → verify → ready-to-upload); `redact` and `verify` for single files. Takes PDFs (text, scanned, mixed) and photos.
- `uv run taxonymous explain --workspace DIR DOC [PLACEHOLDER]` prints why a document failed, or (with e.g. `ID_10`) why one box is there, with every value masked (letters a/A, digits #, form words kept); the user can paste its output without sharing their data
- `uv run python -m tools.viewer_diff ORIGINAL.pdf REDACTED.pdf [PAGE]` shows, per font, which text macOS (Preview) leaves out that MuPDF (the review UI) draws; prints font names and counts only
- `uv run python -m tools.pdf_shape FILE.pdf [FILE.pdf ...]` (or `--ops PAGE`) shows how a PDF is built (saves, catalog, per-page text drawing), or how one page places its text, with every string masked; for comparing an original with its redacted copy
- `uv run taxonymous serve --workspace DIR` runs the review UI + local API (127.0.0.1 only, token required); endpoints are listed in `src/taxonymous/api.py`.
- UI: `cd ui && npm install && npm run build` (React/TS/Vite, output `ui/dist`). `tests/test_ui.py` (Playwright, system Chrome) needs the build and is skipped without it. If `~/.npm` is not writable, pass `--cache` to npm.
- Pre-commit PII guard: `git config core.hooksPath .githooks` (runs `tools/check_pii.py`).

## Rules

- Never commit real tax documents or `profile.json`. `.gitignore` blocks all PDFs/images except `tests/fixtures/`; don't weaken it.
- Never place real tax documents in this repo. Use synthetic fixtures only (IRS blank forms filled with fake data).

## Open questions

- None blocking. M0–M2 done 2026-10-16, M3 (scans and photos), M4 (long and free-form documents), M5 (local API) and M6 (review UI) 2026-10-17, M7 (hardening: odd PDFs, native wrapper spike) 2026-10-18. All planned milestones are done; native packaging is the open follow-up (`docs/NATIVE.md`).
