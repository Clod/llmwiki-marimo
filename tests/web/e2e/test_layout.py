"""The reading screen fills the window: the panels scroll, the document does not."""

from __future__ import annotations

import pytest
from tests.web.e2e.server import WIKI_ID

CONCEPT = "concepts/plazo-fijo-uva"


@pytest.mark.parametrize("width, height", [(1440, 900), (1440, 600), (1024, 768)])
def test_the_reading_panels_take_the_window_height_and_scroll_inside(page, live, width, height):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{live.url}/w/{WIKI_ID}/pages/{CONCEPT}")
    page.locator("#index-btn").click()
    page.wait_for_function("document.body.classList.contains('index-open')")
    assert page.evaluate("document.documentElement.scrollHeight") == height
    nav = page.locator(".index .column-body")
    client, scroll = nav.evaluate("e => [e.clientHeight, e.scrollHeight]")
    assert scroll > client, "the index of the example wiki is longer than the window and scrolls inside its panel"
    for selector in [".index", ".page", "#chat"]:
        box = page.locator(selector).bounding_box()
        assert box["y"] + box["height"] <= height, f"{selector} ends below the window"
