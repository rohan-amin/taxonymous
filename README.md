# Taxonymous

Scrub personal identifiers from tax documents **on your own machine**, so you can share them with an AI assistant (e.g. Claude) for review without exposing who you are.

> **Status:** early development. Text-layer PDFs are redacted (M1) and every output is verified, including by OCR, before release (M2). Scans and photos (M3) are not supported yet, and nothing has been tested on real documents, so **do not rely on it yet**. See [`docs/PLAN.md`](docs/PLAN.md).

## What it will do

- Take any tax document: W-2s, 1099s, K-1s, brokerage statements, IRA statements, letters, receipts, as text PDFs or scans/photos.
- Remove identifiers (SSNs, EINs, account numbers, names, addresses, dates of birth, phones, emails) while keeping dollar amounts and box numbers.
- **Really remove them**: the underlying text and pixels are deleted, not just covered with a black box, and metadata and form fields are stripped.
- Replace them with consistent placeholders (`<SSN_A>`, `<ACCT_1>`) and keep the mapping in a local `vault/` that is never uploaded.
- **Verify every output** by re-extracting the text and OCR-ing the result. A file only reaches `ready-to-upload/` if nothing leaks.
- Make no network calls. Everything runs locally.

## Usage (preview)

```sh
uv run taxonymous init --workspace ~/Dropbox/Taxes/taxonymous   # creates inbox/ work/ ready-to-upload/ vault/
# put your identifiers in vault/profile.json (format: src/taxonymous/profile.py), PDFs in inbox/
uv run taxonymous run --workspace ~/Dropbox/Taxes/taxonymous
```

Only files that pass verification are copied to `ready-to-upload/`. Failures stay in `work/` with a `*.report.json` that names the kind of leak and the page, never the value.

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
