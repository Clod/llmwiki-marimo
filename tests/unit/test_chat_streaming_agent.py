"""The streaming mode runs the whole agent: a sentence the model writes before a tool
call does not end the turn (cue-04: "Permíteme buscar en el wiki." and the search
"Tool not executed - a final result was already processed."). Streaming means no
check, not a cut agent. A real Pydantic AI agent with a simulated model."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from domain.tools.wiki_fs import create_page
from services import chat
from services.wiki import open_wiki, save_page

BODY = "Cinderella lost a glass slipper at the royal ball and the prince searched the kingdom. " * 3


@pytest.fixture
def wiki(tmp_path):
    wiki = open_wiki(tmp_path / "tales")
    create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "cinderella", "Cinderella", BODY, [])
    save_page(wiki, "concepts/cinderella", BODY, None)
    return wiki


def _agents(stream_fn, calls):
    def read_page(ctx: RunContext[str], path: str) -> str:
        """Read a wiki page."""
        calls.append(path)
        return "[wiki page: wiki/concepts/cinderella.md]\n\n" + BODY

    agent = Agent(FunctionModel(stream_function=stream_fn), tools=[read_page], deps_type=str)
    config = SimpleNamespace(language="en", off_limits=[], data_aliases={}, system_prompt="",
                             suggested_prompts=[])
    return chat.ChatAgents(config=config, agent=agent, agent_pre_retrieval=agent)


def _collect(wiki, agents):
    async def run():
        return [item async for item in chat.chat_turn_stream_with_context(
            wiki, agents, [SimpleNamespace(role="user", content="Who lost a slipper?")])]
    return asyncio.run(run())


def test_a_sentence_before_a_tool_call_does_not_end_the_turn(wiki):
    calls = []

    async def stream_fn(messages, info: AgentInfo):
        if len(messages) == 1:                    # first model request: a sentence, then the tool
            yield "Let me search the wiki. "
            yield {0: DeltaToolCall(name="read_page", json_args='{"path": "concepts/cinderella"}')}
        else:                                     # after the tool return: the answer
            yield "Cinderella lost "
            yield "a glass slipper."

    items = _collect(wiki, _agents(stream_fn, calls))
    assert calls == ["concepts/cinderella"]                       # the tool ran
    text = "".join(i for i in items if isinstance(i, str))
    assert "Let me search the wiki." in text and "Cinderella lost a glass slipper." in text
    result = items[-1]
    assert isinstance(result, chat.ChatResult)
    assert result.answer.endswith("Cinderella lost a glass slipper.")
    assert result.context and result.context[0].page == "/wiki/concepts/cinderella.md"


def test_the_texts_of_two_model_responses_are_separated(wiki):
    async def stream_fn(messages, info: AgentInfo):
        if len(messages) == 1:
            yield "Searching."
            yield {0: DeltaToolCall(name="read_page", json_args='{"path": "x"}')}
        else:
            yield "Found it."

    items = _collect(wiki, _agents(stream_fn, []))
    assert items[-1].answer == "Searching.\n\nFound it."


def test_an_answer_without_tools_streams_as_before(wiki):
    async def stream_fn(messages, info: AgentInfo):
        yield "Cinderella "
        yield "lost a slipper."

    items = _collect(wiki, _agents(stream_fn, []))
    assert [i for i in items if isinstance(i, str)] == ["Cinderella ", "lost a slipper."]
    assert items[-1].answer == "Cinderella lost a slipper."


def test_a_streamed_run_with_a_tool_sums_every_model_request(wiki):
    """A real Pydantic AI agent: two model requests (the tool call, the answer); the
    result carries the usage of the run, the sum of both."""
    async def stream_fn(messages, info: AgentInfo):
        if len(messages) == 1:
            yield {0: DeltaToolCall(name="read_page", json_args='{"path": "concepts/cinderella"}')}
        else:
            yield "Cinderella lost a glass slipper."

    result = _collect(wiki, _agents(stream_fn, []))[-1]
    assert result.input_tokens > 0 and result.output_tokens > 0

    async def one_request(messages, info: AgentInfo):
        yield "Cinderella lost a glass slipper."

    single = _collect(wiki, _agents(one_request, []))[-1]
    assert result.input_tokens > single.input_tokens      # the second request resends the tool return
