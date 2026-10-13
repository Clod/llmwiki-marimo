"""The turns, the tools and the fragment path write the records of their nodes.

The model is simulated; tracing is on, with a temporary workspace. The records
are read back from `<workspace>/.llmwiki/traces/spans.jsonl`. The node tables
are in docs/query_nodes.md and docs/ingestion_nodes.md.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic_ai import AgentRunResultEvent
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from domain import tracing
from domain.chat import dataset_tools, tools, wiki_tools
from domain.finance_argentina import agent_tool
from domain.tools.wiki_fs import create_page
from services import chat
from services.wiki import open_wiki

BODY = "Cinderella lost a glass slipper at the royal ball and the prince searched the kingdom. " * 3


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    monkeypatch.setenv(tracing.ENV_VAR, "1")
    wiki = open_wiki(tmp_path / "tales")
    create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "cinderella", "Cinderella", BODY, [])
    return wiki


def spans(wiki):
    path = wiki.path / ".llmwiki" / "traces" / tracing.SPAN_FILE
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def records(wiki):
    """The node records by node id; each value is the llmwiki.* attributes."""
    return {
        s["name"]: {k.removeprefix("llmwiki."): v for k, v in s["attributes"].items()
                    if k.startswith("llmwiki.")}
        for s in spans(wiki) if "llmwiki.node" in s["attributes"]
    }


def msg(role, content):
    return SimpleNamespace(role=role, content=content)


def evidence():
    call = ToolCallPart(tool_name="read_wiki_page", args={"path": "x"}, tool_call_id="c")
    ret = ToolReturnPart(tool_name="read_wiki_page", tool_call_id="c",
                         content="[wiki page: wiki/concepts/cinderella.md]\n\n" + BODY)
    return [ModelResponse(parts=[call]), ModelRequest(parts=[ret])]


class Agent:
    def __init__(self, output, messages=()):
        self.output, self.messages = output, list(messages)

    async def run(self, prompt, deps, message_history):
        return SimpleNamespace(output=self.output, all_messages=lambda: self.messages)

    @asynccontextmanager
    async def run_stream_events(self, prompt, deps, message_history):
        """The events of a run: the text in chunks, then the result of the run."""
        async def events():
            first, *rest = [self.output]
            yield PartStartEvent(index=0, part=TextPart(first))
            for chunk in rest:
                yield PartDeltaEvent(index=0, delta=TextPartDelta(chunk))
            yield AgentRunResultEvent(SimpleNamespace(output=self.output, all_messages=lambda: self.messages))

        yield events()


def agents(output, messages=()):
    config = SimpleNamespace(language="en", off_limits=[], data_aliases={}, system_prompt="",
                             suggested_prompts=[])
    return chat.ChatAgents(config=config, agent=Agent(output, messages),
                           agent_pre_retrieval=Agent(output, messages))


def test_a_strict_turn_writes_the_nodes_of_the_strict_mode(wiki):
    history = [msg("user", "hi"), msg("assistant", "hello"), msg("user", "Who is Cinderella?")]
    asyncio.run(chat.chat_turn_with_context(
        wiki, agents("Cinderella lost a slipper.", evidence()), history, mode=chat.STRICT))
    rec = records(wiki)
    assert rec["C1"] == {"node": "C1", "diagram": "reading", "prior_messages": 2,
                         "refused_dropped": 0, "history_messages": 2}
    assert rec["C2"]["mode"] == "strict"
    assert rec["T1"]["tools_called"] == ["read_wiki_page"]
    assert rec["T1"]["raw_output"] == "Cinderella lost a slipper."
    assert rec["T2"]["answer"] is True
    assert "T3" not in rec
    assert rec["T4"]["citation_added"] is True and rec["T4"]["table_added"] is False
    assert rec["T5"]["refusal_substituted"] is False
    assert rec["T5"]["final_answer"].startswith("Cinderella lost a slipper.")


def test_a_strict_turn_without_evidence_writes_t3(wiki):
    asyncio.run(chat.chat_turn_with_context(
        wiki, agents("Paris."), [msg("user", "capital of France?")], mode=chat.STRICT))
    rec = records(wiki)
    assert rec["T2"]["answer"] is False
    assert rec["T3"]["final_answer"] == rec["T5"]["final_answer"]
    assert rec["T5"]["refusal_substituted"] is True
    assert rec["T1"]["tools_called"] == []


def test_c1_counts_the_refused_exchanges_it_dropped(wiki):
    refused = "I cannot answer this question"
    history = [msg("user", "weather?"), msg("assistant", refused),
               msg("user", "a"), msg("assistant", "b"), msg("user", "Who is Cinderella?")]
    asyncio.run(chat.chat_turn_with_context(
        wiki, agents("Paris."), history, mode=chat.STRICT))
    c1 = records(wiki)["C1"]
    assert (c1["prior_messages"], c1["history_messages"]) == (4, c1["prior_messages"] - c1["refused_dropped"])


def test_a_streaming_turn_writes_the_nodes_of_the_streaming_mode(wiki):
    async def collect():
        return [i async for i in chat.chat_turn_stream_with_context(
            wiki, agents("Cinderella lost a slipper.", evidence()), [msg("user", "Who is Cinderella?")])]

    asyncio.run(collect())
    rec = records(wiki)
    assert rec["C1"]["prior_messages"] == 0
    assert rec["C2"]["mode"] == "streaming"
    assert rec["F1"]["tools_called"] == ["read_wiki_page"]
    assert rec["F2"]["final_answer"] == "Cinderella lost a slipper."


def test_a_pre_retrieval_turn_writes_c1_c2_and_q1(wiki):
    asyncio.run(chat.chat_turn_with_context(
        wiki, agents("Cinderella lost a slipper."), [msg("user", "Who is Cinderella?")],
        mode=chat.PRE_RETRIEVAL))
    rec = records(wiki)
    assert rec["Q1"]["question"] == "Who is Cinderella?"
    assert rec["C2"]["mode"] == "pre-retrieval"
    assert "C1" in rec


def ctx(wiki):
    return SimpleNamespace(deps=str(wiki.db_path))


def test_each_tool_writes_its_record_with_substantive(wiki):
    with tracing.root("turn", wiki.path):
        found = wiki_tools.read_wiki_page(ctx(wiki), "wiki/concepts/cinderella.md")
        assert found.startswith("[wiki page:")
        wiki_tools.search_wiki_fts(ctx(wiki), "nonexistentterm", limit=3)
        asyncio.run(tools.search_source_chunks(ctx(wiki), "nonexistentterm", limit=4))
        dataset_tools.query_dataset(ctx(wiki), "nada", clave="k")
        agent_tool.estimar_alternativas(ctx(wiki), 1000.0, 3.0)
    by_name = {s["name"]: s["attributes"] for s in spans(wiki)}
    assert by_name["K1"]["llmwiki.path"] == "wiki/concepts/cinderella.md"
    assert by_name["K1"]["llmwiki.substantive"] is True
    assert (by_name["K2"]["llmwiki.query"], by_name["K2"]["llmwiki.limit"]) == ("nonexistentterm", 3)
    assert by_name["K2"]["llmwiki.substantive"] is False
    assert by_name["K3"]["llmwiki.limit"] == 4 and by_name["K3"]["llmwiki.substantive"] is False
    assert by_name["K4"]["llmwiki.categoria"] == "nada" and by_name["K4"]["llmwiki.clave"] == "k"
    assert by_name["K4"]["llmwiki.substantive"] is False
    assert by_name["K5"]["llmwiki.monto"] == 1000.0 and by_name["K5"]["llmwiki.moneda"] == "ARS"
    assert "llmwiki.substantive" in by_name["K5"]


def test_a_rejected_path_is_recorded_too(wiki):
    with tracing.root("turn", wiki.path):
        wiki_tools.read_wiki_page(ctx(wiki), "wiki/../../.env")
    k1 = records(wiki)["K1"]
    assert k1["substantive"] is False


def test_insert_chunks_writes_w1_w2_w3(wiki):
    with tracing.root("ingest", wiki.path):
        create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "other", "Other", BODY, [])
    rec = records(wiki)
    doc_id = rec["W1"]["document_id"]
    assert rec["W2"]["document_id"] == doc_id and rec["W3"]["document_id"] == doc_id
    assert rec["W2"]["fragments"] == rec["W1"]["fragments"] == rec["W3"]["fragments"]
    assert rec["W2"]["language"] == "en"
