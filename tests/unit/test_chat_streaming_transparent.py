"""The streaming mode changes nothing between the user and the agent.

The agent receives every prior message as written, refused exchanges included,
with no origin line and no trimming. It receives the question as written, with the
note of the open page when the user reads a page. The answer is the text of the
agent, and the path of the turn is C1 C2 F1 F2. The agent is a stand-in; no model
is called.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic_ai import AgentRunResultEvent
from pydantic_ai.messages import (
    ModelRequest, ModelResponse, PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta,
    UserPromptPart,
)

from domain.chat.guardrail import REFUSAL_EN
from domain.chat.history import DEFAULT_MAX_TURNS
from domain.chat.preretrieval import with_open_page
from domain.verify.models import ContextPassage
from services import chat
from services.wiki import open_wiki

INVENTED = "Cinderella lost 7.777 slippers, about 1.200."
TABLE = "| opción | TEA |\n|---|---|\n| plazo_fijo | 12.34% |"


class RecordingAgent:
    """Streams `output` in two chunks and records the prompt and the history it receives."""

    def __init__(self, output: str) -> None:
        self.output = output
        self.prompts: list[str] = []
        self.histories: list[list] = []

    @asynccontextmanager
    async def run_stream_events(self, prompt, deps, message_history):
        self.prompts.append(prompt)
        self.histories.append(list(message_history))

        async def events():
            yield PartStartEvent(index=0, part=TextPart(self.output[:5]))
            yield PartDeltaEvent(index=0, delta=TextPartDelta(self.output[5:]))
            yield AgentRunResultEvent(SimpleNamespace(output=self.output, all_messages=lambda: []))

        yield events()


def msg(role, content, context=()):
    return SimpleNamespace(role=role, content=content, context=context)


def agents(agent):
    config = SimpleNamespace(language="en", number_tolerance=0.05, off_limits=[], data_aliases={},
                             system_prompt="", suggested_prompts=[])
    return chat.ChatAgents(config=config, agent=agent, agent_pre_retrieval=agent)


def stream(wiki, ag, messages, open_page=None):
    async def collect():
        return [item async for item in chat.chat_turn_stream_with_context(
            wiki, ag, messages, open_page=open_page)]

    return asyncio.run(collect())


@pytest.fixture
def wiki(tmp_path):
    return open_wiki(tmp_path / "tales")


def conversation() -> list:
    """A refused exchange, an answer that read a dataset, an answer with a table, and
    more turns than `trim_history` keeps."""
    dataset = (ContextPassage("datasets/plazo_fijo.md", "| clave | valor |"),)
    messages = [
        msg("user", "capital of France?"), msg("assistant", REFUSAL_EN),
        msg("user", "rate of Banco Galicia?"), msg("assistant", "The TNA is 32,5 %.", dataset),
        msg("user", "and the TEA?"), msg("assistant", f"The TEA is 12.34 %.\n\n{TABLE}"),
    ]
    for i in range(DEFAULT_MAX_TURNS + 1):
        messages += [msg("user", f"question {i}"), msg("assistant", f"answer {i}")]
    return messages


def test_the_agent_receives_every_prior_message_as_written(wiki):
    prior = conversation()
    agent = RecordingAgent("Cinderella lost a slipper.")
    stream(wiki, agents(agent), [*prior, msg("user", "Who is Cinderella?")])
    expected = [
        ModelRequest(parts=[UserPromptPart(content=m.content)]) if m.role == "user"
        else ModelResponse(parts=[TextPart(content=m.content)])
        for m in prior
    ]
    received = agent.histories[0]
    assert len(received) == len(prior)
    assert [type(m) for m in received] == [type(m) for m in expected]
    assert [m.parts[0].content for m in received] == [m.content for m in prior]
    assert all("Origin" not in m.parts[0].content for m in received)


def test_c1_counts_the_whole_history(wiki):
    prior = conversation()
    result = stream(wiki, agents(RecordingAgent("a")), [*prior, msg("user", "Who is Cinderella?")])[-1]
    c1 = next(n["attributes"] for n in result.path if n["node"] == "C1")
    assert (c1["prior_messages"], c1["refused_dropped"], c1["history_messages"]) == (len(prior), 0, len(prior))


def test_the_agent_receives_the_question_as_written(wiki):
    agent = RecordingAgent("a")
    stream(wiki, agents(agent), [msg("user", "Who is Cinderella?")])
    assert agent.prompts == ["Who is Cinderella?"]


def test_with_an_open_page_the_agent_receives_the_question_with_the_page(wiki):
    agent = RecordingAgent("a")
    stream(wiki, agents(agent), [msg("user", "Who is she?")], open_page="concepts/cinderella")
    assert agent.prompts == [with_open_page("Who is she?", "concepts/cinderella", "en")]
    assert "concepts/cinderella" in agent.prompts[0] and agent.prompts[0].endswith("Who is she?")


def test_the_user_sees_the_text_of_the_agent_and_the_path_is_c1_c2_f1_f2(wiki):
    items = stream(wiki, agents(RecordingAgent(INVENTED)), [msg("user", "How many slippers, 1187,5?")])
    result = items[-1]
    assert "".join(items[:-1]) == INVENTED
    assert result.answer == INVENTED
    assert result.figures is None and result.generated == ()
    assert [n["node"] for n in result.path] == ["C1", "C2", "F1", "F2"]
