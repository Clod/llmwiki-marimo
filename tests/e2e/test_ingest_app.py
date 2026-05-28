"""
E2E test: ingest three PDFs through the marimo ingest app one at a time,
then verify all three are ready in SQLite with linked wiki pages.

Prerequisites (run once):
    uv run playwright install chromium

Run (headed, verbose):
    cd /path/to/llmwiki
    uv run pytest tests/e2e/test_ingest_app.py -v -s

PDFs must be placed in tests/fixtures/pdfs/ before running.
The ingested wiki lands in tests/fixtures/workspace/ (gitignored).
"""

import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, Browser, Page

# ── Constants ─────────────────────────────────────────────────────────────────

PROJECT_ROOT     = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR     = PROJECT_ROOT / "tests" / "fixtures"
WORKSPACE        = FIXTURES_DIR / "workspace"
PDFS_SRC         = FIXTURES_DIR / "pdfs"
DB_PATH          = WORKSPACE / ".llmwiki" / "index.db"
TEST_PORT        = 2719
PDFS             = ["Cinderella.pdf", "Little Red Riding Hood.pdf", "Snow White and the Seven Dwarfs.pdf"]
INGEST_TIMEOUT_S = 300           # 5 min per file (includes LLM wiki-page generation)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _wait_for_port(host: str, port: int, timeout: float = 60) -> bool:
    """Return True once the port accepts a TCP connection, False if timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _start_output_reader(proc: subprocess.Popen) -> list[str]:
    """Drain proc.stdout in a daemon thread; return the shared list of URLs found."""
    found_urls: list[str] = []

    def _reader():
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, ""):
            print(f"[marimo] {raw.rstrip()}")
            m = re.search(r'(https?://(?:localhost|127\.0\.0\.1)\S*)', raw)
            if m:
                found_urls.append(m.group(1).rstrip("/."))

    threading.Thread(target=_reader, daemon=True).start()
    return found_urls


@pytest.fixture(scope="session")
def marimo_server() -> str:
    """Wipe workspace, start a marimo server on TEST_PORT, yield the app URL."""
    missing = [p for p in PDFS if not (PDFS_SRC / p).exists()]
    if missing:
        pytest.fail(
            f"Missing test PDFs in {PDFS_SRC}: {missing}\n"
            f"Copy the PDFs into tests/fixtures/pdfs/ before running."
        )

    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    config_src = FIXTURES_DIR / "wiki_config.toml"
    if config_src.exists():
        shutil.copy(config_src, WORKSPACE / "wiki_config.toml")

    marimo_bin = Path(sys.executable).parent / "marimo"
    proc = subprocess.Popen(
        [
            str(marimo_bin), "run",
            "marimo/ingest_app.py",
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

    found_urls = _start_output_reader(proc)

    if not _wait_for_port("localhost", TEST_PORT, timeout=60):
        if proc.poll() is not None:
            pytest.fail("marimo server process died; check [marimo] output above")
        pytest.fail(f"marimo not reachable on port {TEST_PORT} after 60 s")

    time.sleep(0.5)
    app_url = found_urls[0] if found_urls else f"http://localhost:{TEST_PORT}"

    print(f"\n[marimo] Server ready at {app_url}\n")
    yield app_url

    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="session")
async def browser_session(marimo_server: str) -> Browser:
    """Chromium browser. Headless when HEADLESS=1, headed otherwise."""
    headless = os.environ.get("HEADLESS", "0") == "1"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless, slow_mo=0 if headless else 400)
        yield browser
        await browser.close()


@pytest.fixture(scope="session")
async def page(browser_session: Browser, marimo_server: str) -> Page:
    p = await browser_session.new_page(viewport={"width": 1440, "height": 900})
    await p.goto(marimo_server, wait_until="networkidle", timeout=30_000)
    yield p
    await p.close()


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def wait_for_ingestion(filename: str, timeout_s: int = INGEST_TIMEOUT_S) -> None:
    """Poll the DB every 3 s until the source doc is 'ready' or 'failed'."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if DB_PATH.exists():
            conn = _db()
            row = conn.execute(
                "SELECT status, error_message FROM documents "
                "WHERE filename=? AND source_kind='source'",
                (filename,),
            ).fetchone()
            conn.close()
            if row:
                if row["status"] == "ready":
                    return
                if row["status"] == "failed":
                    pytest.fail(
                        f"'{filename}' ingestion FAILED: {row['error_message']}"
                    )
        time.sleep(3)
    pytest.fail(f"Timeout ({timeout_s} s) — '{filename}' never became ready")


def wait_for_wiki_page(source_doc_id: str, filename: str, timeout_s: int = INGEST_TIMEOUT_S) -> None:
    """Poll until a wiki summary page linked to source_doc_id appears in the DB.

    status='ready' is set at Step 6 (chunking), before the LLM wiki generation
    at Steps 7-9. This separate poll waits for the wiki page that the LLM creates.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if DB_PATH.exists():
            conn = _db()
            row = conn.execute(
                "SELECT id FROM documents "
                "WHERE source_kind='wiki' AND source_document_id=?",
                (source_doc_id,),
            ).fetchone()
            conn.close()
            if row:
                return
        time.sleep(3)
    pytest.fail(f"Timeout ({timeout_s} s) — no wiki page created for '{filename}'")


def assert_source_ok(filename: str) -> dict:
    conn = _db()
    row = conn.execute(
        "SELECT * FROM documents WHERE filename=? AND source_kind='source'",
        (filename,),
    ).fetchone()
    conn.close()

    assert row is not None,            f"No source row in DB for '{filename}'"
    assert row["status"] == "ready",   f"'{filename}' status={row['status']}"
    assert row["page_count"] > 0,      f"'{filename}' page_count={row['page_count']}"
    assert row["content"] is not None, f"'{filename}' content is NULL"
    return dict(row)


def assert_wiki_ok(source_doc_id: str, source_filename: str) -> None:
    conn = _db()
    wiki = conn.execute(
        "SELECT filename FROM documents "
        "WHERE source_kind='wiki' AND source_document_id=?",
        (source_doc_id,),
    ).fetchone()
    conn.close()
    assert wiki, f"No wiki page linked to source doc for '{source_filename}'"
    print(f"   📄 wiki page: {wiki['filename']}")


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pdf_name", PDFS)
async def test_ingest_pdf(page: Page, pdf_name: str) -> None:
    """Upload one PDF, click Ingest, poll DB until ready, assert DB state."""

    print(f"\n{'─'*60}")
    print(f"📂 Ingesting: {pdf_name}")

    await page.reload(wait_until="networkidle", timeout=30_000)
    await page.set_input_files("input[type='file']", str(PDFS_SRC / pdf_name))
    await page.wait_for_timeout(1_500)

    await page.get_by_text("Ingest uploaded file", exact=False).click()
    await page.wait_for_timeout(800)

    print("⏳ Waiting for source to be indexed…")
    wait_for_ingestion(pdf_name)
    src = assert_source_ok(pdf_name)

    print("⏳ Waiting for wiki page to be generated (LLM)…")
    wait_for_wiki_page(src["id"], pdf_name)
    assert_wiki_ok(src["id"], pdf_name)
    print(
        f"✅ {pdf_name} → ready "
        f"(pages={src['page_count']}, size={src['file_size']} B)"
    )


def test_final_db_state() -> None:
    """Verify DB totals after all three ingestions."""
    conn = _db()
    src_ready = conn.execute(
        "SELECT COUNT(*) FROM documents "
        "WHERE source_kind='source' AND status='ready'"
    ).fetchone()[0]
    wiki_pages = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE source_kind='wiki'"
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT filename, status, page_count FROM documents "
        "WHERE source_kind='source' ORDER BY filename"
    ).fetchall()
    conn.close()

    print("\n📊 Final DB state:")
    for r in rows:
        print(f"   {r['filename']}: {r['status']}  ({r['page_count']} pages)")
    print(f"   Wiki pages total: {wiki_pages}")

    assert src_ready == 3,  f"Expected 3 ready source docs, got {src_ready}"
    assert wiki_pages >= 3, f"Expected ≥3 wiki pages, got {wiki_pages}"
