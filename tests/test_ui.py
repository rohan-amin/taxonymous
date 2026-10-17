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
    assert page.locator(".scrubbed").count() == 1  # the preview outlines what was just removed
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
    # running it again repairs it
    page.request.post("/api/documents/w2/run", headers={"X-Taxonymous-Token": TOKEN})
    app.state.worker.wait()
    wait_status(page, "passed")
    assert page.get_by_role("button", name="Export", exact=True).is_enabled()


def test_clicking_a_box_can_leave_it_readable_here(page, site):
    _, app, *_ = site
    open_doc(page, app, "k1_statement.pdf")
    region = page.locator("[data-testid=pane-redacted] .region").first
    placeholder = region.get_attribute("title").split(" · ")[0]
    region.click()
    page.get_by_role("button", name="Don't redact this here").click()
    page.wait_for_selector(f".kept .chip:has-text('{placeholder}')", timeout=60_000)
    app.state.worker.wait()
    page.get_by_role("button", name="redact again").click()
    page.wait_for_selector(".kept", state="detached", timeout=60_000)
