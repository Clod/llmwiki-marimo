"""The Vocabulario screen in a real browser, on a copy of the finance example."""

from __future__ import annotations

from playwright.sync_api import expect

from domain.chat.config import load_config
from tests.web.e2e.server import WIKI_ID


def settled(page):
    page.wait_for_function("!document.querySelector('.htmx-request, .htmx-settling, .htmx-swapping')")


def test_search_the_roster_reject_an_alias_and_blacklist_a_term(page, live):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{live.url}/w/{WIKI_ID}/vocabulary")
    expect(page.locator(".tabs").get_by_role("link", name="Vocabulario")).to_have_attribute("aria-current", "page")
    assert page.evaluate("document.documentElement.scrollHeight") == 900     # the screen fills the window
    expect(page.locator(".vocab-findings")).to_contain_text("«uva»")

    roster = page.locator("#roster-list li[data-name]:visible")
    total = roster.count()
    page.get_by_label("Buscar en el padrón").fill("DOLAR")                    # no case, no accent
    expect(roster.first).to_be_visible()
    names = roster.evaluate_all("items => items.map(i => i.dataset.name)")
    assert names and all("dolar" in n.lower().replace("ó", "o") for n in names) and len(names) < total
    page.get_by_label("Buscar en el padrón").fill("zzz")
    expect(page.locator("#roster-none")).to_be_visible()
    page.get_by_label("Buscar en el padrón").fill("")
    expect(roster).to_have_count(total)

    group = page.locator(".alias-group", has=page.get_by_role("heading", name="Plazo fijo UVA", exact=True))
    group.get_by_role("button", name="Descartar").click()
    settled(page)
    expect(page.locator("#h-rejected")).to_contain_text("Alias descartados · 3")
    assert "Plazo fijo UVA" not in load_config(live.wiki_dir).data_aliases

    page.get_by_label("Término nuevo de la lista negra").fill("futuros")
    page.locator("section:has(#h-blacklist)").get_by_role("button", name="Agregar").click()
    settled(page)
    expect(page.locator("#h-blacklist")).to_contain_text("Lista negra · 5")
    assert "futuros" in load_config(live.wiki_dir).off_limits

    page.get_by_label("Término nuevo de la lista negra").fill("acciones")
    page.locator("section:has(#h-blacklist)").get_by_role("button", name="Agregar").click()
    expect(page.locator(".vocab-message")).to_contain_text("«acciones» es un tema que la wiki cubre")


def test_a_dataset_name_opens_its_dataset_in_a_dialog(page, live):
    page.goto(f"{live.url}/w/{WIKI_ID}/vocabulary")
    page.get_by_label("Buscar en el padrón").fill("nación")
    entry = page.get_by_role("button", name="Banco Nación", exact=True)
    entry.click()
    dialog = page.locator("dialog.dataset")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("heading", level=2)).to_have_text("datasets/plazo_fijo.md")
    expect(dialog.locator(".dataset-meta")).to_contain_text("TNA · %")
    expect(dialog.locator("tbody tr")).to_have_count(8)
    expect(dialog.locator("tr.marked th")).to_have_text("Banco Nación")
    expect(dialog.locator("tr.marked")).to_be_in_viewport()
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(entry).to_be_focused()

    page.get_by_label("Buscar en el padrón").fill("dolar")
    page.get_by_role("button", name="dolar", exact=True).click()
    expect(dialog.locator("thead th.num")).to_have_text(["compra", "venta"])
    expect(dialog.locator("tr.marked")).to_have_count(0)
    dialog.get_by_role("button", name="Cerrar", exact=True).click()
    expect(dialog).to_have_count(0)
