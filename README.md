# Taxonymous

Scrub personal identifiers from tax documents **on your own machine**, so you can share them with an AI assistant (e.g. Claude) for review without exposing who you are.

> **Status:** early development (milestone M0: scaffolding and test fixtures). Nothing redacts yet. See [`docs/PLAN.md`](docs/PLAN.md).

## What it will do

- Take any tax document: W-2s, 1099s, K-1s, brokerage statements, IRA statements, letters, receipts, as text PDFs or scans/photos.
- Remove identifiers (SSNs, EINs, account numbers, names, addresses, dates of birth, phones, emails) while keeping dollar amounts and box numbers.
- **Really remove them**: the underlying text and pixels are deleted, not just covered with a black box, and metadata and form fields are stripped.
- Replace them with consistent placeholders (`<SSN_A>`, `<ACCT_1>`) and keep the mapping in a local `vault/` that is never uploaded.
- **Verify every output** by re-extracting the text and OCR-ing the result. A file only reaches `ready-to-upload/` if nothing leaks.
- Make no network calls. Everything runs locally.

## Development

Requires [uv](https://docs.astral.sh/uv/) and [Tesseract](https://github.com/tesseract-ocr/tesseract) (`brew install uv tesseract`).

```sh
uv sync
git config core.hooksPath .githooks        # enable the PII pre-commit guard (once per clone)
uv run python -m tools.fixtures.generate   # build the synthetic test corpus
uv run pytest
uv run ruff check .
```

### Test data is fake, always

Never put real tax documents in this repo. Test fixtures are generated from blank IRS forms (`tests/fixtures/irs_blank/`, public domain) filled with a fake household:

- SSNs only from the SSA advertising block `987-65-4320`–`4329`
- EINs only with the never-issued `00-` prefix
- phones `555-01xx`, emails `@example.com`, published test card numbers

`.gitignore` blocks PDFs and images everywhere except `tests/fixtures/`, plus `profile.json` and the workspace folders. The pre-commit hook (`tools/check_pii.py`) also rejects SSN- or EIN-shaped values outside the fake ranges.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
