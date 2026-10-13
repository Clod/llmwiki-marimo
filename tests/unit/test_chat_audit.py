"""The path of a turn kept in memory (`tracing.collect`, `ChatResult.path`), the matched terms of
the coverage roster check (Q5, Q5b). The short texts of the nodes are tested in
tests/web/test_audit.py: they live in web/, which needs the `web` group."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic_ai import AgentRunResultEvent
from pydantic_ai.messages import ModelRequest, PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from pydantic_ai.messages import ToolReturnPart

from domain import tracing
from domain.chat.scope import matched_terms, mentions_known_data
from domain.tools.wiki_fs import create_page
from services import chat
from services.wiki import open_wiki, save_page

BODY = "Cinderella lost a glass slipper at the royal ball and the prince searched the kingdom. " * 3


# ── tracing.collect ──────────────────────────────────────────────────────────

def test_collect_records_nodes_and_attributes_with_tracing_off(monkeypatch, tmp_path):
    monkeypatch.delenv(tracing.ENV_VAR, raising=False)
    with tracing.collect() as path:
        with tracing.root("turn", tmp_path):
            tracing.mark("Q3", tracing.READING, answer=False)
            with tracing.node("Q6", tracing.READING, fts_query="a OR b") as q6:
                q6.set(pages=["x"], skipped=None)
    assert path == [
        {"node": "Q3", "diagram": "reading", "attributes": {"answer": False}},
        {"node": "Q6", "diagram": "reading", "attributes": {"fts_query": "a OR b", "pages": ["x"]}},
    ]
    assert not (tmp_path / ".llmwiki").exists()


def test_nothing_is_collected_outside_collect(monkeypatch):
    monkeypatch.delenv(tracing.ENV_VAR, raising=False)
    tracing.mark("Q3", tracing.READING, answer=False)     # no error, nothing kept
    with tracing.collect() as path:
        pass
    assert path == []


def test_payload_adds_only_the_ref_when_a_file_was_written(monkeypatch, tmp_path):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    with tracing.collect() as path:
        with tracing.root("turn", tmp_path):
            with tracing.node("I4", tracing.WRITING) as n:
                n.payload("text", "hello", channel="extracted_text")
    attributes = path[0]["attributes"]
    assert list(attributes) == ["text.ref"] and attributes["text.ref"].startswith("payloads/")
    assert "hello" not in json.dumps(path)


def test_payload_with_tracing_off_records_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv(tracing.ENV_VAR, raising=False)
    with tracing.collect() as path:
        with tracing.node("I4", tracing.WRITING) as n:
            n.payload("text", "hello", channel="extracted_text")
    assert path[0]["attributes"] == {}


# ── matched_terms ────────────────────────────────────────────────────────────

def test_matched_terms_returns_the_roster_terms_by_stem_and_exact():
    found = matched_terms("¿Cuánto rinden los plazos fijos en GGAL?", ["plazo_fijo", "caucion"], ["billete verde"],
                          language="es", exact=["GGAL", "AAPL"])
    assert found == ["GGAL", "plazo_fijo"]


def test_matched_terms_returns_the_alias_that_matched():
    assert matched_terms("el billete verde hoy", ["dolar"], ["billete verde"], language="es") == ["billete verde"]


def test_matched_terms_without_language_compares_whole_words():
    assert matched_terms("dollar mep today", ["mep", "cer"]) == ["mep"]
    assert matched_terms("nothing here", ["mep"]) == []


def test_mentions_known_data_is_the_boolean_of_matched_terms():
    assert mentions_known_data("plazos fijos", ["plazo_fijo"], language="es") is True
    assert mentions_known_data("hola", ["plazo_fijo"], language="es") is False


# ── ChatResult.path in the three modes; Q5 matched ───────────────────────────

@pytest.fixture
def wiki(tmp_path):
    wiki = open_wiki(tmp_path / "tales")
    create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "cinderella", "Cinderella", BODY, [])
    save_page(wiki, "concepts/cinderella", BODY, None)
    return wiki


def msg(role, content):
    return SimpleNamespace(role=role, content=content)


class Agent:
    def __init__(self, output, messages=()):
        self.output, self.messages = output, list(messages)

    async def run(self, prompt, deps, message_history):
        return SimpleNamespace(output=self.output, all_messages=lambda: self.messages)

    @asynccontextmanager
    async def run_stream_events(self, prompt, deps, message_history):
        async def events():
            yield PartStartEvent(index=0, part=TextPart(self.output[:5]))
            yield PartDeltaEvent(index=0, delta=TextPartDelta(self.output[5:]))
            yield AgentRunResultEvent(SimpleNamespace(output=self.output, all_messages=lambda: self.messages))

        yield events()


def agents(output, messages=(), condenser=None):
    config = SimpleNamespace(language="en", off_limits=[], data_aliases={}, system_prompt="", suggested_prompts=[])
    return chat.ChatAgents(config=config, agent=Agent(output, messages),
                           agent_pre_retrieval=Agent(output, messages), condenser=condenser)


def grounded():
    part = ToolReturnPart(tool_name="read_wiki_page", tool_call_id="c",
                          content="[wiki page: wiki/concepts/cinderella.md]\n\n" + BODY)
    return [ModelRequest(parts=[part])]


def ids(result):
    return [step["node"] for step in result.path]


def run(coro):
    return asyncio.run(coro)


def test_pre_retrieval_returns_its_path_and_the_matched_terms(wiki, monkeypatch):
    monkeypatch.delenv(tracing.ENV_VAR, raising=False)
    result = run(chat.chat_turn_with_context(
        wiki, agents("Cinderella lost a slipper."), [msg("user", "Who is Cinderella?")], mode=chat.PRE_RETRIEVAL))
    assert ids(result)[:2] == ["C1", "C2"] and "Q5" in ids(result) and "Q6" in ids(result)
    q5 = next(s for s in result.path if s["node"] == "Q5")
    assert q5["attributes"]["answer"] is True and q5["attributes"]["matched"] == ["Cinderella"]
    assert not (wiki.path / ".llmwiki" / "traces").exists()


def test_pre_retrieval_records_an_empty_match(wiki):
    result = run(chat.chat_turn_with_context(
        wiki, agents("x"), [msg("user", "Will it rain tomorrow in Rosario?")], mode=chat.PRE_RETRIEVAL))
    q5 = next(s for s in result.path if s["node"] == "Q5")
    assert q5["attributes"]["answer"] is False and q5["attributes"]["matched"] == []
    assert "R2a" in ids(result)


def test_pre_retrieval_q5b_records_the_matched_terms_of_the_rewrite(wiki):
    history = [msg("user", "Who is Cinderella?"), msg("assistant", "A girl."), msg("user", "And then?")]
    ag = agents("She left.", condenser=SimpleNamespace(
        run=lambda prompt: _async(SimpleNamespace(output="What happened to Cinderella?"))))
    result = run(chat.chat_turn_with_context(wiki, ag, history, mode=chat.PRE_RETRIEVAL))
    q5b = next(s for s in result.path if s["node"] == "Q5b")
    assert q5b["attributes"]["matched"] == ["Cinderella"]


async def _async(value):
    return value


def test_strict_returns_its_path(wiki):
    result = run(chat.chat_turn_with_context(
        wiki, agents("Cinderella lost a slipper.", grounded()), [msg("user", "Who is Cinderella?")], mode=chat.STRICT))
    assert ids(result) == ["C1", "C2", "T1", "T2", "T4", "T5"]
    refused = run(chat.chat_turn_with_context(
        wiki, agents("Paris."), [msg("user", "capital of France?")], mode=chat.STRICT))
    assert "T3" in ids(refused)


def test_streaming_returns_its_path(wiki):
    async def last():
        result = None
        async for item in chat.chat_turn_stream_with_context(
                wiki, agents("Cinderella lost a slipper."), [msg("user", "Who is Cinderella?")]):
            result = item
        return result

    assert ids(run(last())) == ["C1", "C2", "F1", "F2"]


def test_two_turns_keep_separate_paths(wiki):
    one = run(chat.chat_turn_with_context(wiki, agents("a", grounded()), [msg("user", "Who is Cinderella?")],
                                          mode=chat.STRICT))
    two = run(chat.chat_turn_with_context(wiki, agents("a", grounded()), [msg("user", "Who is Cinderella?")],
                                          mode=chat.STRICT))
    assert len(one.path) == len(two.path) == 6
