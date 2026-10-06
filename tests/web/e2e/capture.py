"""Screenshots of every screen at 1440, 1024 and 768 px, for the design review.

    uv run --group web python -m tests.web.e2e.capture <output folder> <label> [en|es]

The files are named `<label>_<n>-<screen>_<width>.png`. The interface is in the language given (default
`es`, the language the screens were first captured in): the labels the script clicks are looked up in
the catalog of that language. The application runs over
a copy of `examples/finanzas-argentinas` with simulated agents and a simulated
scan: no model call.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "base"))   # as tests/conftest.py does

from playwright.sync_api import Page, expect, sync_playwright  # noqa: E402

from services import ingest  # noqa: E402
from web import i18n  # noqa: E402
from web.i18n import _  # noqa: E402
from tests.web.e2e.conftest import launch  # noqa: E402
from tests.web.e2e.server import WIKI_ID, Live, serve  # noqa: E402

WIDTHS = (1440, 1024, 768)
HEIGHT = 900
PROGRESS = [
    "🔍 Validating 01 Acciones Locales.docx", "📄 Converting to PDF…", "📝 Extracting text (2 pages)",
    "🧩 Chunking: 14 chunks", "🧠 Generating the summary page", "📚 Creating concept pages (5)",
    "🔗 Cross-linking 5 page(s)", "✅ 01 Acciones Locales.docx — ingested",
    "⏭ 04 Bonos CER y UVA.docx — already up to date",
]


def fake_scan(wiki, client, model, *, progress):
    for line in PROGRESS:
        progress(line)
    return [SimpleNamespace(status="ingested"), SimpleNamespace(status="skipped")]


def screens(page: Page, live: Live):
    """Yield (number-name, ready page) for each screen; the page state is the screen's."""
    base = f"{live.url}/w/{WIKI_ID}"
    page.goto(live.url)
    yield "1-selector"
    page.goto(f"{base}/pages/concepts/plazo-fijo-uva")
    page.wait_for_load_state("networkidle")
    yield "2-lectura"
    # Reading with a conversation and the save panel. Wider than 1100 px the index
    # column is open; narrower, it is a drawer, shown on its own screen.
    wide = page.viewport_size["width"] > 1100
    if wide:
        page.locator("#index-btn").click()
    for question in ("¿Qué tasa de plazo fijo ofrece el Banco Galicia?", "¿Qué es un plazo fijo UVA?"):
        page.locator("#composer textarea").fill(question)
        page.locator("#composer textarea").press("Enter")
        expect(page.locator("#messages .a").last).not_to_be_empty()
        page.wait_for_function("() => !document.querySelector('#messages .typing')")
    page.evaluate("window.scrollTo(0, 0)")
    yield "2b-lectura-chat"
    page.locator("#save-btn").click()
    page.locator("#save-panel input[name=title]").fill("Plazos fijos de Banco Galicia")
    yield "3-lectura-conversacion"
    # The review dialog of the save, over the reading screen.
    page.locator("#save-panel .primary").click()
    expect(page.locator("dialog.review[open]")).to_be_visible()
    yield "3c-guardar-dialogo"
    page.keyboard.press("Escape")
    if not wide:
        page.locator("#index-btn").click()
        yield "3b-lectura-indice-cajon"
        page.locator("#index-btn").click()
    page.locator("#save-panel").get_by_role("button", name=_("Cancel")).click()
    page.get_by_role("button", name=_("Delete"), exact=True).click()
    yield "4-lectura-borrar"
    page.goto(f"{base}/edit/concepts/plazo-fijo-uva")
    yield "5-editor"
    page.goto(f"{base}/ingest")
    yield "6-ingesta"
    # Ingestion with an operation in the console and two confirmations open.
    page.get_by_role("button", name=_("Scan sources/ for changes")).click()
    expect(page.locator("#progress .log")).to_contain_text("🏁 " + _("Scan"))
    yield "7-ingesta-progreso"
    # Maintenance, with one confirmation open.
    page.goto(f"{base}/maintain")
    yield "8-mantener"
    page.get_by_role("button", name=_("Run Wiki Lint & Repair")).click()
    yield "9-mantener-confirmar"
    # History, with the confirmation of a revert open.
    page.goto(f"{base}/history")
    yield "10-historial"
    page.locator("li.commit").nth(1).get_by_role("button", name=_("Go back to this point")).click()
    expect(page.locator("li.commit").nth(1).locator(".confirm")).to_be_visible()
    yield "11-historial-confirmar"
    for name in relations_screens(page, live):
        yield f"12-relaciones-{name}"


def relations_screens(page: Page, live: Live):
    """The relations screen: the whole graph, a search highlight, the local graph."""
    base = f"{live.url}/w/{WIKI_ID}/relations"

    def ready():
        page.wait_for_selector("#relations[data-ready='1']")
        page.wait_for_function("document.getElementById('relations').dataset.settled === '1'")

    page.goto(base)
    ready()
    page.get_by_label(_("Show sources")).check()
    page.wait_for_timeout(1500)
    yield "1-grafo"
    page.locator("#search").fill("Plazo fijo UVA")
    page.locator("#search").press("Enter")
    page.wait_for_timeout(1500)
    yield "2-busqueda"
    page.goto(f"{base}?page=concepts/plazo-fijo-uva&depth=2")
    ready()
    page.wait_for_timeout(800)
    yield "3-local"


def fake_draft(db_path, workspace, title, content, category, **kwargs):
    """The draft of a saved conversation, without the model call."""
    import domain.chat.wiki_tools as wiki_tools
    from domain.tools.wiki_fs import read_page

    slug = "plazos-fijos-de-banco-galicia"
    body = f"# {title}\n\n## Definición\nEl plazo fijo UVA se ajusta por inflación.\n\n## Fuentes\n- 10 Plazos Fijos.docx"
    return wiki_tools.PageDraft(title, category, slug, f"wiki/concepts/{slug}.md", body,
                                bool(read_page(db_path, workspace, "/wiki/concepts/", slug)))


def seed_history(wiki) -> None:
    """Seven points in the history: the newest have an index copy, the oldest do not."""
    from domain.tools.git_ops import auto_commit, init_wiki_repo
    from domain.tools.wiki_fs import create_page

    init_wiki_repo(wiki.path)
    for n, message in enumerate(["ingest: 01 Acciones Locales.docx", "ingest: 04 Bonos CER y UVA.docx",
                                 "chat: Plazos fijos", "regenerate summaries: 2", "delete stale pages: 2",
                                 "ingest: 12 Cauciones Bursátiles.docx", "chat: Final del lobo"], start=1):
        create_page(wiki.db_path, wiki.path, "/wiki/concepts/", f"nota-{n}", f"Nota {n}",
                    "Una nota de prueba para el historial de la wiki. " * 3, [])
        auto_commit(wiki.path, message)


def main(out: Path, label: str, which=screens, lang: str = "es") -> None:
    out.mkdir(parents=True, exist_ok=True)
    i18n.set_language(lang)   # the labels this script clicks, in the language of the interface it captures
    ingest.scan_sources = fake_scan
    import domain.chat.wiki_tools as wiki_tools

    wiki_tools.draft_wiki_page = fake_draft
    with tempfile.TemporaryDirectory() as tmp, serve(Path(tmp)) as live, sync_playwright() as playwright:
        # One stale page, so the ingestion screen shows its list.
        from domain.tools.db import get_connection

        with get_connection(live.wiki().db_path) as conn:
            conn.execute("UPDATE documents SET stale_since = '2026-10-01 10:00:00' "
                         "WHERE relative_path IN ('wiki/concepts/byma.md', 'wiki/concepts/panel-lider.md')")
            conn.commit()
        seed_history(live.wiki())
        browser = launch(playwright)
        # Each width is a fresh context: the layout state is per browser and per width.
        for width in WIDTHS:
            context = browser.new_context(viewport={"width": width, "height": HEIGHT})
            context.set_default_timeout(15000)
            context.add_cookies([{"name": i18n.COOKIE, "value": lang, "domain": "127.0.0.1", "path": "/"}])
            page = context.new_page()
            for name in which(page, live):
                page.wait_for_timeout(400)   # let the transitions end
                page.screenshot(path=str(out / f"{label}_{name}_{width}.png"), full_page=False)
            context.close()
        browser.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], lang=sys.argv[3] if len(sys.argv) > 3 else "es")
