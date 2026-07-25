"""
E2E test (v2) for marimo/ingest_app.py — covers what test_ingest_app.py misses:
the wiki picker, the ingest form (button + full-LLM-lint checkbox bundled
atomically via mo.ui.form), the streaming Activity Log, the ingest-time
vocabulary subsystem (generated aliases + the vocabulary/thin_page lint
checks), source deletion, and the wiki-wide lint & repair sweep.

`test_ingest_app.py` is left untouched — different workspace (workspace_e2e/,
not workspace/) and a different port (2721, not 2719), so the two suites can
coexist and even run in the same pytest session without fighting over state.

The golden rule for what gets asserted here: assert on what the CODE decides,
never on what the MODEL writes. Page titles, generated aliases and prose vary
run to run; the pipeline's control flow (statuses, DB rows, deterministic log
markers, file existence) does not.

Not in CI — the workflow only runs `pytest tests/unit tests/regression`. This
suite needs a live marimo server, Chromium, and a configured LLM; it is a
manual/local gate.

Prerequisites (run once):
    uv run playwright install chromium
    a configured .env (LLM_BASE_URL / LLM_API_KEY / LLM_MODEL, or the
    WIKI_LLM_* overrides ingest_app.py falls back through) — otherwise the
    whole suite is SKIPPED (not failed) at the server fixture.

Run (headed, verbose):
    cd /path/to/llmwiki
    uv run pytest tests/e2e/test_ingest_app_v2.py -v -s

Headless:
    HEADLESS=1 uv run pytest tests/e2e/test_ingest_app_v2.py -v -s

Opt-in destructive/expensive tests (off by default, gated by env flag):
    E2E_DESTRUCTIVE=1  runs test_delete_source_removes_derived_pages, which
                       deletes a source and its derived wiki pages — must run
                       last, since later tests rely on the ingested state.
    E2E_FULL=1         runs test_wiki_wide_lint_and_repair, a full LLM sweep
                       (contradictions, data gaps, LLM-backed repairs) —
                       spends tokens and can take minutes.

PDFs must already be in tests/fixtures/pdfs/. The ingested wiki lands in
tests/fixtures/workspace_e2e/ (gitignored).
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
import tomllib
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, Browser, Page

# tests/conftest.py puts `base/` on sys.path, so this matches how the marimo
# apps themselves resolve config — see base/config.py and ingest_app.py's
# setup() cell.
from config import settings
from domain.wiki_registry import short_label

# ── Constants ─────────────────────────────────────────────────────────────────

PROJECT_ROOT     = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR     = PROJECT_ROOT / "tests" / "fixtures"
WORKSPACE        = FIXTURES_DIR / "workspace_e2e"
PDFS_SRC         = FIXTURES_DIR / "pdfs"
DB_PATH          = WORKSPACE / ".llmwiki" / "index.db"
ALIASES_PATH     = WORKSPACE / ".llmwiki" / "aliases.generated.toml"
TEST_PORT        = 2721                      # 2718-2720 are taken by dev apps + the old e2e tests
# Two files is enough to exercise cross-document linking; a third only adds
# ~2 min of LLM time for no extra coverage.
PDFS             = ["Cinderella.pdf", "Little Red Riding Hood.pdf"]
INGEST_TIMEOUT_S = 300           # 5 min per file (includes LLM wiki-page generation)

# ingest_app.py's setup() cell falls back WIKI_LLM_* -> LLM_* for the wiki-
# generation client; mirror that precedence so the skip check matches what the
# app itself would actually require.
_WIKI_LLM_BASE_URL = settings.WIKI_LLM_BASE_URL or settings.LLM_BASE_URL
_WIKI_LLM_API_KEY  = settings.WIKI_LLM_API_KEY  or settings.LLM_API_KEY
_WIKI_LLM_MODEL    = settings.WIKI_LLM_MODEL    or settings.LLM_MODEL
_LLM_CONFIGURED = bool(
    _WIKI_LLM_BASE_URL.strip() and _WIKI_LLM_API_KEY.strip() and _WIKI_LLM_MODEL.strip()
)


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
    """Wipe workspace_e2e/, start a marimo server on TEST_PORT, yield the app URL.

    Missing PDFs is a real setup error (fail). A missing LLM config is not a
    setup mistake the test author can fix — skip honestly instead of letting
    the server die mid-ingest with a confusing traceback.
    """
    if not _LLM_CONFIGURED:
        pytest.skip(
            "E2E needs a live LLM — set LLM_BASE_URL/LLM_API_KEY/LLM_MODEL "
            "(or the WIKI_LLM_* overrides) in .env."
        )

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
    """Chromium browser. Headless when HEADLESS=1, headed otherwise.

    `marimo_server` is unused in the body but must stay: it orders the fixtures so
    the server (and its missing-LLM skip) resolves before Chromium launches.
    """
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


# ── DOM helpers ───────────────────────────────────────────────────────────────

async def _activity_log_text(page: Page) -> str:
    """Inner text of the Activity Log's scroll container.

    `activity_log` (see marimo/ingest_app.py) builds a raw `mo.Html` div with an
    inline `flex-direction:column-reverse` style so the newest line stays in
    view — that inline style is unique enough on the page to target reliably
    without an id/data-testid. It is plain DOM (mo.md text), NOT shadow DOM
    like the chat widget in read_app, so a normal inner_text() already sees
    everything — no shadow-piercing walk needed here.
    """
    container = page.locator("div[style*='column-reverse']").first
    return await container.inner_text()


async def _wait_for_log_contains(page: Page, needle: str, timeout_s: float = 30) -> str:
    """Poll the Activity Log until it contains `needle`; return the full text.

    Background ops stream lines in over time (mo.Thread + the 1s auto-refresh
    ticker), so a single read right after triggering an action can race an
    in-flight step. Polling for a specific marker is the deterministic way to
    know a given step has actually happened.
    """
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        last = await _activity_log_text(page)
        if needle in last:
            return last
        await page.wait_for_timeout(1_000)
    pytest.fail(f"Timeout ({timeout_s} s) waiting for {needle!r} in Activity Log.\n\nLast seen:\n{last}")


async def _click_button_pierce_shadow(page: Page, text: str, timeout_s: float = 5) -> None:
    """Click a button by its exact visible text, in light DOM or an open shadow root.

    DeleteConfirmWidget (used for both source-delete and the wiki-wide lint &
    repair confirm) renders inside an `mo.ui.anywidget`. Playwright locators
    already pierce *open* shadow roots for query/click, so `get_by_role`
    usually reaches the widget's JS-rendered buttons directly. The manual
    walk below is only a documented fallback in case a future marimo/anywidget
    version wraps the mount point in a way that path doesn't reach — so this
    doesn't need to be rediscovered blind if a version bump ever breaks it.
    """
    locator = page.get_by_role("button", name=text, exact=True)
    try:
        await locator.first.wait_for(state="visible", timeout=timeout_s * 1_000)
        await locator.first.click()
        return
    except Exception:
        pass

    clicked = await page.evaluate(
        """(text) => {
            function search(root) {
                for (const b of root.querySelectorAll('button')) {
                    if (b.textContent.trim() === text) return b;
                }
                for (const node of root.querySelectorAll('*')) {
                    if (node.shadowRoot) {
                        const inner = search(node.shadowRoot);
                        if (inner) return inner;
                    }
                }
                return null;
            }
            const el = search(document);
            if (el) { el.click(); return true; }
            return false;
        }""",
        text,
    )
    assert clicked, f"Button {text!r} not found in light DOM or any open shadow root"


async def _select_source_row(page: Page, filename: str) -> None:
    """Select `filename`'s row in the sources table.

    Marimo table gotcha (documented, do not rediscover): `mo.ui.table` rows do
    NOT select on a row-body click — only the leftmost selector `td` (the radio
    input marimo renders ahead of the data columns) toggles selection. This
    fixture only ever has 2 rows with distinct filenames, so filtering the row
    by `has_text` is safe here; elsewhere prefer filtering on the specific
    filename cell, since another column (e.g. `error`) could coincidentally
    contain the search text and select the wrong row.
    """
    row = page.locator("table tbody tr").filter(has_text=filename)
    await row.locator("td").first.click()
    await page.wait_for_timeout(300)


async def _toggle_checkbox(page: Page, label_text: str) -> None:
    """Click a native `mo.ui.checkbox` located by its label text.

    marimo renders checkboxes as a `<marimo-checkbox>` custom element with an
    internal (open) shadow root — same pattern as the read-app's "strict mode"
    toggle. CSS/role locators pierce open shadow DOM automatically, so no
    manual shadow walk is needed here (contrast with the anywidget buttons
    above, whose fallback exists for the closed-root case).
    """
    toggle = page.locator(
        "marimo-checkbox", has=page.get_by_text(label_text, exact=False)
    ).get_by_role("checkbox")
    await toggle.wait_for(state="visible", timeout=5_000)
    await toggle.click()


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_app_loads_targeting_the_env_wiki(page: Page) -> None:
    """Wiki picker defaults to WIKI_PATH with no dropdown interaction needed.

    `ENV_DEFAULT` in ingest_app.py's `setup()` cell seeds `wiki_state`'s
    `active_wiki`, so `wiki_picker`'s dropdown already shows the test
    workspace on first paint — this pins the "active wiki" contract without
    driving the dropdown at all.

    marimo renders `mo.ui.dropdown` as a native `<select>`, and its options carry
    the LABEL as their value (`wiki_picker` builds `{short_label(p): p}`). Never
    assert an `<option>` is *visible* — options inside a collapsed select never
    are. Assert the select's current value instead, which is the stronger claim:
    the option exists AND is the selected one.
    """
    label = short_label(str(WORKSPACE.resolve()))
    picker = page.locator(f'select:has(option[value="{label}"])').first
    await picker.wait_for(state="attached", timeout=10_000)
    assert await picker.input_value() == label, (
        f"Wiki picker is not defaulting to WIKI_PATH: expected {label!r}, "
        f"got {await picker.input_value()!r}"
    )

    upload_input = page.locator("input[type='file']")
    assert await upload_input.count() > 0, "Upload widget (input[type='file']) not found"


@pytest.mark.parametrize("pdf_name", PDFS)
async def test_ingest_pdf(page: Page, pdf_name: str) -> None:
    """Upload one PDF, submit the ingest form, poll DB until ready, assert DB state.

    The form is the change under test here vs. the old suite: `ingest_form`
    bundles the submit button with the "full LLM lint & repair" checkbox so
    both are read atomically on submit (no checkbox-reset race). We leave it
    unchecked (the default) — the cheap deterministic lint still runs after
    ingest and costs nothing, and its log lines are what
    test_activity_log_streams_pipeline_steps asserts on.
    """
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


async def test_activity_log_streams_pipeline_steps(page: Page) -> None:
    """Activity Log carries deterministic markers proving the ingest->lint
    pipeline ran, not just the LLM wiki-page write.

    Ingest submits are per-file, so by the time test_ingest_pdf's DB poll
    returns for a given PDF, the reconciliation/lint pass that follows the
    ingest loop inside `ingest_runner` may still be mid-flight. Poll for the
    run's closing "total:" marker (emitted once, by `make_timed_logger.finish()`,
    only after ingest + lint + crosslink all complete) before reading the log,
    rather than racing it. `finish()` writes it as `**total: …**`, but we read
    RENDERED text — marimo turns the `**` into bold markup, so the needle must be
    the bare `total:`, never the raw markdown.

    `make_timed_logger` REPLACES the log state per run (not append-across-runs),
    so what's visible here is the *last* ingest's messages only — good enough,
    since none of these markers are file-specific.
    """
    log_text = await _wait_for_log_contains(page, "total:", timeout_s=60)

    for marker in (
        "Ingestion started",
        "Done:",
        "Running deterministic lint",   # proves the cheap path ran, not the LLM one
        "lint: vocabulary",             # the vocabulary check runs
        "lint: thin pages",             # the thin-page detector runs
    ):
        assert marker in log_text, f"Activity Log missing {marker!r}\n\nFull log:\n{log_text}"


async def test_vocabulary_artifact_written() -> None:
    """The ingest-time vocabulary subsystem writes aliases.generated.toml.

    Deliberately NOT asserting it has entries: concept aliases come from the
    LLM (extract_structured), and a fairy tale may legitimately yield none —
    the project's own UAT notes that an empty file is not a failure. If it is
    non-empty, only a *shape* check: valid TOML, and (if present) [alias_datos]
    is a table.

    `.llmwiki/dataset_aliases.fingerprint` is NOT expected here — that sidecar
    is written by the batch `scan_and_ingest` dataset pass, and this fixture
    has no `datasets/` dir.
    """
    assert ALIASES_PATH.exists(), f"{ALIASES_PATH} was not written by ingestion"

    raw = ALIASES_PATH.read_bytes()
    if raw.strip():
        data = tomllib.loads(raw.decode("utf-8"))
        if "alias_datos" in data:
            assert isinstance(data["alias_datos"], dict), (
                "[alias_datos] should parse as a TOML table"
            )


async def test_scan_is_idempotent(page: Page) -> None:
    """Both PDFs are already ingested and unchanged, so a scan must skip them —
    no LLM work when nothing changed."""
    conn = _db()
    count_before = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE source_kind='source'"
    ).fetchone()[0]
    conn.close()

    await page.get_by_text("Scan sources/ for changes", exact=False).click()
    log_text = await _wait_for_log_contains(page, "Scan complete", timeout_s=30)
    assert "skipped" in log_text or "ingested: 0" in log_text, (
        f"Expected the scan to skip already-ingested files.\n\nLog:\n{log_text}"
    )

    conn = _db()
    count_after = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE source_kind='source'"
    ).fetchone()[0]
    conn.close()
    assert count_after == count_before, (
        f"Source count changed on a no-op scan: {count_before} -> {count_after}"
    )


def test_final_db_state() -> None:
    """Verify DB totals after both ingestions, plus a real cross-link check the
    old suite missed: at least one links_to edge between two wiki pages, i.e.
    `crosslink_wiki_pages` ran as the ingest pipeline's final pass."""
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
    wiki_links = conn.execute(
        "SELECT COUNT(*) FROM document_references dr "
        "JOIN documents d1 ON dr.source_document_id = d1.id "
        "JOIN documents d2 ON dr.target_document_id = d2.id "
        "WHERE dr.reference_type='links_to' "
        "AND d1.source_kind='wiki' AND d2.source_kind='wiki'"
    ).fetchone()[0]
    conn.close()

    print("\n📊 Final DB state:")
    for r in rows:
        print(f"   {r['filename']}: {r['status']}  ({r['page_count']} pages)")
    print(f"   Wiki pages total: {wiki_pages}")
    print(f"   wiki<->wiki links_to edges: {wiki_links}")

    assert src_ready == len(PDFS), f"Expected {len(PDFS)} ready source docs, got {src_ready}"
    assert wiki_pages >= len(PDFS), f"Expected >={len(PDFS)} wiki pages, got {wiki_pages}"
    assert wiki_links >= 1, "Expected at least one wiki<->wiki links_to cross-link"


# ── Opt-in / destructive tests — kept last so they never affect the tests above ──

@pytest.mark.skipif(
    os.environ.get("E2E_DESTRUCTIVE") != "1",
    reason="destructive — set E2E_DESTRUCTIVE=1 to run (deletes a source + its derived wiki pages)",
)
async def test_delete_source_removes_derived_pages(page: Page) -> None:
    """Delete a source through the UI; assert the cascade contract: the source
    row AND its derived wiki pages are gone from the DB
    (.trellis/spec/backend/database-guidelines.md)."""
    target = PDFS[0]
    src = assert_source_ok(target)
    doc_id = src["id"]

    await _select_source_row(page, target)
    await _toggle_checkbox(page, "Also remove file from sources/")
    await _click_button_pierce_shadow(page, f"Delete {target}")
    await page.wait_for_timeout(300)
    await _click_button_pierce_shadow(page, "Confirm")

    deadline = time.time() + 30
    while time.time() < deadline:
        conn = _db()
        still_there = conn.execute(
            "SELECT id FROM documents WHERE filename=? AND source_kind='source'",
            (target,),
        ).fetchone()
        conn.close()
        if not still_there:
            break
        time.sleep(1)
    else:
        pytest.fail(f"Timeout — source row for {target!r} was not deleted")

    conn = _db()
    derived = conn.execute(
        "SELECT id FROM documents WHERE source_kind='wiki' AND source_document_id=?",
        (doc_id,),
    ).fetchone()
    conn.close()
    assert derived is None, f"Wiki page derived from {target!r} survived the cascade delete"
    assert not (WORKSPACE / "sources" / target).exists(), (
        f"'{target}' still present in sources/ despite 'Also remove file' being checked"
    )


@pytest.mark.skipif(
    os.environ.get("E2E_FULL") != "1",
    reason="spends LLM tokens/minutes — set E2E_FULL=1 to run the wiki-wide lint & repair sweep",
)
async def test_wiki_wide_lint_and_repair(page: Page) -> None:
    """Confirm the 'Run Wiki Lint & Repair' widget; the sweep always passes the
    live llm_client, so this is the one test in the suite that spends real
    tokens/minutes — hence opt-in."""
    await _click_button_pierce_shadow(page, "Run Wiki Lint & Repair")
    await page.wait_for_timeout(300)
    await _click_button_pierce_shadow(page, "Confirm")

    log_text = await _wait_for_log_contains(
        page, "total:", timeout_s=300,
    )
    assert "❌" not in log_text, f"Lint & repair logged an error.\n\nLog:\n{log_text}"
    reached_a_conclusion = (
        ("Found" in log_text and "issue" in log_text) or "No issues found" in log_text
    )
    assert reached_a_conclusion, (
        f"Expected a 'Found N issue(s)' or 'No issues found' line.\n\nLog:\n{log_text}"
    )
