"""The chat service returns the passages the model read, in the three modes."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic_ai import AgentRunResultEvent
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.usage import RunUsage

from domain.chat.guardrail import REFUSAL_EN
from domain.tools.wiki_fs import create_page
from services import chat
from services.wiki import open_wiki, save_page

BODY = "Cinderella lost a glass slipper at the royal ball and the prince searched the kingdom. " * 3


@pytest.fixture
def wiki(tmp_path):
    wiki = open_wiki(tmp_path / "tales")
    create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "cinderella", "Cinderella", BODY, [])
    save_page(wiki, "concepts/cinderella", BODY, None)    # builds the chunks the search reads
    return wiki


def msg(role, content):
    return SimpleNamespace(role=role, content=content)


def tool_messages():
    part = ToolReturnPart(tool_name="read_wiki_page", tool_call_id="c",
                          content="[wiki page: wiki/concepts/cinderella.md]\n\n" + BODY)
    table = ToolReturnPart(tool_name="estimar_alternativas", tool_call_id="d",
                           content="| opción | tasa |\n|---|---|\n| a | 1 |")
    return [ModelRequest(parts=[part, table])]


class Agent:
    def __init__(self, output, messages=(), usage=(0, 0)):
        self.output, self.messages = output, list(messages)
        self.usage = RunUsage(input_tokens=usage[0], output_tokens=usage[1])

    async def run(self, prompt, deps=None, message_history=None):
        return SimpleNamespace(output=self.output, all_messages=lambda: self.messages,
                               usage=lambda: self.usage)

    @asynccontextmanager
    async def run_stream_events(self, prompt, deps, message_history):
        """The events of a run: the text in chunks, then the result of the run."""
        async def events():
            first, *rest = [self.output[:5], self.output[5:]]
            yield PartStartEvent(index=0, part=TextPart(first))
            for chunk in rest:
                yield PartDeltaEvent(index=0, delta=TextPartDelta(chunk))
            yield AgentRunResultEvent(SimpleNamespace(output=self.output, all_messages=lambda: self.messages,
                                                      usage=lambda: self.usage))

        yield events()


def agents(output, messages=(), usage=(0, 0), condenser=None):
    config = SimpleNamespace(language="en", off_limits=[], data_aliases={}, system_prompt="",
                             suggested_prompts=[])
    return chat.ChatAgents(config=config, agent=Agent(output, messages, usage),
                           agent_pre_retrieval=Agent(output, messages, usage), condenser=condenser)


def run(coro):
    return asyncio.run(coro)


def test_pre_retrieval_context_holds_the_pages_injected_in_the_prompt(wiki, monkeypatch):
    monkeypatch.delenv("WIKI_TRACE", raising=False)
    result = run(chat.chat_turn_with_context(
        wiki, agents("Cinderella lost a slipper."), [msg("user", "Who is Cinderella?")],
        mode=chat.PRE_RETRIEVAL))
    assert result.answer.startswith("Cinderella lost a slipper.\n\nReference: undetermined.")
    assert [p.page for p in result.context] == ["/wiki/concepts/cinderella.md"]
    assert "glass slipper" in result.context[0].text and result.context[0].tool is None


def test_pre_retrieval_context_adds_the_tool_returns_of_the_run(wiki):
    result = run(chat.chat_turn_with_context(
        wiki, agents("Cinderella lost a slipper.", tool_messages()), [msg("user", "Who is Cinderella?")],
        mode=chat.PRE_RETRIEVAL))
    assert [p.tool for p in result.context] == [None, "read_wiki_page", "estimar_alternativas"]
    assert result.generated == ("| opción | tasa |\n|---|---|\n| a | 1 |",)


def test_a_refusal_made_before_the_model_has_an_empty_context(wiki):
    result = run(chat.chat_turn_with_context(
        wiki, agents("should not run"), [msg("user", "Will it rain tomorrow in Rosario?")],
        mode=chat.PRE_RETRIEVAL))
    assert result.answer.startswith("I received no information from the wiki related")
    assert result.context == () and result.generated == ()


def test_strict_context_comes_from_the_tool_returns(wiki):
    result = run(chat.chat_turn_with_context(
        wiki, agents("Cinderella lost a slipper.", tool_messages()), [msg("user", "Who is Cinderella?")],
        mode=chat.STRICT))
    assert [(p.page, p.tool) for p in result.context] == [
        ("/wiki/concepts/cinderella.md", "read_wiki_page"), ("estimar_alternativas", "estimar_alternativas")]
    assert result.answer.startswith("Cinderella lost a slipper.") and "| opción" in result.answer


def test_a_strict_refusal_has_an_empty_context(wiki):
    result = run(chat.chat_turn_with_context(
        wiki, agents("Paris."), [msg("user", "capital of France?")], mode=chat.STRICT))
    assert result.answer == REFUSAL_EN and result.context == ()


def test_a_strict_refusal_keeps_what_the_model_wrote(wiki):
    """The guardrail replaces an answer no tool supports; the model's text stays in
    `raw_answer`, for the user to audit (the "unverified answer" button)."""
    result = run(chat.chat_turn_with_context(
        wiki, agents("Paris."), [msg("user", "capital of France?")], mode=chat.STRICT))
    assert result.raw_answer == "Paris."


def test_an_answer_shown_as_written_has_no_raw_answer(wiki):
    result = run(chat.chat_turn_with_context(
        wiki, agents("Cinderella lost a slipper.", tool_messages()), [msg("user", "Who is Cinderella?")],
        mode=chat.STRICT))
    assert result.raw_answer is None


def test_the_guardrail_says_what_the_system_did():
    """pil-01, pil-04, pil-06: "Eso no está en mi base de conocimiento." said the wiki lacks
    a datum it held. The refusal now says what the system could not do."""
    from domain.chat.guardrail import REFUSAL_ES

    assert REFUSAL_ES == "No pude respaldar esta respuesta con una consulta a la wiki en este turno."
    assert "base de conocimiento" not in REFUSAL_EN.lower() and "knowledge base" not in REFUSAL_EN.lower()


def test_streaming_yields_the_chunks_then_the_result(wiki):
    async def collect():
        return [item async for item in chat.chat_turn_stream_with_context(
            wiki, agents("Cinderella lost a slipper.", tool_messages()), [msg("user", "Who is Cinderella?")])]

    items = run(collect())
    assert items[:-1] == ["Cinde", "rella lost a slipper."]
    last = items[-1]
    assert isinstance(last, chat.ChatResult) and last.answer == "Cinderella lost a slipper."
    assert last.context[0].page == "/wiki/concepts/cinderella.md"


def test_the_existing_functions_keep_their_results(wiki):
    question = [msg("user", "Who is Cinderella?")]
    answer = run(chat.chat_turn(wiki, agents("Cinderella lost a slipper."), question, mode=chat.PRE_RETRIEVAL))
    assert answer.startswith("Cinderella lost a slipper.")

    async def chunks():
        return [c async for c in chat.chat_turn_stream(wiki, agents("Cinderella lost a slipper."), question)]

    assert run(chunks()) == ["Cinde", "rella lost a slipper."]


def test_pre_retrieval_sums_the_condensation_and_the_answer(wiki):
    """The turn made two model calls: the condensation (Q5b) and the answer."""
    condenser = Agent("Who is Cinderella?", usage=(100, 5))
    history = [msg("user", "Who is Cinderella?"), msg("assistant", "A girl."), msg("user", "And what about her?")]
    result = run(chat.chat_turn_with_context(
        wiki, agents("Cinderella lost a slipper.", usage=(200, 20), condenser=condenser), history,
        mode=chat.PRE_RETRIEVAL))
    assert result.answer.startswith("Cinderella lost a slipper.\n\nReference: undetermined.")
    assert (result.input_tokens, result.output_tokens) == (300, 25)


def test_a_strict_run_reports_its_usage(wiki):
    result = run(chat.chat_turn_with_context(
        wiki, agents("Cinderella lost a slipper.", tool_messages(), usage=(900, 40)),
        [msg("user", "Who is Cinderella?")], mode=chat.STRICT))
    assert (result.input_tokens, result.output_tokens) == (900, 40)


def test_a_refusal_before_the_model_counts_zero_tokens(wiki):
    result = run(chat.chat_turn_with_context(
        wiki, agents("should not run", usage=(7, 7)), [msg("user", "Will it rain tomorrow in Rosario?")],
        mode=chat.PRE_RETRIEVAL))
    assert (result.input_tokens, result.output_tokens) == (0, 0)


def test_streaming_reports_the_usage_of_the_run(wiki):
    async def collect():
        return [item async for item in chat.chat_turn_stream_with_context(
            wiki, agents("Cinderella lost a slipper.", usage=(120, 9)), [msg("user", "Who is Cinderella?")])]

    last = run(collect())[-1]
    assert (last.input_tokens, last.output_tokens) == (120, 9)
