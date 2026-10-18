# Taxonymous

Scrub personal identifiers from tax documents **on your own machine**, so you can share them with an AI assistant (e.g. Claude) for review without exposing who you are.

> **Status:** early development. Text PDFs (M1), scans and photos (M3), and long and free-form documents with names in prose (M4) are redacted, and every output is verified, including by OCR, before release (M2). A review screen shows each original and its redacted version side by side, lets you mark anything missed, and re-verifies before export (M6). Odd PDFs (text hidden off the page, in hidden layers, bookmarks or broken fonts) are handled and tested (M7). Nothing has been tested on real documents yet, so **do not rely on it yet**. See [`docs/PLAN.md`](docs/PLAN.md).

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
# put your identifiers in vault/profile.json (format: src/taxonymous/profile.py),
# PDFs and photos (JPG, PNG, HEIC, TIFF) in inbox/
uv run taxonymous run --workspace ~/Dropbox/Taxes/taxonymous
```

To review before uploading:

```sh
(cd ui && npm install && npm run build)                        # once, to build the review screen
uv run taxonymous serve --workspace ~/Dropbox/Taxes/taxonymous  # opens it in your browser
```

Each document is shown original and redacted side by side; scrolling and paging move both together. Use **Mark areas** to drag over anything that was missed, then **Final scrub** to remove it all, re-verify, and preview the result. **Export** copies a document to `ready-to-upload/`, and only once it has passed. The server only listens on 127.0.0.1 and needs the token in the link it opens.

To try the review screen in a native window instead of the browser (an experiment, see [`docs/NATIVE.md`](docs/NATIVE.md)): `uv run --with pywebview python -m tools.native_window --workspace DIR`.

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
