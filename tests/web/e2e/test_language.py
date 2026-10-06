"""The interface-language switch in a real browser, on the reading screen."""

from __future__ import annotations

from playwright.sync_api import expect

from tests.web.e2e.server import WIKI_ID

CONCEPT = "concepts/plazo-fijo-uva"


def test_the_switch_changes_the_language_of_the_reading_screen_and_keeps_the_page(context, page, live):
    context.clear_cookies()                       # no cookie: the browser's Accept-Language (en-US) decides
    url = f"{live.url}/w/{WIKI_ID}/pages/{CONCEPT}"
    page.goto(url)
    expect(page.locator("html")).to_have_attribute("lang", "en")
    expect(page.locator(".tabs").get_by_role("link", name="Read")).to_be_visible()
    expect(page.get_by_role("heading", name="Conversation")).to_be_visible()
    expect(page.get_by_placeholder("Ask something…")).to_be_visible()
    expect(page.get_by_role("button", name="EN", exact=True)).to_have_attribute("aria-pressed", "true")

    page.get_by_role("button", name="ES", exact=True).click()
    expect(page).to_have_url(url)                 # the switch reloads the same page
    expect(page.locator("html")).to_have_attribute("lang", "es")
    expect(page.locator(".tabs").get_by_role("link", name="Leer")).to_be_visible()
    expect(page.get_by_role("heading", name="Conversación")).to_be_visible()
    expect(page.get_by_placeholder("Preguntá algo…")).to_be_visible()
    expect(page.get_by_role("button", name="ES", exact=True)).to_have_attribute("aria-pressed", "true")
    assert any(c["name"] == "ui_lang" and c["value"] == "es" for c in context.cookies())

    page.reload()                                 # the choice outlives the page
    expect(page.locator("html")).to_have_attribute("lang", "es")

    page.get_by_role("button", name="EN", exact=True).click()
    expect(page.locator("html")).to_have_attribute("lang", "en")
    expect(page.get_by_role("heading", name="Conversation")).to_be_visible()
    expect(page.locator(".prose")).to_contain_text("UVA")   # the wiki's own text is not translated
