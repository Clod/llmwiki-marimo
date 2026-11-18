"""The radio group «Comprobar que las páginas responden la pregunta» of the chat form, and
the choice it sends to the pre-retrieval turn. The turn is simulated: the test records the
arguments of `chat_turn_with_context`. No model runs."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from services import chat as chat_service
from tests.web.conftest import WIKI_ID
from tests.web.test_chat import stream


@pytest.fixture
def received(monkeypatch):
    """The keyword arguments of each simulated turn."""
    calls: list[dict] = []

    async def answered(wiki, agents, conversation, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(answer="Va al baile.", context=(), figures=None, raw_answer=None,
                               path=(), input_tokens=0, output_tokens=0)

    monkeypatch.setattr(chat_service, "chat_turn_with_context", answered)
    return calls


def _ask(client, mode, relevance):
    response = client.post(f"/w/{WIKI_ID}/chat", data={
        "message": "¿En qué se convertían los lacayos?", "mode": mode, "conversation_id": "c1",
        "open_page": "", "relevance": relevance})
    stream(client, re.search(r'sse-connect="/w/tales/chat/(\w+)/stream"', response.text).group(1))


def test_the_form_offers_the_checks_with_no_check_chosen_and_jev_disabled(client):
    page = client.get(f"/w/{WIKI_ID}/pages/overview").text
    assert "Comprobar que las páginas responden la pregunta" in page
    assert '<input type="radio" name="relevance" value="none" checked>Ninguna' in page
    assert '<input type="radio" name="relevance" value="evidence">Cita de evidencia' in page
    assert '<input type="radio" name="relevance" value="judge">Juez' in page
    assert '<input type="radio" name="relevance" value="jev" disabled>Jev — sin implementar' in page
    assert "Alternativa: un modelo de relevancia de TypeSafe juzga cada fragmento. Falta implementarla" in page


@pytest.mark.parametrize("choice", ["none", "evidence", "judge"])
def test_the_pre_retrieval_turn_receives_the_choice(client, received, choice):
    _ask(client, "pre-retrieval", choice)
    assert received[-1]["relevance_check"] == choice


@pytest.mark.parametrize(("mode", "choice"), [("agent", "judge"), ("pre-retrieval", "jev")])
def test_another_mode_or_an_unknown_value_is_no_check(client, received, mode, choice):
    _ask(client, mode, choice)
    assert received[-1]["relevance_check"] == "none"
