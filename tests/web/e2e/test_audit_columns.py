"""The separator of the audit page: drag, keys, double click, minimum and persistence."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.web.e2e.server import WIKI_ID
from tests.web.e2e.test_panels import drag_by


@pytest.fixture
def audit(page, live):
    """The audit page of a conversation with one answered question."""
    page.goto(f"{live.url}/w/{WIKI_ID}/pages/concepts/plazo-fijo-uva")
    page.locator("#composer textarea").fill("¿Qué es un plazo fijo UVA?")
    page.locator("#composer textarea").press("Enter")
    page.wait_for_function("() => !document.querySelector('#messages .typing')")
    href = page.locator("#audit-buttons a").first.get_attribute("href")
    page.goto(live.url + href)
    expect(page.locator(".audit-separator")).to_be_visible()
    return page


def diagrams_width(page) -> float:
    return page.locator("#audit-diagrams").bounding_box()["width"]


def turns_width(page) -> float:
    return page.locator("#audit-left").bounding_box()["width"]


def test_the_columns_start_equal(audit):
    assert abs(diagrams_width(audit) - turns_width(audit)) <= 2


def test_dragging_the_separator_to_the_left_widens_the_diagrams(audit):
    before = diagrams_width(audit)
    drag_by(audit, ".audit-separator", -200)
    assert abs(diagrams_width(audit) - (before + 200)) <= 2
    assert audit.locator(".audit-separator").get_attribute("aria-valuenow") == str(round(diagrams_width(audit)))


def test_the_turns_keep_their_minimum(audit):
    drag_by(audit, ".audit-separator", -2000)
    assert turns_width(audit) >= 320 - 1


def test_the_arrow_keys_move_the_separator(audit):
    before = diagrams_width(audit)
    audit.locator(".audit-separator").focus()
    audit.keyboard.press("ArrowLeft")
    assert abs(diagrams_width(audit) - (before + 16)) <= 2


def test_the_width_survives_a_reload_and_double_click_restores_it(audit):
    drag_by(audit, ".audit-separator", -200)
    width = diagrams_width(audit)
    audit.reload()
    expect(audit.locator(".audit-separator")).to_be_visible()
    assert abs(diagrams_width(audit) - width) <= 1
    audit.locator(".audit-separator").dblclick()
    assert abs(diagrams_width(audit) - turns_width(audit)) <= 2
