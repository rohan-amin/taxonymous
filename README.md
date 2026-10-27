# Taxonymous

Taxonymous removes your personal details (names, Social Security numbers, addresses, account numbers) from tax documents **on your own computer**. You can then share the cleaned-up copies with an AI assistant such as Claude, and get help with your taxes without handing over your name, Social Security number or account numbers.

> **Status:** early. It works on real returns, W-2s, 1099s and brokerage statements, but it is new. **Always look over every page on the review screen before you upload anything.**

## How it works

1. **You tell it what to remove.** Once, you enter your details: the names, SSNs, birth dates, addresses, account numbers and so on that you don't want anyone to see. They are saved in a file on your computer and never leave it.
2. **You add your documents.** PDFs downloaded from your bank or employer, scans, or phone photos.
3. **It finds and removes your details.** Every place one of your details appears, it is deleted and replaced with a label such as `<NAME_A>` or `<ACCT_1>`. Dollar amounts, box numbers and everything else stay, so the document is still useful.
4. **It checks its own work.** Each cleaned copy is read back in two ways: by its text, and by reading the page image the way a person would (OCR). If any of your details can still be found, the document fails and is held back.
5. **You review.** A screen in your web browser shows each original and its cleaned copy side by side. You can drag a box over anything it missed.
6. **You export and upload.** Documents that passed go into a `ready-to-upload` folder. You upload those files to Claude yourself, whenever you're ready.

## What it does and doesn't do

**It does:**

- Run entirely on your computer. It makes no internet connections and sends nothing anywhere. The text recognition (OCR) is a program on your computer too, not an online service.
- Truly remove your details. The text and the pixels are deleted, not just covered by a black box someone could lift off. Hidden extras inside the file (author, form fields, notes) are stripped as well.
- Remove anything that looks like a Social Security number (`987-65-4320`), even if you didn't list it.
- Black out barcodes, because a state return's barcode can hold the whole return.
- Keep amounts, box numbers, dates (other than birth dates) and other people's or companies' names readable.

**It doesn't:**

- Use AI to find your details. It looks only for the exact details you entered, in whatever format a document prints them (`987-65-4321`, `XXX-XX-4321`, `ending in 4321`). Something you didn't list, such as your employer's name or a relative's address, stays readable unless you add it.
- Upload anything for you. Nothing reaches Claude, or any other AI, until you upload files from `ready-to-upload` yourself.
- Replace your review. It double-checks itself, but you are the final check.
- Make you anonymous. See below.

### It hides your details, not your identity

A tax return is a detailed picture of your life. Even with your name, SSN, address and accounts removed, what's left can point to you: your employer and exact salary, the town you work in, a rental property, the partnerships on your K-1s, the dates things happened. A determined person who already knows something about you could put those together and work out whose return it is.

Taxonymous is built to keep the details that identify you directly away from the AI. It does not make the document impossible to trace back to you. If that matters, add more to your details (your employer, a rental property's address) and use **Mark areas** for anything else you'd rather not share.

## What you need

- **A Mac.** That's what it is built and tested on. Linux should work too; Windows hasn't been tried.
- **Some comfort with the Terminal.** Setup and starting the program mean copying and pasting a few commands. After that, everything happens in your web browser.
- **Three free programs**, installed once with [Homebrew](https://brew.sh) (a standard installer for Mac programs):
  - **uv**, which runs Taxonymous and fetches the Python libraries it needs
  - **Tesseract**, which reads text from scans and photos
  - **Node.js**, which builds the review screen (only needed during setup)
- **Your details at hand:** SSNs, birth dates, addresses, account numbers, and so on for everyone on your return.

## Setup (once)

In the Terminal:

```sh
brew install uv tesseract node                       # the three programs
git clone https://github.com/rohan-amin/taxonymous.git
cd taxonymous
(cd ui && npm install && npm run build)              # build the review screen
```

## Using it

Start it, telling it where to keep your files. Any folder works, and it is created if it doesn't exist, but pick one that isn't synced to the cloud (see [Things to consider](#things-to-consider)):

```sh
uv run taxonymous serve --workspace ~/Taxes/taxonymous
```

The review screen opens in your web browser. Keep the Terminal window open while you work; close it (or press Control-C) to stop.

1. **Your details:** fill in everyone on your return and **Save**. Only the name is required for a person; add whatever else you have.
2. **Add files** (or drag PDFs and photos onto the window), then **Run all**.
3. **Review** each document. The original is on the left, the cleaned copy on the right, and they scroll together. If something of yours is still showing:
   - **Add to my details** if it's a detail you'll want removed everywhere (for example, an account you forgot). The document is cleaned again.
   - **Mark areas** to drag a box over a single spot, then **Final scrub** to remove it and check again.
4. **Export** each document that passed. It is copied to `ready-to-upload`.
5. Upload the files from `ready-to-upload` to Claude.

## Your workspace folders

The folder you chose when starting it holds four folders:

| Folder | What's in it | Share it? |
|---|---|---|
| `inbox` | Your original documents, exactly as you added them. Never changed. | No |
| `work` | Cleaned copies and check results, including documents that failed. | No |
| `ready-to-upload` | Cleaned documents that passed every check and that you exported. | **Yes, this is the only one** |
| `vault` | Your details, and the key that matches each label (`<SSN_A>`) to the real value. | **Never** |

Exported files are renamed with your details taken out too: `Abernathy W-2.pdf` becomes `NAME W-2.redacted.pdf`.

## Your details (`vault/profile.json`)

What you enter under **Your details** is saved in `vault/profile.json`, a plain text file in your workspace. It is the list of everything to remove, and nothing else is removed (except SSN-shaped numbers and barcodes).

- Each detail is found however a document prints it: with or without dashes, in capitals, `Avery F. Abernathy` or `ABERNATHY AVERY F`, an account as `****5678` or `ending in 5678`.
- Besides names, SSNs, birth dates, addresses, phones, emails, accounts, EINs and cards, you can list: each account's routing number; other numbers of yours, such as an e-file PIN or a client ID; your tax preparer (name, firm, address, phone, PTIN, EIN); each person's occupation and employer; and each address's school district.
- An address's city and ZIP are also removed on their own, since forms print them in separate boxes. A city name is removed everywhere it appears, even as part of a bank branch's address.
- Use the screen to edit it. If you do edit the file by hand, it looks like this (fake values):

```json
{
  "people": [{"name": "Avery F Abernathy", "ssn": "987-65-4321", "dob": "04/12/1980",
              "occupation": "Teacher", "employer": "Contoso Widgets LLC"}],
  "addresses": [{"street": "5842 Fenwick Ave", "city": "Tacoma", "state": "WA", "zip": "98458"}],
  "phones": ["(555) 555-0142"],
  "emails": ["avery@example.com"],
  "accounts": [{"institution": "Bank", "number": "0001235678", "routing": "000000000"}]
}
```

Keep the `vault` folder private. It holds your real details in plain text.

## When something is missed or a document fails

A document **fails** when the check finds one of your details still in the cleaned copy. It stays in `work` and is not exported, and the review screen shows which kind of detail was found and on which page. Something is **missed** when you spot one of your details on the review screen that the check didn't flag.

Either way, there are three fixes, from quickest to most involved:

1. **It's a detail you never listed** (a second account, an old address, a preparer's phone): **Add to my details**. It is removed from this document and every other one.
2. **It's a one-off** (a handwritten note, a stray number): **Mark areas** over it, then **Final scrub**.
3. **A detail you did list wasn't recognized**, because a document prints it in a way Taxonymous doesn't know yet (an unusual mask, a city and ZIP run together). This needs a change to the matching rules. Please report it so it gets fixed for everyone; you can do that without sharing your documents.

To see what went wrong without revealing your data, run:

```sh
uv run taxonymous explain --workspace ~/Taxes/taxonymous "document name.pdf"
```

It prints the text around each problem with every letter shown as `a` and every digit as `#`. Paste that into a [GitHub issue](https://github.com/rohan-amin/taxonymous/issues), along with how the missed detail is printed, with its digits changed to fake ones (for example, `Acct ...5678`). That is enough to write the new rule.

If you're making the change yourself: the matching rules are in `src/taxonymous/detect.py` (`ProfileMatcher`). Add a test with fake values to `tests/test_detect.py` showing the line that was missed, change the rule until it passes, and run the whole test suite, because a rule that finds more can also black out things it shouldn't (an amount, a box number). Never add a real document to the repo.

## Things to consider

- **Where your workspace lives.** Your details are saved in plain text in `vault/profile.json`, and your original documents sit in `inbox`. If the workspace is inside a folder that syncs to the cloud (Dropbox, Google Drive, OneDrive, or iCloud, which often syncs Desktop and Documents), those copies go to that company's servers. Put the workspace in a folder that doesn't sync, such as `~/Taxes`, and delete it when you're done with the season.
- **It takes some time.** Scans and photos are read several times to be sure. A long return can take several minutes.
- **It is still a technical tool.** Setup uses the Terminal, and digging into a failure means running a command. A simpler, double-click Mac app is planned, but doesn't exist yet.

## Privacy details

- The review screen is served by Taxonymous itself, only to your own computer (address 127.0.0.1). Other computers on your network can't reach it, and the link it opens carries a secret key that other web pages don't have.
- The files that leave your computer are the ones you upload from `ready-to-upload`. Nothing else does.

To try the review screen in its own window instead of the browser (an experiment, see [`docs/NATIVE.md`](docs/NATIVE.md)): `uv run --with pywebview python -m tools.native_window --workspace DIR`.

## Development

Build plan and design notes: [`docs/PLAN.md`](docs/PLAN.md).

```sh
uv sync
git config core.hooksPath .githooks        # enable the PII pre-commit guard (once per clone)
uv run python -m tools.fixtures.generate   # build the synthetic test corpus
uv run pytest
uv run ruff check .
```

Command-line use without the review screen: `uv run taxonymous init|run --workspace DIR` cleans and checks everything in `inbox` and copies what passes to `ready-to-upload`.

### Test data is fake, always

Never put real tax documents in this repo. Test fixtures are generated from blank IRS forms (`tests/fixtures/irs_blank/`, public domain) filled with a fake household:

- SSNs only from the SSA advertising block `987-65-4320`–`4329`
- EINs only with the never-issued `00-` prefix
- phones `555-01xx`, emails `@example.com`, published test card numbers

`.gitignore` blocks PDFs and images everywhere except `tests/fixtures/`, plus `profile.json` and the workspace folders. The pre-commit hook (`tools/check_pii.py`) also rejects SSN- or EIN-shaped values outside the fake ranges.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).

In short: it's free to use, share and change, for any purpose. If you share a changed version, or run one as a service other people use over a network, you must make your source code available under the same license.
