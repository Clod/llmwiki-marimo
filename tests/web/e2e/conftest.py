"""Fixtures of the Playwright tests: the application on a free port, over a copy
of `tests/fixtures/wikis/finanzas-argentinas`, with simulated agents (`server.py`).

The tests are skipped when Playwright cannot start a browser. No test calls a
model: the agents are simulated, and the ingestion services are replaced. The
one exception is `test_lint_live.py`, which runs only with `E2E_FULL=1` and
calls the model of `.env` (`server.serve_live`).

Every test records a Playwright trace: a screenshot and the page before each action.
When the test fails, the trace is kept as `test-results/<test>.zip` (the CI job uploads
the folder as the artifact `e2e-traces`); when it passes, the trace is dropped. To see
one: `gh run download <run id> -n e2e-traces`, then
`uv run playwright show-trace test-results/<test>.zip`.
"""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path

import pytest

from tests.web.e2e.server import WIKI_ID, serve  # noqa: F401 — WIKI_ID is imported by the tests


TRACES = Path(__file__).resolve().parents[3] / "test-results"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Keep the report of each phase on the test, for the `context` fixture to know
    whether the test failed."""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


def trace_path(nodeid: str) -> Path:
    """The file of the trace of the test `nodeid`: its name, with anything that is not a
    letter, a digit, `.`, `_` or `-` replaced by `_`."""
    name = nodeid.split("::", 1)[-1]
    return TRACES / (re.sub(r"[^\w.-]+", "_", name)[:150] + ".zip")


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
            # On a developer machine without a browser the flows are skipped. In the CI
            # (GitHub Actions sets `CI`) a browser is installed, so a failure to start one
            # is a failure: skipping would hide every flow behind a green run.
            if os.environ.get("CI"):
                pytest.fail(str(exc))
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
def context(browser, request):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(15000)
    # The flows assert Spanish texts: they run with the Spanish interface (`test_language.py` switches it).
    ctx.add_cookies([{"name": "ui_lang", "value": "es", "domain": "127.0.0.1", "path": "/"}])
    ctx.tracing.start(screenshots=True, snapshots=True)
    yield ctx
    failed = getattr(getattr(request.node, "rep_call", None), "failed", False)
    if failed:
        TRACES.mkdir(exist_ok=True)
        ctx.tracing.stop(path=str(trace_path(request.node.nodeid)))
    else:
        ctx.tracing.stop()
    ctx.close()


@pytest.fixture
def page(context):
    return context.new_page()
