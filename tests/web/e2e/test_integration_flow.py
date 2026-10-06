"""Flows that join two branches: the relations graph and the text formats."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from domain.tools.db import get_connection
from tests.web.e2e.test_flows import WIKI_ID, click_node, relations


@pytest.mark.parametrize("name, kind, text, selector, shown", [
    ("notas.md", "md", "# Notas de campo\n\nUn **texto** cualquiera.\n", ".prose h1", "Notas de campo"),
    ("memo.txt", "txt", "Un memo de texto plano.\n", "pre, .prose", "Un memo de texto plano."),
])
def test_a_text_source_is_a_node_and_a_click_opens_its_view(page, live, name, kind, text, selector, shown):
    wiki = live.wiki()
    (wiki.sources_dir / name).write_text(text, encoding="utf-8")
    with get_connection(wiki.db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, user_id, filename, title, relative_path, path, source_kind, file_type, "
            "status, page_count, parser) VALUES (?, 'local', ?, ?, ?, 'sources/', 'source', ?, 'ready', 1, 'text:utf-8')",
            (f"txt-{kind}", name, name, f"sources/{name}", kind))
        conn.commit()

    relations(page, live)
    page.get_by_label("Mostrar fuentes").check()
    page.wait_for_function("document.getElementById('relations').dataset.settled === '1'")
    page.wait_for_timeout(600)
    with page.context.expect_page() as opened:
        click_node(page, f"source:{name}")
    view = opened.value
    view.wait_for_load_state()
    assert view.url.endswith(f"/w/{WIKI_ID}/view/{name}")
    expect(view.locator(selector).first).to_contain_text(shown)
