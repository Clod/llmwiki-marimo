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


@pytest.mark.parametrize("lang", ["en", "es"])
@pytest.mark.parametrize("width", [1440, 1024])
def test_the_question_box_and_its_controls_stay_inside_the_conversation_panel(page, context, live, lang, width):
    """The Spanish label of the agent mode is the longest option of the Mode selector. It
    once widened the composer, and the Send button ended past the right edge of the panel."""
    context.add_cookies([{"name": "ui_lang", "value": lang, "url": live.url}])
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live.url}/w/{WIKI_ID}/pages/{CONCEPT}")
    panel = page.locator("#composer").bounding_box()
    right = panel["x"] + panel["width"]
    for selector in ["#composer textarea", "#composer .composer-row .btn", "#relevance-choice label"]:
        for box in [element.bounding_box() for element in page.locator(selector).all()]:
            assert box["x"] + box["width"] <= right + 0.5, f"{selector} ends past the conversation panel ({lang}, {width} px)"
    chat = page.locator("#chat").bounding_box()
    assert right <= chat["x"] + chat["width"] + 0.5, f"the composer ends past the conversation panel ({lang}, {width} px)"
