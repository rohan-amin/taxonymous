# Taxonymous

Open-source tool that scrubs sensitive information from tax documents (1099s, W-2s, etc.) locally, so they can be uploaded to Claude for review without exposing identity.

## Decisions so far (2026-10-16)

- **License:** AGPL-3.0 (lets us use PyMuPDF for true redaction).
- **Inputs:** mix of downloaded PDFs (text layer) and scans/photos, so local OCR (Tesseract) is in v1.
- **v1 scope:** redaction + verification only. No network calls, no Anthropic API. The user reviews output, then manually uploads files from `ready-to-upload/` into the Claude desktop app.
- **Outputs per document:** truly redacted PDF (text/pixels removed, metadata/annotations/form fields stripped) + placeholder text (`<SSN_A>`, `<ACCT_1>`). Placeholder→real mapping ("vault") is stored in a separate folder that is never uploaded.
- **Redact identifiers, not amounts:** SSN/ITIN/EIN, account/routing numbers, names, addresses, DOB. Dollar amounts and box numbers stay.
- **Verification is mandatory:** re-extract text and OCR the redacted output; fail if any known value or pattern survives.
- **Architecture:** Python core engine (no UI code) → local FastAPI server bound to 127.0.0.1 → React + TypeScript web UI with side-by-side original/redacted review. Designed so a native Mac app later wraps the same UI (pywebview/Tauri) without a rewrite.
- **Tooling:** Python with `uv`; React/TS (Vite) frontend.
- **Document-agnostic:** engine scrubs PII from any document with generic detectors; no per-form layouts or special cases. Test corpus: W-2, 1099 family (incl. 1099-G), brokerage consolidated statements, K-1s (+ attached statements), draft Schedule H, IRA statements/5498, DAF documentation, receipts.
- **GitHub:** `git@github.com:rohan-amin/taxonymous.git` via single-repo SSH deploy key (`~/.ssh/taxonymous_deploy`, set in repo-local `core.sshCommand`). No `gh` CLI auth. Don't push without asking.
- **Build plan:** `docs/PLAN.md`.
- **Location:** stays in Dropbox. Dependency folders (`.venv`, `node_modules`) should be excluded from Dropbox sync.

## Commands

- `uv run pytest` / `uv run ruff check .`
- `uv run python -m tools.fixtures.generate` builds the fake corpus into `tests/fixtures/generated/` (gitignored); `manifest.json` there is the ground truth (`redact` / `keep` per document).
- Pre-commit PII guard: `git config core.hooksPath .githooks` (runs `tools/check_pii.py`).

## Rules

- Never commit real tax documents or `profile.json`. `.gitignore` blocks all PDFs/images except `tests/fixtures/`; don't weaken it.
- Never place real tax documents in this repo. Use synthetic fixtures only (IRS blank forms filled with fake data).

## Open questions

- None blocking. M0 (scaffolding + fixtures) done 2026-10-16; next is M1 (text-PDF engine).
