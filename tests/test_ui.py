"""M6 acceptance: the review UI in a real browser (Playwright).

Needs the built UI (`cd ui && npm install && npm run build`) and Chrome or
Playwright's Chromium; skipped otherwise.
"""

import json
import shutil
import socket
import threading
import time

import pymupdf
import pytest

from taxonymous.api import UI_DIR, create_app
from taxonymous.pipeline import Workspace

pytestmark = pytest.mark.skipif(not (UI_DIR / "index.html").exists(), reason="UI not built")
sync_api = pytest.importorskip("playwright.sync_api")

TOKEN = "ui-test-token"
FILES = ["k1_statement.pdf", "w2.pdf"]
AMOUNT_TYPE = "AMOUNT"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def site(corpus, tmp_path_factory):
    import uvicorn
    src, manifest, profile = corpus
    ws = Workspace(tmp_path_factory.mktemp("uiws"))
    ws.init()
    ws.profile_path.write_text(json.dumps(profile))
    for name in FILES:
        shutil.copyfile(src / name, ws.inbox / name)
    app = create_app(ws, TOKEN)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    docs = {d["file"]: d for d in manifest["documents"]}
    yield f"http://127.0.0.1:{port}", app, ws, src, docs
    server.should_exit = True


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        for launch in (lambda: p.chromium.launch(channel="chrome"), lambda: p.chromium.launch()):
            try:
                b = launch()
                break
            except Exception:
                continue
        else:
            pytest.skip("no Chrome or Playwright Chromium")
        yield b
        b.close()


@pytest.fixture
def page(site, browser):
    url, app, *_ = site
    pg = browser.new_page(viewport={"width": 1500, "height": 950}, base_url=url)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{url}/?token={TOKEN}")
    yield pg
    pg.close()
    assert not errors


def open_doc(pg, app, name: str) -> None:
    docs = pg.request.get("/api/documents", headers={"X-Taxonymous-Token": TOKEN}).json()
    if any(d["status"] == "new" for d in docs):
        pg.get_by_role("button", name="Run all").click()
        time.sleep(0.3)
        app.state.worker.wait()
    pg.get_by_role("button", name=name).click()
    pg.wait_for_function("document.querySelectorAll('[data-testid=pane-redacted] .region').length > 0")
    pg.wait_for_function("[...document.querySelectorAll('[data-testid=pane-redacted] img')]"
                         ".slice(0, 1).every(i => i.complete && i.naturalWidth > 0)")


def wait_banner(pg, text: str) -> None:
    pg.wait_for_function(f"document.querySelector('[data-testid=banner]')?.innerText.includes({text!r})",
                         timeout=60_000)


def wait_status(pg, status: str) -> None:
    js = f"document.querySelector('[data-testid=status]')?.innerText.toLowerCase() === {status!r}"
    pg.wait_for_function(js, timeout=60_000)


def scroll_top(pg, side: str) -> float:
    return pg.eval_on_selector(f"[data-testid=pane-{side}]", "el => el.scrollTop")


def test_the_token_is_taken_out_of_the_address_bar(page):
    assert "token" not in page.url


def test_scrolling_and_paging_are_mirrored(page, site):
    _, app, *_ = site
    open_doc(page, app, "k1_statement.pdf")
    page.eval_on_selector("[data-testid=pane-original]", "el => el.scrollTop = 500")
    page.wait_for_timeout(200)
    assert scroll_top(page, "redacted") == scroll_top(page, "original") > 0
    page.eval_on_selector("[data-testid=pane-redacted]", "el => el.scrollTop = 40")
    page.wait_for_timeout(200)
    assert scroll_top(page, "original") == 40
    page.get_by_role("button", name="›").click()
    page.wait_for_timeout(200)
    assert page.inner_text("[data-testid=page]") == "2 / 2"
    assert scroll_top(page, "original") == scroll_top(page, "redacted") > 40
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(200)
    assert page.inner_text("[data-testid=page]") == "1 / 2" and scroll_top(page, "redacted") == 0


def test_mark_final_scrub_preview_and_export(page, site):
    """The plan's acceptance path: mark an area, scrub, see it verified, export."""
    _, app, ws, src, docs = site
    open_doc(page, app, "k1_statement.pdf")
    amount = next(i["value"] for i in docs["k1_statement.pdf"]["keep"] if i["type"] == AMOUNT_TYPE)
    [rect] = pymupdf.open(src / "k1_statement.pdf")[0].search_for(amount)
    pdf_width = pymupdf.open(src / "k1_statement.pdf")[0].rect.width

    page.get_by_role("button", name="Mark areas").click()
    target = page.locator("[data-testid=pane-original] .page").nth(0)
    bb = target.bounding_box()
    zoom = bb["width"] / pdf_width
    page.mouse.move(bb["x"] + (rect.x0 - 1) * zoom, bb["y"] + (rect.y0 - 1) * zoom)
    page.mouse.down()
    page.mouse.move(bb["x"] + (rect.x1 + 1) * zoom, bb["y"] + (rect.y1 + 1) * zoom, steps=6)
    page.mouse.up()
    assert page.locator("[data-testid=pane-original] [data-testid=mark]").count() == 1
    assert page.locator("[data-testid=pane-redacted] [data-testid=mark]").count() == 1  # mirrored
    assert page.get_by_role("button", name="Export", exact=True).is_disabled()  # marks must be applied first

    page.get_by_role("button", name="Final scrub (1)").click()
    wait_banner(page, "Final scrub done")
    page.locator(".scrubbed").wait_for()  # the preview outlines what was just removed, once it reloads
    assert page.locator(".scrubbed").count() == 1
    assert amount not in pymupdf.open(ws.outputs("k1_statement")["pdf"])[0].get_text()

    page.get_by_role("button", name="Export", exact=True).click()
    wait_banner(page, "Exported")
    assert (ws.ready / "k1_statement.redacted.pdf").exists()
    released = pymupdf.open(ws.ready / "k1_statement.redacted.pdf")[0].get_text()
    assert amount not in released and "<REDACTED_" in released


def test_a_failed_verification_blocks_export(page, site):
    _, app, ws, src, _ = site
    open_doc(page, app, "w2.pdf")
    shutil.copyfile(src / "w2.pdf", ws.outputs("w2")["pdf"])  # the output is swapped for the original
    page.get_by_role("button", name="Re-verify").click()
    wait_status(page, "failed")
    assert page.get_by_role("button", name="Export", exact=True).is_disabled()
    assert "failed" in page.inner_text("[data-testid=banner]").lower()
    assert not (ws.ready / "w2.redacted.pdf").exists()
    # running it again from the review screen repairs it
    page.get_by_role("button", name="Run again").click()
    app.state.worker.wait()
    wait_status(page, "passed")
    assert page.get_by_role("button", name="Export", exact=True).is_enabled()


def _repeated_region(pg):
    """The first SSN box on the redacted side whose placeholder has other boxes too (an SSN:
    one line, where a name can wrap onto two)."""
    boxes = "[data-testid=pane-redacted] .region:not(.shown)"
    titles = pg.eval_on_selector_all(boxes, "els => els.map(e => e.title)")
    placeholders = [t.split(" · ")[0] for t in titles]
    i = next(i for i, ph in enumerate(placeholders) if ph.startswith("<SSN_") and placeholders.count(ph) > 1)
    return pg.locator("[data-testid=pane-redacted] .region:not(.shown)").nth(i), placeholders[i]


def regions_of(pg, placeholder: str) -> int:
    return pg.locator(f"[data-testid=pane-redacted] .region[title^='{placeholder} · ']:not(.shown)").count()


def test_clicking_a_box_can_leave_it_readable_everywhere(page, site):
    _, app, *_ = site
    open_doc(page, app, "w2.pdf")
    region, placeholder = _repeated_region(page)
    region.click()
    page.get_by_role("button", name="Show it everywhere in this document").click()
    page.wait_for_selector(f".kept .chip:has-text('{placeholder} everywhere')", timeout=60_000)
    app.state.worker.wait()
    wait_status(page, "passed")
    assert regions_of(page, placeholder) == 0
    page.get_by_role("button", name="redact again").click()
    page.wait_for_selector(".kept", state="detached", timeout=60_000)
    app.state.worker.wait()  # the re-run, before the next test looks at the documents
    wait_status(page, "passed")


def test_clicking_a_box_can_leave_just_that_box_readable(page, site):
    _, app, *_ = site
    open_doc(page, app, "w2.pdf")
    region, placeholder = _repeated_region(page)
    before = regions_of(page, placeholder)
    region.click()
    page.get_by_role("button", name="Show this box only").click()
    page.wait_for_selector("[data-testid=pane-redacted] [data-testid=shown]", timeout=60_000)
    app.state.worker.wait()
    wait_status(page, "passed")
    assert regions_of(page, placeholder) == before - 1  # the other copies are still redacted
    assert page.locator(f".kept .chip:has-text('{placeholder} one box')").count() == 1
    page.locator("[data-testid=pane-redacted] [data-testid=shown]").click()
    page.get_by_role("button", name="Redact this box again").click()
    page.wait_for_selector("[data-testid=shown]", state="detached", timeout=60_000)
    app.state.worker.wait()
    wait_status(page, "passed")
    assert regions_of(page, placeholder) == before


@pytest.fixture
def fresh_page(browser, tmp_path):
    """A first run: an empty workspace, no details yet."""
    import uvicorn
    ws = Workspace(tmp_path / "fresh")
    app = create_app(ws, TOKEN, ocr=False)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    pg = browser.new_page(viewport={"width": 1500, "height": 950}, base_url=f"http://127.0.0.1:{port}")
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"/?token={TOKEN}")
    yield pg, ws
    pg.close()
    server.should_exit = True
    assert not errors


def test_first_run_asks_for_your_details_in_a_form(fresh_page):
    pg, ws = fresh_page
    pg.get_by_role("heading", name="Your details").wait_for()
    assert pg.get_by_role("button", name="Run all").is_disabled()

    person = pg.get_by_test_id("person").first
    person.get_by_label("Full name").fill("Avery F Abernathy")
    person.get_by_label("SSN or ITIN").fill("987-65-432")  # a digit short
    person.get_by_label("Date of birth").fill("04/07/1984")
    person.get_by_label("Occupation").fill("Software engineer")
    person.get_by_label("Employer").fill("Contoso Widgets LLC")
    pg.get_by_role("button", name="Add a person").click()
    spouse = pg.get_by_test_id("person").nth(1)
    spouse.get_by_label("Full name").fill("Quinn L Abernathy")
    spouse.get_by_label("Who").select_option("spouse")
    address = pg.get_by_test_id("address").first
    address.get_by_label("Street").fill("5842 Fenwick Ave")
    address.get_by_label("City").fill("Tacoma")
    address.get_by_label("State").fill("WA")
    address.get_by_label("ZIP").fill("98458")
    assert spouse.get_by_label("Date of death").count() == 0
    spouse.get_by_label("Deceased").check()
    spouse.get_by_label("Date of death").fill("3/14/2025")
    pg.get_by_role("button", name="Add an account").click()
    account = pg.get_by_test_id("record-accounts").first
    account.get_by_label("Institution").fill("Contoso Bank")
    account.get_by_label("Account number").fill("8320968305")
    account.get_by_label("Account name").fill("Joint checking")
    account.get_by_label("Routing number").fill("021000021")
    pg.get_by_role("button", name="Add a number").click()
    pg.get_by_test_id("record-numbers").first.get_by_label("Number or ID").fill("54321")
    pg.get_by_role("button", name="Add a preparer").click()
    preparer = pg.get_by_test_id("record-preparers").first
    preparer.get_by_label("Preparer's name").fill("Jordan Q Okafor")
    preparer.get_by_label("PTIN").fill("P00012345")

    pg.get_by_role("button", name="Save").click()
    pg.get_by_text("An SSN or ITIN has 9 digits.").wait_for()
    assert not ws.profile_path.exists()

    person.get_by_label("SSN or ITIN").fill("987654323")
    pg.get_by_role("button", name="Save").click()
    pg.wait_for_function("document.querySelector('[data-testid=details-message]')?.innerText === 'Saved.'")
    saved = json.loads(ws.profile_path.read_text())
    assert [p["role"] for p in saved["people"]] == ["taxpayer", "spouse"]
    assert saved["people"][0]["ssn"] == "987-65-4323"
    assert saved["people"][0]["occupation"] == "Software engineer"
    assert saved["people"][0]["employer"] == "Contoso Widgets LLC"
    for width in (1500, 1280, 900):  # the form fits the window: no scrolling sideways
        pg.set_viewport_size({"width": width, "height": 950})
        pg.screenshot(path=str(ws.root / f"details-{width}.png"))
        assert pg.evaluate("[...document.querySelectorAll('.details, .details *')]"
                           ".every(e => e.scrollWidth <= e.clientWidth + 1)"), width
    pg.set_viewport_size({"width": 1500, "height": 950})
    assert saved["accounts"] == [{"institution": "Contoso Bank", "number": "8320968305",
                                  "routing": "021000021", "name": "Joint checking"}]
    assert saved["numbers"] == [{"number": "54321", "description": ""}]
    assert saved["preparers"][0]["name"] == "Jordan Q Okafor" and saved["preparers"][0]["ptin"] == "P00012345"
    assert saved["people"][1]["deceased"] is True and saved["people"][1]["dod"] == "03/14/2025"
    assert pg.locator(".badge.needed").count() == 0

    pg.get_by_label("hide numbers on screen").check()
    assert "secret" in person.get_by_label("SSN or ITIN").get_attribute("class")


def test_a_run_shows_how_far_it_has_got(page, site, monkeypatch, tmp_path):
    """The bar follows what the engine reports: held part-way here, so it can be looked at."""
    from taxonymous import api as api_mod
    _, app, *_ = site
    open_doc(page, app, "k1_statement.pdf")  # first in the list, so it runs first
    go = threading.Event()

    def slow(src, ws, matcher, vault, ocr=True, release=True, progress=None):
        progress.start("checking", 4)
        progress.step(3)
        assert go.wait(30)

    monkeypatch.setattr(api_mod, "process", slow)
    try:
        page.get_by_role("button", name="Run all").click()
        page.wait_for_function("document.querySelector('[data-testid=banner] [data-testid=progress]')"
                               "?.innerText.includes('page 3 of 4')", timeout=30_000)
        text = page.inner_text("[data-testid=banner] [data-testid=progress]")
        assert "Document" in text and "of 2" in text and "Checking the redacted pages" in text
        assert page.locator("[data-testid=documents] [data-testid=progress]").count() == 1
        bar = page.get_by_role("progressbar").first
        assert bar.get_attribute("aria-valuenow") == "3" and bar.get_attribute("aria-valuemax") == "4"
        page.screenshot(path=str(tmp_path / "progress.png"))
    finally:
        go.set()
        app.state.worker.wait()
    page.wait_for_function("!document.querySelector('[data-testid=progress]')", timeout=30_000)


def test_export_several_at_once(page, site):
    """Pick documents in the sidebar (or All passed) and export them in one go: only the
    redacted PDFs reach ready-to-upload/."""
    _, app, ws, *_ = site
    open_doc(page, app, "w2.pdf")
    docs = page.request.get("/api/documents", headers={"X-Taxonymous-Token": TOKEN}).json()
    passed = [d for d in docs if d["status"] == "passed"]
    assert passed
    for p in ws.ready.iterdir():
        p.unlink()
    page.get_by_label("All passed").check()
    page.get_by_role("button", name=f"Export selected ({len(passed)})").click()
    page.wait_for_selector(".side-ok", timeout=60_000)
    assert sorted(p.name for p in ws.ready.iterdir()) == sorted(f"{d['id']}.redacted.pdf" for d in passed)
    assert page.get_by_role("button", name="Export selected (0)").is_disabled()
    failed = [d for d in docs if d["status"] != "passed"]
    for d in failed:  # a document that didn't pass can't be picked
        assert page.get_by_label(f"Pick {d['name']} for export").is_disabled()
    page.screenshot(path=str(ws.root / "export-selected.png"))


def test_something_left_readable_is_added_to_your_details_and_redacted(fresh_page):
    pg, ws = fresh_page
    ws.profile_path.write_text(json.dumps({"people": [{"name": "Avery F Abernathy"}]}))
    doc = pymupdf.open()
    w2 = doc.new_page()
    lines = ["Employee: Avery F Abernathy", "Spouse: Morgan Delacroix", "Wages 1,234.56"]
    for i, line in enumerate(lines):
        w2.insert_text((72, 90 + 20 * i), line, fontsize=10)
    doc.save(ws.inbox / "w2.pdf")
    pg.reload()
    pg.get_by_role("button", name="Run all").click()
    pg.get_by_role("button", name="w2.pdf").click()
    wait_status(pg, "passed")
    text = lambda: pg.request.get("/api/documents/w2/text", headers={"X-Taxonymous-Token": TOKEN}).text()  # noqa: E731
    assert "Morgan Delacroix" in text()  # not in your details: left readable

    pg.get_by_role("button", name="Add to my details").click()
    form = pg.get_by_test_id("add-detail")
    form.get_by_label("Kind").select_option("person")
    form.get_by_label("Full name").fill("Morgan")  # one word: not a full name
    form.get_by_label("Who").select_option("spouse")
    form.get_by_role("button", name="Add and run again").click()
    form.get_by_text("Enter a first and last name.").wait_for()
    form.get_by_label("Full name").fill("Morgan Delacroix")
    form.get_by_role("button", name="Add and run again").click()
    wait_banner(pg, "Added to your details")
    pg.wait_for_function("!document.querySelector('[data-testid=add-detail]')")
    wait_status(pg, "passed")
    pg.wait_for_function("document.querySelectorAll('[data-testid=pane-redacted] .region').length >= 2",
                         timeout=60_000)
    assert "Morgan" not in text()
    pg.get_by_test_id("details-card").get_by_text("2 people").wait_for()  # the sidebar card is updated
    added = json.loads(ws.profile_path.read_text())["people"][-1]
    assert added["name"] == "Morgan Delacroix" and added["role"] == "spouse"

    pg.get_by_role("button", name="Add to my details").click()  # the fields follow the kind
    form.get_by_label("Kind").select_option("account")
    assert form.get_by_label("Institution").count() == form.get_by_label("Account name").count() == 1
