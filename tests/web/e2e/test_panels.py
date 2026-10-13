"""The separators of the reading page: drag, keys, double click, minimum and persistence."""

from __future__ import annotations

import pytest
from tests.web.e2e.server import WIKI_ID

URL = "/w/{wiki}/pages/concepts/plazo-fijo-uva"


@pytest.fixture
def reader(page, live):
    page.goto(live.url + URL.format(wiki=WIKI_ID))
    return page


def chat_width(page) -> float:
    return page.locator("#chat").bounding_box()["width"]


def index_width(page) -> float:
    return page.locator("#index").bounding_box()["width"]


def drag_by(page, selector: str, dx: float) -> None:
    box = page.locator(selector).bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx / 2, y)
    page.mouse.move(x + dx, y)
    page.mouse.up()


def test_dragging_the_chat_separator_changes_the_width_of_the_chat(reader):
    before = chat_width(reader)
    assert round(before) == 380
    drag_by(reader, ".separator-chat", -100)
    assert abs(chat_width(reader) - (before + 100)) <= 2
    assert reader.locator(".separator-chat").get_attribute("aria-valuenow") == str(round(chat_width(reader)))


def test_the_width_survives_a_reload(reader):
    drag_by(reader, ".separator-chat", -100)
    width = chat_width(reader)
    reader.reload()
    assert abs(chat_width(reader) - width) <= 1


def test_the_stored_width_is_applied_before_the_first_paint(reader):
    drag_by(reader, ".separator-chat", -100)
    reader.reload()
    # The inline script of the head set the variable before the body existed.
    assert reader.evaluate("document.getElementById('panel-widths').textContent").count("--chat-w") == 1


def test_double_click_restores_the_default_width(reader):
    drag_by(reader, ".separator-chat", -100)
    reader.locator(".separator-chat").dblclick()
    assert round(chat_width(reader)) == 380
    reader.reload()
    assert round(chat_width(reader)) == 380


def test_the_arrow_keys_move_the_separator_by_steps(reader):
    separator = reader.locator(".separator-chat")
    separator.focus()
    reader.keyboard.press("ArrowLeft")
    assert round(chat_width(reader)) == 396
    reader.keyboard.press("Shift+ArrowLeft")
    assert round(chat_width(reader)) == 460
    reader.keyboard.press("ArrowRight")
    assert round(chat_width(reader)) == 444


def test_the_chat_never_goes_under_280px_nor_leaves_the_page_under_360px(reader):
    drag_by(reader, ".separator-chat", 600)
    assert round(chat_width(reader)) == 280
    drag_by(reader, ".separator-chat", -2000)
    page_box = reader.locator(".page").bounding_box()
    assert round(page_box["width"]) == 360


def test_the_index_separator_works_when_the_index_is_open(reader):
    assert not reader.locator(".separator-index").is_visible()
    reader.locator("#index-btn").click()
    assert round(index_width(reader)) == 220
    drag_by(reader, ".separator-index", -200)
    assert round(index_width(reader)) == 160
    reader.locator(".separator-index").focus()
    reader.keyboard.press("ArrowRight")
    assert round(index_width(reader)) == 176
    reader.locator(".separator-index").dblclick()
    assert round(index_width(reader)) == 220


def test_the_chat_separator_is_hidden_with_the_chat(reader):
    assert reader.locator(".separator-chat").is_visible()
    reader.locator("#chat-btn").click()
    assert not reader.locator(".separator-chat").is_visible()


def test_the_page_text_fills_its_column(reader):
    prose = reader.locator(".page .prose").bounding_box()["width"]
    inner = reader.locator(".page-inner").bounding_box()["width"]
    assert prose > 800 and inner - prose < 100


def test_no_separator_under_1100px(page, live):
    page.set_viewport_size({"width": 1000, "height": 800})
    page.goto(live.url + URL.format(wiki=WIKI_ID))
    assert not page.locator(".separator-chat").is_visible()
