"""The wiki-wide lint and repair of the Maintain screen, with the model of `.env`.

The test spends tokens and minutes: it runs only with `E2E_FULL=1`, and it is
skipped when no ingestion model is configured. The wait for the end of the
operation is `E2E_FULL_TIMEOUT_S` seconds (900 by default). The wiki is a copy
of `examples/finanzas-argentinas` in `tmp_path`: the repair can rewrite and
delete pages, and the example wiki does not change.

The test asserts what the code decides, never what the model writes: the
operation log ends with the closing line of `lint_post`, no log line carries
the `❌` of an exception, and the wiki still opens afterwards.

    E2E_FULL=1 uv run --group web pytest -q tests/web/e2e/test_lint_live.py
"""

from __future__ import annotations

import os
import re

import pytest
from playwright.sync_api import expect

from tests.web.e2e.server import WIKI_ID, live_model_missing, serve_live

pytestmark = pytest.mark.skipif(
    os.environ.get("E2E_FULL") != "1",
    reason="calls the real model (spends tokens and minutes); set E2E_FULL=1",
)

CLOSING = "🏁 Revisión y reparación:"
CLOSING_LINE = re.compile(r"🏁 Revisión y reparación: \d+ problema\(s\) encontrado\(s\)")


@pytest.fixture
def live_model(tmp_path):
    missing = live_model_missing()
    if missing:
        pytest.skip("no ingestion model configured: " + ", ".join(missing))
    with serve_live(tmp_path) as server:
        yield server


def test_lint_and_repair_runs_to_its_closing_line_with_the_real_model(page, live_model):
    timeout = int(os.environ.get("E2E_FULL_TIMEOUT_S", "900")) * 1000
    page.goto(f"{live_model.url}/w/{WIKI_ID}/maintain")
    page.get_by_role("button", name="Revisar y reparar la wiki").click()
    confirm = page.locator("#confirm-lint")
    expect(confirm).to_be_visible()
    # Without the verifier: this test covers the lint and the repair only.
    expect(page.locator("#verify-lint")).not_to_be_checked()
    # `exact`: the name of the button that opened the confirmation contains this one.
    confirm.get_by_role("button", name="Revisar y reparar", exact=True).click()

    log = page.locator("#progress ul.log")
    expect(log).to_contain_text(CLOSING, timeout=timeout)
    text = log.inner_text()
    assert "❌" not in text, f"the operation raised an exception:\n{text}"
    assert CLOSING_LINE.search(text), f"no closing line with a problem count:\n{text}"

    response = page.goto(f"{live_model.url}/w/{WIKI_ID}/pages/overview")
    assert response is not None and response.status == 200
