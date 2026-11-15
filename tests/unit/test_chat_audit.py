"""The path of a turn kept in memory (`tracing.collect`, `ChatResult.path`), the matched terms of
the coverage roster check (Q_IN_ROSTER, Q_CONDENSED). The short texts of the nodes are tested in
tests/web/test_audit.py: they live in web/, which needs the `web` group."""

import asyncio
from dataclasses import replace
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
from tests.helpers.workflow_path import steps

BODY = "Cinderella lost a glass slipper at the royal ball and the prince searched the kingdom. " * 3


# ── tracing.collect ──────────────────────────────────────────────────────────

def test_collect_records_nodes_and_attributes_with_tracing_off(monkeypatch, tmp_path):
    monkeypatch.delenv(tracing.ENV_VAR, raising=False)
    with tracing.collect() as path:
        with tracing.root("turn", tmp_path):
            tracing.mark("Q_IN_BLACKLIST", tracing.READING, answer=False)
            with tracing.node("Q_SEARCH_WIKI", tracing.READING, fts_query="a OR b") as q6:
                q6.set(pages=["x"], skipped=None)
    assert path == [
        {"node": "Q_IN_BLACKLIST", "diagram": "reading", "attributes": {"answer": False}},
        {"node": "Q_SEARCH_WIKI", "diagram": "reading", "attributes": {"fts_query": "a OR b", "pages": ["x"]}},
    ]
    assert not (tmp_path / ".llmwiki").exists()


def test_nothing_is_collected_outside_collect(monkeypatch):
    monkeypatch.delenv(tracing.ENV_VAR, raising=False)
    tracing.mark("Q_IN_BLACKLIST", tracing.READING, answer=False)     # no error, nothing kept
    with tracing.collect() as path:
        pass
    assert path == []


def test_payload_adds_only_the_ref_when_a_file_was_written(monkeypatch, tmp_path):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    with tracing.collect() as path:
        with tracing.root("turn", tmp_path):
            with tracing.node("I_EXTRACT", tracing.WRITING) as n:
                n.payload("text", "hello", channel="extracted_text")
    attributes = path[0]["attributes"]
    assert list(attributes) == ["text.ref"] and attributes["text.ref"].startswith("payloads/")
    assert "hello" not in json.dumps(path)


def test_payload_with_tracing_off_records_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv(tracing.ENV_VAR, raising=False)
    with tracing.collect() as path:
        with tracing.node("I_EXTRACT", tracing.WRITING) as n:
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


# ── ChatResult.path in the three modes; Q_IN_ROSTER matched ───────────────────────────

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
    config = SimpleNamespace(language="en", blacklist=[], data_aliases={}, system_prompt="", suggested_prompts=[])
    return chat.ChatAgents(config=config, agent=Agent(output, messages),
                           agent_pre_retrieval=Agent(output, messages), condenser=condenser)


def grounded():
    part = ToolReturnPart(tool_name="read_wiki_page", tool_call_id="c",
                          content="[wiki page: wiki/concepts/cinderella.md]\n\n" + BODY)
    return [ModelRequest(parts=[part])]


def ids(result):
    return steps(result.path)


def run(coro):
    return asyncio.run(coro)


def test_pre_retrieval_returns_its_path_and_the_matched_terms(wiki, monkeypatch):
    monkeypatch.delenv(tracing.ENV_VAR, raising=False)
    result = run(chat.chat_turn_with_context(
        wiki, agents("Cinderella lost a slipper."), [msg("user", "Who is Cinderella?")], mode=chat.PRE_RETRIEVAL))
    assert ids(result)[:2] == ["C_COUNT_HISTORY", "C_WHICH_MODE"] and "Q_IN_ROSTER" in ids(result) and "Q_SEARCH_WIKI" in ids(result)
    q5 = next(s for s in result.path if s["node"] == "Q_IN_ROSTER")
    assert q5["attributes"]["answer"] is True and q5["attributes"]["matched"] == ["Cinderella"]
    assert not (wiki.path / ".llmwiki" / "traces").exists()


def test_pre_retrieval_records_an_empty_match(wiki):
    result = run(chat.chat_turn_with_context(
        wiki, agents("x"), [msg("user", "Will it rain tomorrow in Rosario?")], mode=chat.PRE_RETRIEVAL))
    q5 = next(s for s in result.path if s["node"] == "Q_IN_ROSTER")
    assert q5["attributes"]["answer"] is False and q5["attributes"]["matched"] == []
    assert "R_NOT_IN_ROSTER" in ids(result)


def test_pre_retrieval_q5b_records_the_matched_terms_of_the_rewrite(wiki):
    history = [msg("user", "Who is Cinderella?"), msg("assistant", "A girl."), msg("user", "And then?")]
    ag = agents("She left.", condenser=SimpleNamespace(
        run=lambda prompt: _async(SimpleNamespace(output="What happened to Cinderella?"))))
    result = run(chat.chat_turn_with_context(wiki, ag, history, mode=chat.PRE_RETRIEVAL))
    q5b = next(s for s in result.path if s["node"] == "Q_CONDENSED")
    assert q5b["attributes"]["matched"] == ["Cinderella"]


async def _async(value):
    return value


def test_the_agent_mode_refuses_a_blacklisted_question_without_calling_the_model(wiki):
    ag = agents("The model would answer.")
    ag.config.blacklist = ["cedears"]

    class NoModel(Agent):
        async def run(self, prompt, deps, message_history):
            raise AssertionError("the model was called")

    ag = replace(ag, agent=NoModel("x"))
    result = run(chat.chat_turn_with_context(wiki, ag, [msg("user", "What are cedears?")], mode=chat.AGENT))
    assert ids(result)[-2:] == ["Q_IN_BLACKLIST", "R_BLACKLIST"]
    assert "cedears" in result.answer
    assert (result.input_tokens, result.output_tokens, result.context) == (0, 0, ())


def test_a_retry_with_evidence_is_shown_without_the_warning(wiki):
    """The first run has no evidence, the retry reads a wiki page: two model calls, the
    answer of the retry, no warning."""
    class SecondRunReads(Agent):
        calls = 0

        async def run(self, prompt, deps, message_history):
            SecondRunReads.calls += 1
            messages = grounded() if SecondRunReads.calls == 2 else []
            return SimpleNamespace(output=f"Answer {SecondRunReads.calls}.", all_messages=lambda: messages)

    ag = replace(agents("x"), agent=SecondRunReads("x"))
    result = run(chat.chat_turn_with_context(wiki, ag, [msg("user", "Who is Cinderella?")], mode=chat.AGENT))
    assert SecondRunReads.calls == 2
    assert result.answer.startswith("Answer 2.")
    assert "No consultation of the wiki" not in result.answer
    assert "T_RETRY" in ids(result) and "T_NO_EVIDENCE_WARNING" not in ids(result)


def test_agent_returns_its_path(wiki):
    result = run(chat.chat_turn_with_context(
        wiki, agents("Cinderella lost a slipper.", grounded()), [msg("user", "Who is Cinderella?")], mode=chat.AGENT))
    assert ids(result) == ["C_COUNT_HISTORY", "C_WHICH_MODE", "C_MARK_REFUSED", "C_DROP_TABLES", "C_TRIM_TURNS", "C_ORIGIN_LINES", "Q_IN_BLACKLIST", "C_OPEN_PAGE_NOTE", "T_AGENT", "T_TOOL_EVIDENCE", "T_ADVISORY_TABLE", "T_CITE_TOOLS", "T_MARK_UNCITED", "C_FIGURES", "T_SHOWN"]
    refused = run(chat.chat_turn_with_context(
        wiki, agents("Paris."), [msg("user", "capital of France?")], mode=chat.AGENT))
    assert ids(refused)[8:11] == ["T_AGENT", "T_RETRY", "T_TOOL_EVIDENCE"]
    assert "T_NO_EVIDENCE_WARNING" in ids(refused)


def test_streaming_returns_its_path(wiki):
    async def last():
        result = None
        async for item in chat.chat_turn_stream_with_context(
                wiki, agents("Cinderella lost a slipper."), [msg("user", "Who is Cinderella?")]):
            result = item
        return result

    assert ids(run(last())) == ["C_COUNT_HISTORY", "C_WHICH_MODE", "F_AGENT", "F_COMPLETE"]


def test_two_turns_keep_separate_paths(wiki):
    one = run(chat.chat_turn_with_context(wiki, agents("a", grounded()), [msg("user", "Who is Cinderella?")],
                                          mode=chat.AGENT))
    two = run(chat.chat_turn_with_context(wiki, agents("a", grounded()), [msg("user", "Who is Cinderella?")],
                                          mode=chat.AGENT))
    assert len(steps(one.path)) == len(steps(two.path)) == 15
