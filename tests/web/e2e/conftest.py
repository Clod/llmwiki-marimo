"""Fixtures of the Playwright tests: the application on a free port, over a copy
of `examples/finanzas-argentinas`, with simulated agents (`server.py`).

The tests are skipped when Playwright cannot start a browser. No test calls a
model: the agents are simulated, and the ingestion services are replaced.
"""

from __future__ import annotations

import glob
import os

import pytest

from tests.web.e2e.server import WIKI_ID, serve  # noqa: F401 — WIKI_ID is imported by the tests


def chromium_fallback() -> str | None:
    """A Chromium of the browsers folder when the one Playwright expects is
    missing (the folder holds another revision)."""
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    for pattern in ("chromium/chrome", "chromium-*/chrome-linux*/chrome"):
        for candidate in sorted(glob.glob(os.path.join(root, pattern))):
            if os.access(candidate, os.X_OK):
                return candidate
    return None


def launch(playwright):
    """Start Chromium; raise `RuntimeError` when no browser can start."""
    try:
        return playwright.chromium.launch()
    except Exception as exc:  # noqa: BLE001 — the expected revision is not installed
        fallback = chromium_fallback()
        if fallback is None:
            raise RuntimeError("Playwright browsers are not installed (uv run playwright install chromium)") from exc
        try:
            return playwright.chromium.launch(executable_path=fallback)
        except Exception as exc2:  # noqa: BLE001
            raise RuntimeError(f"Playwright cannot start a browser: {exc2}") from exc2


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    try:
        try:
            instance = launch(playwright)
        except RuntimeError as exc:
            pytest.skip(str(exc))
        yield instance
        instance.close()
    finally:
        playwright.stop()


@pytest.fixture
def live(tmp_path):
    with serve(tmp_path) as server:
        yield server


@pytest.fixture
def context(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(15000)
    # The flows assert Spanish texts: they run with the Spanish interface (`test_language.py` switches it).
    ctx.add_cookies([{"name": "ui_lang", "value": "es", "domain": "127.0.0.1", "path": "/"}])
    yield ctx
    ctx.close()


@pytest.fixture
def page(context):
    return context.new_page()
