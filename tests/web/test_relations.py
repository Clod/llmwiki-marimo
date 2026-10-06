"""The relations screen: its route, its data, the tab and the reading link."""

from __future__ import annotations

import time
from pathlib import Path

from domain.tools.db import get_connection
from tests.web.conftest import WIKI_ID


def test_the_screen_renders_with_its_controls_and_tooltips(client):
    r = client.get(f"/w/{WIKI_ID}/relations")
    assert r.status_code == 200
    html = r.text
    for text in ("Mostrar resúmenes", "Mostrar fuentes", "Concepto", "Resumen", "Fuente"):
        assert text in html
    assert 'id="show-source"' in html and 'id="show-source" checked' not in html   # sources hidden by default
    assert 'id="show-summary" checked' in html
    assert f'data-url="/w/{WIKI_ID}/relations.json"' in html
    assert "/static/js/force-graph.min.js" in html and "https://cdn" not in html
    assert 'aria-current="page"' in html


def test_the_graph_has_zoom_in_zoom_out_and_fit_buttons(client):
    html = client.get(f"/w/{WIKI_ID}/relations").text
    for button_id, label in (("zoom-in", "Acercar"), ("zoom-out", "Alejar")):
        assert f'id="{button_id}" aria-label="{label}"' in html
    assert 'id="zoom-fit"' in html and ">Encuadrar</button>" in html
    # They call the graph library's own zoom, not a CSS transform.
    script = (Path(__file__).resolve().parents[2] / "web" / "static" / "js" / "relations.js").read_text()
    assert "fg.zoom(fg.zoom() * 1.5" in script and "fg.zoom(fg.zoom() / 1.5" in script and "fg.zoomToFit(" in script


def test_the_json_is_the_graph_of_the_wiki(client):
    data = client.get(f"/w/{WIKI_ID}/relations.json").json()
    nodes = {n["id"]: n for n in data["nodes"]}
    assert set(nodes) == {"concepts/cinderella", "concepts/prince", "summaries/tale"}
    assert nodes["concepts/cinderella"]["degree"] == 2
    assert sorted((e["source"], e["target"], e["type"]) for e in data["edges"]) == [
        ("concepts/cinderella", "concepts/prince", "links_to"),
        ("concepts/cinderella", "summaries/tale", "links_to")]


def test_the_header_has_the_third_tab_after_leer_and_ingestar(client):
    html = client.get(f"/w/{WIKI_ID}/relations").text
    nav = html[html.index('<nav class="tabs"'):html.index("</nav>")]
    assert nav.index(">Leer<") < nav.index(">Ingestar<") < nav.index(">Ver relaciones<")
    assert f'href="/w/{WIKI_ID}/relations"' in nav and 'title="Ver el grafo' in nav
    # every other screen carries the tab, not marked as the current one
    other = client.get(f"/w/{WIKI_ID}/ingest").text
    assert ">Ver relaciones<" in other


def test_the_local_graph_screen_names_the_page_and_the_depth(client):
    html = client.get(f"/w/{WIKI_ID}/relations?page=concepts/prince&depth=2").text
    assert 'data-page="concepts/prince"' in html and 'data-depth="2"' in html
    assert "Relaciones de «Prince»" in html
    assert 'data-depth="2"' in client.get(f"/w/{WIKI_ID}/relations?page=concepts/prince&depth=9").text
    assert 'data-depth="1"' in client.get(f"/w/{WIKI_ID}/relations?page=concepts/prince&depth=0").text


def test_the_local_graph_of_a_missing_page_is_a_404(client):
    assert client.get(f"/w/{WIKI_ID}/relations?page=concepts/nope").status_code == 404
    assert client.get(f"/w/{WIKI_ID}/relations?page=overview").status_code == 404


def test_an_unknown_wiki_is_a_404(client):
    assert client.get("/w/nope/relations").status_code == 404
    assert client.get("/w/nope/relations.json").status_code == 404


def test_the_reading_screen_links_to_the_local_graph_but_not_for_fixed_pages(client):
    html = client.get(f"/w/{WIKI_ID}/pages/concepts/prince").text
    assert f'href="/w/{WIKI_ID}/relations?page=concepts/prince&depth=1"' in html
    assert html.index(">Editar<") < html.index(">Relaciones<")
    assert ">Relaciones<" not in client.get(f"/w/{WIKI_ID}/pages/overview").text


def test_a_graph_of_1000_nodes_is_served_fast(client, wiki):
    n = 1000
    with get_connection(wiki.db_path) as conn:
        conn.executemany(
            "INSERT INTO documents (id, user_id, filename, title, relative_path, path, source_kind, file_type, status) "
            "VALUES (?, 'local', ?, ?, ?, '/wiki/concepts/', 'wiki', 'md', 'ready')",
            [(f"p{i}", f"p{i}.md", f"Page {i}", f"wiki/concepts/p{i}.md") for i in range(n)])
        conn.executemany(
            "INSERT INTO document_references (source_document_id, target_document_id, reference_type) "
            "VALUES (?, ?, 'links_to')",
            [(f"p{i}", f"p{(i + 2) % n}") for i in range(n)] + [(f"p{i}", f"p{(i + 1) % n}") for i in range(n)])
        conn.commit()
    start = time.perf_counter()
    r = client.get(f"/w/{WIKI_ID}/relations.json")
    elapsed = time.perf_counter() - start
    data = r.json()
    assert len(data["nodes"]) >= n and len(data["edges"]) >= n
    assert elapsed < 1.0, f"{elapsed:.2f}s"
