"""Regenerate the README screenshots from the running read app.

The images in `docs/assets/` went stale because taking them was a manual
ceremony nobody wanted to repeat. This makes it one command:

    uv run python scripts/capture_screenshots.py

It launches `marimo/read_app_tabs.py` against the bundled `examples/fairy-tales`
wiki, drives it with Playwright, and writes:

    docs/assets/read_app_read_tab.png   page picker + a generated concept page
    docs/assets/read_app_chat_tab.png   a cited answer + the Save to wiki form

Two images rather than one, because the tabs app renders **only the active
tab** — there is no single frame that shows both halves, and pretending
otherwise would misrepresent the interface.

The chat capture makes a **real LLM call** (the same `LLM_*` settings the app
uses), so the answer in the picture is a genuine one with genuine citations, not
a mock. That is the point of the picture.

Requires: `uv run playwright install chromium` once.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.async_api import Page, async_playwright

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "base"))

from config import require_llm_config, settings  # noqa: E402

WIKI = _PROJECT_ROOT / "examples" / "fairy-tales"
ASSETS = _PROJECT_ROOT / "docs" / "assets"

# Retina: a 1600-wide viewport at 2x lands at 3200px, matching the image the
# README already ships so the swap is visually like-for-like. Heights differ per
# shot because the two tabs are differently tall, and dead space at the bottom of
# a screenshot reads as an empty app.
READ_VIEWPORT = {"width": 1600, "height": 1180}
CHAT_VIEWPORT = {"width": 1600, "height": 1160}
SCALE = 2

# Cross-document on purpose: it is the question that shows the wiki compounding,
# and the answer has to cite both pages to be right.
QUESTION = "What do Cinderella and Snow White have in common?"

PAGE_TO_OPEN = "Cinderella"
CHAT_TAB = "💬 Chat"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


async def _send_chat(page: Page, message: str) -> None:
    """Type into marimo's CodeMirror contenteditable and press the send button.

    Enter inserts a newline in CodeMirror rather than submitting, so submission
    goes through the form button — same approach as the E2E suite.
    """
    box = page.locator("marimo-chatbot [contenteditable='true']").last
    await box.click()
    await box.type(message)
    await page.wait_for_timeout(300)
    await page.locator("marimo-chatbot button[type='submit']").last.click()


# A failed turn renders marimo's red error box inside the chat. Capturing that
# would ship a picture of a broken app — and worse: provider errors quote the
# request back, which on at least one provider includes a key-management URL
# carrying the key's identifier. So a failed turn aborts instead of writing.
_FAILURE_MARKERS = (
    "failed with exception",
    "status_code: 4",
    "status_code: 5",
    "Retry",
)


async def _wait_for_answer(page: Page, timeout: float = 90.0) -> None:
    """Wait until the turn completes.

    mo.ui.chat renders a "Stop" button exactly while a turn is in flight, so its
    absence is the authoritative signal; text stability is the confirming one.
    """
    chatbot = page.locator("marimo-chatbot").last
    deadline = time.time() + timeout
    last, stable = "", 0
    while time.time() < deadline:
        await page.wait_for_timeout(500)
        current = await chatbot.evaluate(
            """el => { const walk = n => n.shadowRoot
                 ? Array.from(n.shadowRoot.childNodes).map(walk).join('')
                 : n.nodeType === Node.TEXT_NODE ? n.textContent
                 : n.childNodes ? Array.from(n.childNodes).map(walk).join('') : '';
               return walk(el); }"""
        )
        if "Stop" not in current and current == last:
            stable += 1
            if stable >= 2:
                return
        else:
            stable = 0
        last = current
    print("  ! answer did not settle within the timeout — capturing anyway")


async def _capture(url: str) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport=READ_VIEWPORT, device_scale_factor=SCALE)
        await page.goto(url, wait_until="networkidle", timeout=60_000)
        await page.wait_for_timeout(3_000)

        # ── Read tab ──────────────────────────────────────────────────────
        print("  opening a page in the Read tab…")
        row = page.locator("table tbody tr").filter(has_text=PAGE_TO_OPEN).first
        if await row.count():
            await row.locator("td").first.click()   # the radio cell, not the row
        else:
            await page.locator("table tbody tr").first.locator("td").first.click()
        await page.wait_for_timeout(2_000)

        out = ASSETS / "read_app_read_tab.png"
        await page.screenshot(path=str(out))
        print(f"  wrote {out.relative_to(_PROJECT_ROOT)}")

        # ── Chat tab ──────────────────────────────────────────────────────
        print("  switching to the Chat tab…")
        await page.set_viewport_size(CHAT_VIEWPORT)
        await page.get_by_text(CHAT_TAB, exact=False).last.click()
        await page.locator("marimo-chatbot").last.wait_for(state="attached", timeout=15_000)
        await page.wait_for_timeout(1_500)

        print(f"  asking: {QUESTION!r} (live model call)…")
        await _send_chat(page, QUESTION)
        await _wait_for_answer(page)

        transcript = await page.locator("marimo-chatbot").last.evaluate(
            """el => { const walk = n => n.shadowRoot
                 ? Array.from(n.shadowRoot.childNodes).map(walk).join('')
                 : n.nodeType === Node.TEXT_NODE ? n.textContent
                 : n.childNodes ? Array.from(n.childNodes).map(walk).join('') : '';
               return walk(el); }"""
        )
        hit = next((m for m in _FAILURE_MARKERS if m in transcript), None)
        if hit:
            await browser.close()
            raise SystemExit(
                f"the chat turn failed (matched {hit!r}) — refusing to write a "
                "screenshot of an error.\n"
                "The answer in this image is the whole point of it, and provider "
                "errors can quote credentials back.\n"
                "Fix the model config and re-run; "
                "docs/assets/read_app_chat_tab.png was left untouched."
            )

        # Expand the save form. Collapsed, the picture omits the whole point of
        # the feature: a chat answer becomes a page only on a human click.
        save = page.get_by_text("Save the last response to the wiki", exact=False).last
        if await save.count():
            await save.click()
            await page.wait_for_timeout(800)
        await page.wait_for_timeout(1_000)

        out = ASSETS / "read_app_chat_tab.png"
        await page.screenshot(path=str(out))
        print(f"  wrote {out.relative_to(_PROJECT_ROOT)}")

        await browser.close()


def main() -> None:
    require_llm_config(
        settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL,
        purpose="the README screenshots (the chat answer is a real one)",
    )
    if not (WIKI / "wiki").exists():
        raise SystemExit(f"no wiki at {WIKI} — the bundled demo is missing")
    ASSETS.mkdir(parents=True, exist_ok=True)

    port = _free_port()
    marimo_bin = Path(sys.executable).parent / "marimo"
    print(f"launching read_app_tabs.py on :{port} against {WIKI.name}…")
    proc = subprocess.Popen(
        [str(marimo_bin), "run", "marimo/read_app_tabs.py",
         "--port", str(port), "--headless", "--no-token", "--no-sandbox"],
        cwd=str(_PROJECT_ROOT),
        env={**os.environ, "WIKI_PATH": str(WIKI)},
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_for_port(port):
            raise SystemExit("the app never came up")
        time.sleep(2)
        asyncio.run(_capture(f"http://localhost:{port}"))
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    print("done.")


if __name__ == "__main__":
    main()
