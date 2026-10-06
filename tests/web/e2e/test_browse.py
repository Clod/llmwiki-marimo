"""The folder browser of the picker in a real browser: go into a folder, create one, open it."""

from __future__ import annotations

from playwright.sync_api import expect


def settled(page, text):
    """Wait until the panel shows ``text`` and htmx has processed it: a click on a button htmx
    has not processed yet is lost."""
    expect(page.locator("#browse-panel")).to_contain_text(text)
    page.wait_for_function("!document.querySelector('.htmx-request, .htmx-settling, .htmx-swapping')")


def test_the_browser_creates_a_folder_and_opens_it_as_a_new_wiki_on_ingest(page, live, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    page.goto(f"{live.url}/")
    page.get_by_role("button", name="Abrir otra carpeta…").click()
    dialog = page.locator("dialog.browse")
    expect(dialog).to_be_visible()

    dialog.get_by_label("Carpeta que se muestra").fill(str(tmp_path))
    dialog.get_by_role("button", name="Ir", exact=True).click()
    settled(page, "outside")
    dialog.get_by_role("button", name="outside", exact=True).click()
    expect(dialog.get_by_label("Carpeta que se muestra")).to_have_value(str(outside))
    expect(dialog).to_contain_text("No hay subcarpetas.")
    settled(page, "No hay subcarpetas.")

    dialog.get_by_label("Nombre de una carpeta nueva").fill("mi wiki")
    dialog.get_by_role("button", name="Crear carpeta").click()
    expect(dialog.get_by_label("Carpeta que se muestra")).to_have_value(str(outside / "mi wiki"))
    assert (outside / "mi wiki").is_dir()
    settled(page, "todavía no tiene una wiki")

    expect(dialog.get_by_role("button", name="Abrir esta carpeta")).to_have_count(0)    # a plain folder is not opened
    dialog.get_by_role("button", name="Crear una wiki aquí…").click()
    confirm = dialog.locator("#browse-create")
    expect(confirm).to_contain_text(str(outside / "mi wiki"))
    confirm.get_by_role("button", name="Cancelar").click()                          # cancelling creates nothing
    expect(confirm).to_be_hidden()
    assert list((outside / "mi wiki").iterdir()) == []
    dialog.get_by_role("button", name="Crear una wiki aquí…").click()
    confirm.get_by_role("button", name="Crear la wiki").click()
    expect(page).to_have_url(f"{live.url}/w/mi%20wiki/ingest")     # a wiki without pages opens on Ingest


def test_cancel_closes_the_browser_and_opens_nothing(page, live):
    page.goto(f"{live.url}/")
    page.get_by_role("button", name="Abrir otra carpeta…").click()
    dialog = page.locator("dialog.browse")
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Cancelar").click()
    expect(dialog).to_have_count(0)
    expect(page.get_by_role("button", name="Abrir otra carpeta…")).to_be_focused()


def test_leaving_the_picker_removes_the_dialog_so_back_does_not_show_it(page, live):
    # Playwright's Chromium runs without the back-forward cache, so Back reloads the page and
    # cannot show the bug; the test fires the events a cached page receives instead.
    page.goto(f"{live.url}/")
    page.get_by_role("button", name="Abrir otra carpeta…").click()
    expect(page.locator("dialog.browse")).to_be_visible()
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}))")
    expect(page.locator("dialog.browse")).to_have_count(0)

    page.get_by_role("button", name="Abrir otra carpeta…").click()
    expect(page.locator("dialog.browse")).to_be_visible()
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    expect(page.locator("dialog.browse")).to_have_count(0)


def test_remove_from_the_list_takes_a_recent_wiki_out_of_the_picker(page, live, tmp_path):
    folder = tmp_path / "reciente"
    (folder / "wiki").mkdir(parents=True)
    page.request.post(f"{live.url}/open", form={"path": str(folder)})
    page.goto(f"{live.url}/")
    row = page.locator("table.wikis tr", has=page.locator('a[href="/w/reciente/"]'))
    expect(row).to_have_count(1)
    row.get_by_role("button", name="Quitar de la lista").click()
    expect(page).to_have_url(f"{live.url}/")
    expect(page.locator('a[href="/w/reciente/"]')).to_have_count(0)
    assert (folder / "wiki").is_dir()
