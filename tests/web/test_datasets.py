"""The Data screen: the data files of the wiki and the content of one of them, on the `tales` wiki
with the data files of the `finanzas-argentinas` demo copied in."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.web.conftest import WIKI_ID

URL = f"/w/{WIKI_ID}/data"
DEMO_DATASETS = Path(__file__).resolve().parents[2] / "examples" / "finanzas-argentinas" / "datasets"
DEMO_FILES = ("acciones", "caucion", "dolar", "fci_money_market", "plazo_fijo")


@pytest.fixture
def data_wiki(wiki):
    shutil.copytree(DEMO_DATASETS, wiki.path / "datasets")
    return wiki


def test_the_tab_is_in_the_nav_after_vocabulary_with_its_spanish_label(client, data_wiki):
    html = client.get(URL).text
    assert f'href="{URL}" aria-current="page"' in html and ">Datos</a>" in html
    assert html.index(f'href="/w/{WIKI_ID}/vocabulary"') < html.index(f'href="{URL}"') < html.index(f'href="/w/{WIKI_ID}/history"')


def test_the_tab_appears_on_the_other_screens_too(client, data_wiki):
    html = client.get(f"/w/{WIKI_ID}/vocabulary").text
    assert f'href="{URL}"' in html and ">Datos</a>" in html


def test_the_screen_lists_the_data_files_of_the_demo(client, data_wiki):
    response = client.get(URL)
    assert response.status_code == 200
    html = response.text
    assert "Archivos de datos · 5" in html
    for name in DEMO_FILES:
        assert f'href="{URL}?category={name}"' in html and f">{name}.md</a>" in html


def test_without_a_choice_the_first_file_is_shown(client, data_wiki):
    html = client.get(URL).text
    assert f'href="{URL}?category=acciones" aria-current="page"' in html
    assert '<h2 id="data-file-title" class="mono">datasets/acciones.md</h2>' in html


def test_a_chosen_file_shows_its_front_matter_and_its_table(client, data_wiki):
    html = client.get(URL, params={"category": "dolar"}).text
    assert '<h2 id="data-file-title" class="mono">datasets/dolar.md</h2>' in html
    for name, value in (("categoria", "dolar"), ("clave", "tipo"), ("metricas", "compra: ARS, venta: ARS"),
                        ("as_of", "2026-06-25"), ("fuente", "ambito.com")):
        assert f"<dt>{name}</dt><dd>{value}</dd>" in html
    assert '<section class="dataset-table" aria-label="dolar">' in html
    assert '<th scope="row">Blue</th><td class="num">1220</td><td class="num">1240</td>' in html


def test_a_matrix_file_shows_one_column_per_term(client, data_wiki):
    html = client.get(URL, params={"category": "plazo_fijo"}).text
    assert "<dt>plazos_dias</dt><dd>30, 60, 90, 180, 365</dd>" in html
    assert '<section class="dataset-table" aria-label="plazo_fijo">' in html and "<tbody>" in html


def test_an_unknown_file_is_a_404(client, data_wiki):
    assert client.get(URL, params={"category": "nada"}).status_code == 404
    assert client.get(URL, params={"category": "../wiki_config"}).status_code == 404


def test_a_wiki_without_datasets_shows_the_empty_state(client, wiki):
    response = client.get(URL)
    assert response.status_code == 200
    assert "Archivos de datos · 0" in response.text
    assert "Esta wiki no tiene archivos de datos." in response.text
    assert '<section class="dataset-table"' not in response.text


def test_the_screen_in_english(en_client, data_wiki):
    html = en_client.get(URL).text
    assert ">Data</a>" in html and "Data files · 5" in html


def test_the_screen_writes_nothing(client, data_wiki):
    before = {p: p.read_bytes() for p in (data_wiki.path / "datasets").iterdir()}
    client.get(URL, params={"category": "caucion"})
    assert {p: p.read_bytes() for p in (data_wiki.path / "datasets").iterdir()} == before
    assert client.post(URL).status_code == 405
