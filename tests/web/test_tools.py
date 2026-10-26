"""The Tools screen: the generic tools and one closed section per domain, on the `finanzas-argentinas`
demo (the finance domain is active) and on the `fairy-tales` demo (no domain is active)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domain.extensions.finance_argentina import NODES
from services import chat
from services.wiki import open_wiki
from tests.web.conftest import SPANISH, WIKI_ID
from web import i18n
from web.app import create_app
from web.settings import WebSettings

ROOT = Path(__file__).resolve().parents[2]
URL = f"/w/{WIKI_ID}/tools"
FINANCE_TOOLS = ("estimar_alternativas", "tasa_plazo_fijo", "tasa_caucion", "cotizacion_dolar",
                 "precio_accion", "rendimiento_fci")
GENERIC_TOOLS = ("read_wiki_page", "search_wiki_fts", "search_source_chunks", "query_dataset")


def _client_for(example: str, tmp_path: Path, cookies: dict = SPANISH) -> TestClient:
    """A client whose only wiki is a copy of `examples/<example>`, under the id `tales`."""
    home = tmp_path / "home"
    shutil.copytree(ROOT / "examples" / example, home / WIKI_ID)
    settings = WebSettings(
        wiki_path=None, wiki_home=home, recent_file=tmp_path / "recent_wikis.json",
        llm_base_url="http://llm.invalid/v1", llm_api_key="key", llm_model="model",
        ingest_base_url="http://llm.invalid/v1", ingest_api_key="key", ingest_model="model",
    )
    return TestClient(create_app(settings), follow_redirects=False, cookies=cookies)


@pytest.fixture
def finance(tmp_path):
    return _client_for("finanzas-argentinas", tmp_path)


@pytest.fixture
def tales(tmp_path):
    return _client_for("fairy-tales", tmp_path)


def _section(html: str, name: str) -> str:
    """The `<details>` element of the domain `name`."""
    match = re.search(rf'<details class="domain[^"]*">\s*<summary>\s*<span class="domain-name mono">{name}</span>.*?</details>\s*(?=<details class="domain|</main>)',
                      html, re.DOTALL)
    assert match, f"no section for {name}"
    return match.group(0)


def test_the_tab_is_in_the_nav_between_data_and_history_with_its_spanish_label(finance):
    html = finance.get(URL).text
    assert f'href="{URL}" aria-current="page"' in html and ">Herramientas</a>" in html
    assert html.index(f'href="/w/{WIKI_ID}/data"') < html.index(f'href="{URL}"') < html.index(f'href="/w/{WIKI_ID}/history"')


def test_the_tab_and_the_sections_in_english(tmp_path):
    client = _client_for("finanzas-argentinas", tmp_path, cookies={i18n.COOKIE: "en"})
    html = client.get(URL).text
    assert f'href="{URL}" aria-current="page"' in html and ">Tools</a>" in html
    assert "6 of 6 active" in html and "Calculation functions" in html


def test_finance_wiki_lists_the_generic_tools_above_the_domain_sections(finance):
    html = finance.get(URL).text
    for name in GENERIC_TOOLS:
        assert f'aria-label="{name}"' in html
    assert html.index('aria-label="read_wiki_page"') < html.index('<details class="domain')


def test_finance_wiki_shows_the_finance_domain_active_and_closed(finance):
    html = finance.get(URL).text
    section = _section(html, "finance_argentina")
    assert section.startswith('<details class="domain domain-active">')   # not open: no `open` attribute
    assert "6 de 6 activas" in section
    for name in FINANCE_TOOLS:
        assert f'aria-label="{name}"' in section
    assert "Activa" in section and "Inactiva" not in section


def test_finance_wiki_shows_node_role_arguments_and_allowed_values(finance):
    section = _section(finance.get(URL).text, "finance_argentina")
    for name in FINANCE_TOOLS:
        assert NODES[name] in section
    assert "Evidencia" in section and "Textual" in section
    assert "Banco Nación" in section and "plazo_dias" in section      # allowed values of tasa_plazo_fijo


def test_the_domain_section_holds_the_source_of_its_calculations(finance):
    section = _section(finance.get(URL).text, "finance_argentina")
    assert "def tea(" in section and "def projected_gain(" in section


def test_query_dataset_is_inactive_because_the_data_tools_replace_it(finance):
    html = finance.get(URL).text
    card = re.search(r'<article class="tool inactive" aria-label="query_dataset">.*?</article>', html, re.DOTALL)
    assert card and "reemplazan" in card.group(0)


def test_fairy_tales_wiki_has_no_active_domain(tales):
    response = tales.get(URL)
    assert response.status_code == 200
    section = _section(response.text, "finance_argentina")
    assert section.startswith('<details class="domain">')                # no `domain-active` class
    assert "0 de 6 activas" in section and "Activa" not in section.replace("Inactiva", "")
    assert "Ninguna categoría de datos del dominio cumple sus requisitos." in section
    assert "La categoría de datos de la herramienta no cumple sus requisitos." in section


def test_fairy_tales_wiki_still_lists_the_generic_tools(tales):
    html = tales.get(URL).text
    for name in GENERIC_TOOLS:
        assert f'aria-label="{name}"' in html
    card = re.search(r'<article class="tool inactive" aria-label="query_dataset">.*?</article>', html, re.DOTALL)
    assert card and "no tiene archivos de datos" in card.group(0)


def test_the_tools_listed_are_the_tools_the_agents_register(tmp_path):
    """The tab and the chat agents describe the same tools."""
    shutil.copytree(ROOT / "examples" / "finanzas-argentinas", tmp_path / "w")
    agents = chat.build_agents(open_wiki(tmp_path / "w"), base_url="http://localhost:1/v1", api_key="k", model="m")
    registered = set(agents.agent._function_toolset.tools) - {"read_wiki_page", "search_wiki_fts", "search_source_chunks"}
    assert registered == set(FINANCE_TOOLS)


def test_every_tool_of_the_domain_traces_the_node_it_declares():
    import inspect

    from domain.extensions.finance_argentina import agent_tool, data_tools
    for name, node in NODES.items():
        module = agent_tool if name == "estimar_alternativas" else data_tools
        assert f'tracing.mark("{node}"' in inspect.getsource(getattr(module, name))


def test_the_domain_section_shows_what_each_category_requires_and_what_its_dataset_declares(finance):
    section = _section(finance.get(URL).text, "finance_argentina")
    assert "Requisitos de los datos" in section
    row = re.search(r'<tr aria-label="plazo_fijo">.*?</tr>', section, re.DOTALL)
    assert row, "no requirements row for plazo_fijo"
    assert "TNA" in row.group(0) and "metodo_calculo" in row.group(0)
    assert "interes_simple_vencimiento" in row.group(0) and "Cumple" in row.group(0)


def test_the_domain_section_holds_the_text_of_requirements_md(finance):
    section = _section(finance.get(URL).text, "finance_argentina")
    assert "<summary class=\"mono\">requirements.md</summary>" in section
    assert "# Finance requirements" in section


def test_a_category_whose_dataset_lacks_an_attribute_shows_it_missing(tmp_path):
    client = _client_for("finanzas-argentinas", tmp_path)
    dataset = tmp_path / "home" / WIKI_ID / "datasets" / "caucion.md"
    dataset.write_text(re.sub(r"^metodo_calculo:.*\n", "", dataset.read_text(encoding="utf-8"), flags=re.MULTILINE),
                       encoding="utf-8")
    section = _section(client.get(URL).text, "finance_argentina")
    row = re.search(r'<tr aria-label="caucion">.*?</tr>', section, re.DOTALL)
    assert row and "<em>falta</em>" in row.group(0) and "No cumple" in row.group(0)
