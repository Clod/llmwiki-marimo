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

# tests/conftest.py puts `base/` on sys.path, so this matches how the marimo
# apps themselves resolve LLM config (settings.LLM_API_KEY etc.) — see
# base/config.py and marimo/read_app.py's require_llm_config().
from config import settings
from domain.chat.guardrail import REFUSAL_EN

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


@pytest.fixture
async def chat_page(browser_session: Browser, read_app_server: str) -> Page:
    """Function-scoped, independent page for chat tests.

    A chat turn mutates server-side conversation state (mo.state history in
    chat_panel's `respond` closure), so chat tests get their own tab rather
    than sharing the session-scoped `page` fixture used by the read/navigate
    tests above. New page == fresh marimo session == empty chat history.
    """
    p = await browser_session.new_page(viewport={"width": 1440, "height": 900})
    await p.goto(read_app_server, wait_until="networkidle", timeout=30_000)
    await p.wait_for_timeout(2_000)
    yield p
    await p.close()


# ── Chat helpers ──────────────────────────────────────────────────────────────

_LLM_CONFIGURED = bool(
    (settings.LLM_API_KEY or "").strip()
    and (settings.LLM_BASE_URL or "").strip()
    and (settings.LLM_MODEL or "").strip()
)

# Always present in the chatbot subtree (the CodeMirror input's placeholder
# text), and the "Stop" button rendered by mo.ui.chat while a turn is in
# flight — both get stripped out of the answer text the tests compare against.
_STOP_MARKER = "Stop"
_INPUT_PLACEHOLDER = "Type your message here, / for prompts"


async def _shadow_text(locator) -> str:
    """Shadow-DOM-piercing innerText equivalent.

    marimo-chatbot (and marimo-checkbox) render into a closed-ish custom
    element whose content lives under `el.shadowRoot`; Playwright's plain
    `.inner_text()` / `.text_content()` do not walk shadow boundaries, so we
    walk them manually via `evaluate()`.
    """
    return await locator.evaluate(
        """
        el => {
            function getText(node) {
                if (node.shadowRoot) {
                    return Array.from(node.shadowRoot.childNodes).map(getText).join('');
                }
                if (node.nodeType === Node.TEXT_NODE) {
                    return node.textContent;
                }
                if (node.childNodes) {
                    return Array.from(node.childNodes).map(getText).join('');
                }
                return '';
            }
            return getText(el);
        }
        """
    )


async def _send_chat_message(page: Page, message: str) -> None:
    """Type into the marimo chat's CodeMirror contenteditable and submit.

    The input is `.cm-content[contenteditable="true"]` inside marimo-chatbot's
    shadow DOM (CSS pierces shadow DOM, so a plain shadow-piercing locator
    works). Enter inserts a newline in CodeMirror rather than submitting, so
    submission goes through the form's send button instead, which marimo
    enables once the editor has non-whitespace content.
    """
    chat_input = page.locator("marimo-chatbot [contenteditable='true']").last
    await chat_input.click()
    await chat_input.type(message)
    await page.wait_for_timeout(200)

    submit_btn = page.locator("marimo-chatbot button[type='submit']").last
    await submit_btn.click()


async def _wait_for_chat_answer(page: Page, *, timeout: float = 60.0) -> str:
    """Poll the chatbot subtree until the in-flight turn completes.

    Strict mode buffers the whole answer (no streaming) and OFF mode streams
    token-by-token, so plain text-equality-across-polls is not a reliable
    "done" signal on its own — a stall mid-stream can look stable for a poll
    or two. The "Stop" button is rendered by mo.ui.chat exactly while a turn
    is in flight (cancel control) and removed when it completes, so its
    absence is the authoritative readiness signal; text-stability is then a
    confirming second check.
    """
    chatbot = page.locator("marimo-chatbot").last
    deadline = time.time() + timeout
    last_text = ""
    stable_reads = 0

    while time.time() < deadline:
        await page.wait_for_timeout(500)
        current = await _shadow_text(chatbot)
        turn_in_flight = _STOP_MARKER in current
        if not turn_in_flight and current == last_text:
            stable_reads += 1
            if stable_reads >= 2:
                break
        else:
            stable_reads = 0
        last_text = current

    # Strip the always-present input-box placeholder so callers compare only
    # against actual conversation content.
    return last_text.replace(_INPUT_PLACEHOLDER, "").strip()


def _require_llm_configured() -> None:
    if not _LLM_CONFIGURED:
        pytest.skip(
            "LLM not configured (settings.LLM_API_KEY/LLM_BASE_URL/LLM_MODEL "
            "blank in .env) — skipping live-chat E2E test."
        )


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


# ── Chat handler (respond) — live LLM round trip ────────────────────────────
#
# Everything above only checks that the chat widget *renders*. These two tests
# drive an actual chat turn through marimo/read_app.py's `respond` handler and
# assert on the grounding guardrail (base/domain/chat/guardrail.py), which is
# the one piece of behavior in `respond` that is deterministic enough to test
# reliably: with the "Modo estricto" toggle ON (the default), an off-corpus
# question must come back as the exact refusal phrase, because
# enforce_grounding() replaces the model's output whenever no tool call
# returned substantive evidence — irrespective of what the model itself said.
#
# Both use the dedicated `chat_page` fixture (a fresh tab) rather than the
# shared session-scoped `page` used above, and are placed last in the file so
# a prior test's page state is never affected by a chat turn's side effects.

_OFF_CORPUS_QUESTION = "what is a quinoto?"  # a fruit; not in the fairy-tale fixture wiki


async def test_chat_strict_mode_refuses_off_corpus_question(chat_page: Page) -> None:
    """Strict mode ON (default) + off-corpus question -> exact refusal phrase.

    Deterministic: enforce_grounding() in guardrail.py substitutes REFUSAL_EN
    for the model's answer whenever has_grounding() finds no substantive tool
    result in the run. "what is a quinoto?" has no match in the fairy-tale
    fixture wiki/sources, so every grounding tool call returns its own
    "nothing found" sentinel and the guardrail must fire.
    """
    _require_llm_configured()

    toggle = chat_page.locator(
        "marimo-checkbox", has=chat_page.get_by_text("Modo estricto", exact=False)
    ).get_by_role("checkbox")
    await toggle.wait_for(state="visible", timeout=5_000)
    assert await toggle.get_attribute("aria-checked") == "true", (
        "Strict mode should be ON by default (grounding_flag = {'strict': True})"
    )

    await _send_chat_message(chat_page, _OFF_CORPUS_QUESTION)
    answer = await _wait_for_chat_answer(chat_page, timeout=60.0)

    print(f"\n   Strict-mode answer: {answer!r}")
    assert REFUSAL_EN in answer, (
        f"Expected the guardrail refusal {REFUSAL_EN!r} for an off-corpus "
        f"question under strict mode, got: {answer!r}"
    )


async def test_chat_relaxed_mode_answers_off_corpus_question(chat_page: Page) -> None:
    """Strict mode OFF + same off-corpus question -> NOT the refusal, non-empty.

    Best-effort/secondary: with strict mode off, `respond` takes the
    `run_stream` branch (original UX, no guardrail gating), so the model
    answers freely in its own words. This only asserts shape (non-empty,
    not the canonical refusal string) since the model's exact wording for
    "I don't know" is not deterministic.
    """
    _require_llm_configured()

    toggle = chat_page.locator(
        "marimo-checkbox", has=chat_page.get_by_text("Modo estricto", exact=False)
    ).get_by_role("checkbox")
    await toggle.wait_for(state="visible", timeout=5_000)
    await toggle.click()
    await chat_page.wait_for_timeout(300)
    assert await toggle.get_attribute("aria-checked") == "false", (
        "Toggling 'Modo estricto' should flip grounding_flag['strict'] to False"
    )

    await _send_chat_message(chat_page, _OFF_CORPUS_QUESTION)
    answer = await _wait_for_chat_answer(chat_page, timeout=60.0)

    print(f"\n   Relaxed-mode answer: {answer!r}")
    assert answer, "Expected a non-empty answer with the guardrail off"
    assert REFUSAL_EN not in answer, (
        f"Guardrail should not gate the answer when strict mode is off, "
        f"got: {answer!r}"
    )
