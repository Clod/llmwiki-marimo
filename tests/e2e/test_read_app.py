"""
E2E test for marimo/read_app.py (read-only viewer + FTS5 chat).

Prerequisites:
    1. Run test_ingest_app.py first to populate tests/fixtures/workspace/
    2. uv run playwright install chromium (once)

Run both suites in order:
    cd /path/to/llmwiki
    uv run pytest tests/e2e/test_ingest_app.py tests/e2e/test_read_app.py -v -s

Run read tests only (requires workspace already populated):
    uv run pytest tests/e2e/test_read_app.py -v -s

Headless mode:
    HEADLESS=1 uv run pytest tests/e2e/test_read_app.py -v -s
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, Browser, Page

# ── Constants ─────────────────────────────────────────────────────────────────

PROJECT_ROOT  = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR  = PROJECT_ROOT / "tests" / "fixtures"
WORKSPACE     = FIXTURES_DIR / "workspace"
TEST_PORT     = 2720


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wait_for_port(host: str, port: int, timeout: float = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def read_app_server() -> str:
    """Start read_app.py against the test workspace; skip if not yet ingested."""
    wiki_dir = WORKSPACE / "wiki"
    if not wiki_dir.exists() or not list(wiki_dir.glob("*.md")):
        pytest.skip(
            "No wiki pages found in tests/fixtures/workspace/wiki/. "
            "Run test_ingest_app.py first."
        )

    config_src = FIXTURES_DIR / "wiki_config.toml"
    config_dst = WORKSPACE / "wiki_config.toml"
    if config_src.exists() and not config_dst.exists():
        shutil.copy(config_src, config_dst)

    marimo_bin = Path(sys.executable).parent / "marimo"
    proc = subprocess.Popen(
        [
            str(marimo_bin), "run",
            "marimo/read_app.py",
            "--port", str(TEST_PORT),
            "--headless",
            "--no-token",
            "--no-sandbox",
        ],
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "WIKI_PATH": str(WORKSPACE)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def _reader():
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, ""):
            print(f"[read_app] {line.rstrip()}")

    threading.Thread(target=_reader, daemon=True).start()

    if not _wait_for_port("localhost", TEST_PORT, timeout=60):
        if proc.poll() is not None:
            pytest.fail("read_app server process died; check output above")
        pytest.fail(f"read_app not reachable on port {TEST_PORT} after 60s")

    time.sleep(1.5)
    url = f"http://localhost:{TEST_PORT}"
    print(f"\n[read_app] Server ready at {url}\n")
    yield url

    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="session")
async def browser_session(read_app_server: str) -> Browser:
    headless = os.environ.get("HEADLESS", "0") == "1"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            slow_mo=0 if headless else 300,
        )
        yield browser
        await browser.close()


@pytest.fixture(scope="session")
async def page(browser_session: Browser, read_app_server: str) -> Page:
    p = await browser_session.new_page(viewport={"width": 1440, "height": 900})
    await p.goto(read_app_server, wait_until="networkidle", timeout=30_000)
    await p.wait_for_timeout(2_000)
    yield p
    await p.close()


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_app_loads_with_pages(page: Page) -> None:
    """App renders; left panel table contains ingested wiki pages."""
    table = page.locator("table").first
    await table.wait_for(state="visible", timeout=10_000)

    rows = page.locator("table tbody tr")
    count = await rows.count()
    assert count > 0, "Expected at least one wiki page in the left-panel table"
    print(f"\n   Wiki pages in table: {count}")


async def test_select_page_loads_content(page: Page) -> None:
    """Clicking a table row loads the page content in the middle panel."""
    await page.locator("table tbody tr").first.click()
    await page.wait_for_timeout(800)

    heading = page.locator("h2").first
    await heading.wait_for(state="visible", timeout=5_000)
    title = (await heading.inner_text()).strip()
    assert title, "Expected a page title (h2) in the middle panel"
    print(f"\n   Middle panel showing: {title}")


async def test_refresh_reloads_page_list(page: Page) -> None:
    """Refresh button reloads the page list without errors."""
    initial_count = await page.locator("table tbody tr").count()

    refresh_btn = page.get_by_role("button", name="Refresh", exact=False)
    await refresh_btn.click()
    await page.wait_for_timeout(1_000)

    await page.locator("table").first.wait_for(state="visible", timeout=5_000)
    after_count = await page.locator("table tbody tr").count()
    assert after_count == initial_count, \
        f"Page count changed after refresh: {initial_count} → {after_count}"


async def test_no_edit_controls_present(page: Page) -> None:
    """No inline page-editing controls — the read app never edits existing wiki
    pages in place. The '💾 Save to wiki' button is intentional (the save-to-wiki
    feature, §6.8) and is not an edit control, so it is not forbidden here."""
    for label in ["Edit", "Cancel", "Create"]:
        btn = page.get_by_role("button", name=label, exact=False)
        assert not await btn.is_visible(), \
            f"'{label}' edit control should not be present in the read app"


async def test_chat_panel_renders(page: Page) -> None:
    """Chat panel heading visible; prompts loaded from wiki_config.toml."""
    # Target the heading specifically: in the column layout marimo also renders a
    # heading-navigation entry with the same text, so a plain get_by_text matches
    # two elements (strict-mode violation).
    heading = page.get_by_role("heading", name="Chat with your Wiki")
    await heading.wait_for(state="visible", timeout=5_000)

    chatbot = page.locator("marimo-chatbot").first
    await chatbot.wait_for(state="attached", timeout=5_000)
    prompts_raw = await chatbot.get_attribute("data-prompts")
    prompts = json.loads(prompts_raw or "[]")

    assert len(prompts) > 0, "No prompts in marimo-chatbot data-prompts"
    print(f"\n   Chat prompts: {prompts}")
