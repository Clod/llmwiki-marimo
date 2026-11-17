"""The dataset tables the model received, folded below the chat answer.

The turn is simulated: `chat_turn_with_context` returns an answer with the passages
a data tool returned or pre-retrieval injected. No model runs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from domain.chat.dataset_tools import format_rows_as_table
from domain.datasets.models import DatasetRow
from domain.verify.models import ContextPassage
from services import chat as chat_service
from tests.web.conftest import WIKI_ID
from tests.web.test_chat import stream
from tests.web.test_verify import ask_verified
from web.render import data_tables_html
from web.routes.chat import _data_tables

ANSWER = "Banco Nación paga una TNA del 29 % a 30 días."


def _rows(*banks: str) -> list[DatasetRow]:
    from datetime import date
    return [DatasetRow(categoria="plazo_fijo", clave=bank, metrica="tna", valor=29.0, unidad="%", dims={"plazo": "30"},
                       as_of=date(2026, 11, 14), fuente="bcra.gob.ar") for bank in banks]


TOOL_TABLE = format_rows_as_table("plazo_fijo", _rows("Banco Nación", "Banco Galicia"))
TOOL = ContextPassage("datasets/plazo_fijo.md", TOOL_TABLE, "tasa_plazo_fijo")
INJECTED = ContextPassage("datasets/caucion.md", format_rows_as_table("caucion", _rows("BYMA")))
KEY_NAMES = ContextPassage("datasets/plazo_fijo.md", "Valores de `entidad` con datos en este dataset:\n- Banco Nación")
ALTERNATIVAS = ContextPassage("estimar_alternativas", "| instrumento | rendimiento |\n|---|---|\n| Plazo fijo | 29 % |",
                              "estimar_alternativas")
PAGE = ContextPassage("/wiki/concepts/cinderella.md", "Cinderella lost a glass slipper.", "read_wiki_page")


@pytest.fixture
def turn(monkeypatch):
    """The passages of the next simulated turn; a test sets `turn.context`."""
    state = SimpleNamespace(context=())

    async def answered(wiki, agents, conversation, **kwargs):
        return SimpleNamespace(answer=ANSWER, context=state.context, figures=None, raw_answer=None,
                               path=(), input_tokens=0, output_tokens=0)

    monkeypatch.setattr(chat_service, "chat_turn_with_context", answered)
    return state


def _ask(client, turn, context, mode="pre-retrieval"):
    turn.context = tuple(context)
    return stream(client, ask_verified(client, mode=mode, verify=""))[0][1]


def test_a_data_tool_table_is_folded_below_the_prose(client, turn):
    html = _ask(client, turn, [PAGE, TOOL], mode="agent")
    assert html.index(ANSWER) < html.index('<details class="data-tables">')
    assert "<summary>Datos que recibió el modelo (2 filas)</summary>" in html
    assert "<td>Banco Galicia</td>" in html and "datasets/plazo_fijo.md" in html
    assert "dataset row(s)" not in html


def test_the_rows_injected_by_pre_retrieval_are_shown(client, turn):
    html = _ask(client, turn, [INJECTED])
    assert "<summary>Datos que recibió el modelo (1 fila)</summary>" in html and "<td>BYMA</td>" in html


def test_an_answer_with_no_data_is_not_changed(client, turn):
    assert "data-tables" not in _ask(client, turn, [PAGE])


def test_the_advisory_table_and_a_list_of_key_names_are_not_folded():
    assert data_tables_html([ALTERNATIVAS, KEY_NAMES]) == ""


def test_a_table_that_came_twice_is_shown_once():
    assert data_tables_html([TOOL, TOOL]).count("<table>") == 1


def test_the_title_is_in_the_language_of_the_interface(settings, agents, turn):
    from web.app import create_app
    client = TestClient(create_app(settings, agents_factory=lambda wiki: agents))
    assert "<summary>Data the model received (2 rows)</summary>" in _ask(client, turn, [TOOL])


def test_the_stored_answer_holds_no_table(client, app, turn):
    _ask(client, turn, [TOOL])
    assert app.state.web.conversations[(WIKI_ID, "c1")][-1].content == ANSWER


def test_the_reloaded_thread_shows_the_same_tables(client, turn):
    _ask(client, turn, [TOOL])
    html = client.get(f"/w/{WIKI_ID}/chat/c1/thread").text
    assert "<summary>Datos que recibió el modelo (2 filas)</summary>" in html


def test_the_streaming_mode_shows_no_table():
    assert _data_tables(chat_service.STREAMING, [TOOL]) == ""
    assert _data_tables(chat_service.AGENT, [TOOL]) != ""
