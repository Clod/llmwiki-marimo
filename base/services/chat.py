"""One chat turn, in any of the three chat modes, for any user interface.

`build_agents` builds the two agents of a wiki; `chat_turn` answers a turn in
the pre-retrieval or the strict mode; `chat_turn_stream` answers it in the
streaming mode, chunk by chunk. `chat_turn_with_context` and
`chat_turn_stream_with_context` do the same and also return the passages the
model read (`ChatResult`), for the sentence verification; `chat_turn` and
`chat_turn_stream` are built on them. Each turn is one `turn` root span
(domain/tracing.py). The caller passes the conversation as objects with `.role`
("user" or "assistant") and `.content`, the shape `mo.ui.chat` delivers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai import AgentRunResultEvent
from pydantic_ai.messages import (
    ModelRequest, ModelResponse, PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta,
    ToolCallPart, UserPromptPart,
)

from domain import tracing
from domain.chat.agent import create_agent
from domain.chat.config import WikiAssistantConfig, load_config
from domain.chat.guardrail import strip_refused_exchanges
from domain.chat.history import data_origin, trim_history
from domain.chat.postprocess import advisory_table
from domain.chat.condense import prompt as condense_user_prompt
from domain.chat.condense import system_prompt as condense_prompt
from domain.chat.preretrieval import pre_retrieval_answer, with_open_page
from domain.chat.workflow import output_checks
from domain.prompts import load_prompt
from domain.verify.context import context_from_messages
from domain.verify.models import ContextPassage
from services.wiki import Wiki

PRE_RETRIEVAL = "pre-retrieval"
STRICT = "strict"
STREAMING = "streaming"

# In the strict and streaming modes the model searches with its own tools; the
# open page reaches it as the same note the pre-retrieval mode uses.
_with_open_page = with_open_page


@dataclass(frozen=True)
class ChatAgents:
    """The chat configuration of a wiki and its two agents.

    `agent` searches with its own tools (strict and streaming modes);
    `agent_pre_retrieval` has no wiki-search tools, because the code retrieves
    before it runs (pre-retrieval mode). Both are built once per wiki, so
    switching the mode does not rebuild anything.
    """

    config: WikiAssistantConfig
    agent: Any
    agent_pre_retrieval: Any
    # The agent that rewrites a follow-up into a question that stands alone
    # (query condensation, `domain/chat/condense.py`); None turns it off.
    condenser: Any = None


def build_agents(wiki: Wiki, base_url: str, api_key: str, model: str) -> ChatAgents:
    """Build the chat agents of `wiki`, with the tools of every domain whose
    requirements its data satisfies (`domain/extensions/`)."""
    from domain.extensions import activate_all

    config = load_config(wiki.path)
    domain = activate_all(wiki.path)
    domain_tools = domain.tools if domain else None
    domain_prompt = domain.prompt if domain else None
    # A domain may replace query_dataset (and its prompt addendum) with its own data tools.
    replaces_dataset = bool(domain and domain.replaces_dataset_tool)
    common = dict(
        system_prompt=config.system_prompt, language=config.language,
        workspace=wiki.path, extra_tools=domain_tools, include_dataset_tool=not replaces_dataset,
    )
    agent = create_agent(
        base_url, api_key, model, extra_prompt=domain_prompt, include_wiki_tools=True, **common,
    )
    agent_pre_retrieval = create_agent(
        base_url, api_key, model, extra_prompt=(domain_prompt or "") + load_prompt("pre_retrieval", config.language),
        include_wiki_tools=False, **common,
    )
    condenser = create_agent(
        base_url, api_key, model, system_prompt=condense_prompt(config.language),
        language=config.language, include_wiki_tools=False,
    )
    return ChatAgents(config=config, agent=agent, agent_pre_retrieval=agent_pre_retrieval,
                      condenser=condenser)


def model_history(messages: Sequence[Any], language: str | None = None) -> list:
    """The prior turns as Pydantic AI messages, without refused exchanges.

    A citation-less refusal left in the context primes the model to answer the
    next question without a citation too (verified), so refused exchanges are
    dropped before the history is trimmed and converted. An assistant turn that
    read data ends with its origin line (`data_origin`): the history has no tool
    call and no injected context, so without the line the model calls the figures
    of a past answer unsupported.
    """
    history: list = []
    for message in trim_history(strip_refused_exchanges(list(messages))):
        if message.role == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=message.content)]))
        elif message.role == "assistant":
            origin = data_origin(getattr(message, "context", ()), language)
            content = f"{message.content}\n\n{origin}" if origin else message.content
            history.append(ModelResponse(parts=[TextPart(content=content)]))
    return history


def _history_counts(messages: Sequence[Any], history: list) -> dict:
    """The counts that node C1 records: the prior messages, the refused ones
    `strip_refused_exchanges` removed, and the messages the model receives."""
    prior = list(messages[:-1])
    return dict(
        prior_messages=len(prior),
        refused_dropped=len(prior) - len(strip_refused_exchanges(prior)),
        history_messages=len(history),
    )


def _raw_answer(raw: str, answer: str) -> str | None:
    """The model's text when the code replaced it; None when the answer shows it
    (as written, or with a table, a citation or a refusal line added)."""
    raw = (raw or "").strip()
    return raw if raw and raw not in answer else None


def _text_of(event: Any) -> str:
    """The text a stream event adds to the answer: the start of a text part, or a
    text delta; '' for any other event (tool calls, tool returns, thinking)."""
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return event.part.content
    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return event.delta.content_delta
    return ""


def _tools_called(run_messages: Sequence[Any]) -> list[str]:
    """The names of the tool calls of a run, in order."""
    return [
        part.tool_name
        for message in run_messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolCallPart)
    ]


def _turn_attributes(messages: Sequence[Any], mode: str, conversation_id: str | None,
                     language: str, history: list) -> dict:
    return dict(
        conversation_id=conversation_id,
        turn=sum(1 for m in messages if m.role == "user"),
        mode=mode, question=messages[-1].content, language=language,
        history_messages=len(history),
    )


@dataclass(frozen=True)
class ChatResult:
    """An answer and what the model read to write it.

    context:   the passages of the turn: the pages and fragments the
               pre-retrieval mode injected, and the tool returns of the run. A
               refusal the code makes before the model runs has none.
    generated: texts the code appended to the answer (the advisory table); the
               verification skips them.
    """

    answer: str
    context: tuple[ContextPassage, ...] = ()
    generated: tuple[str, ...] = ()
    # What the model wrote, when the code replaced it (a refusal of the guardrail, R3,
    # R4); None when the answer shows the model's text. For the user to audit it.
    raw_answer: str | None = None
    # The nodes the turn went through, in order: {"node", "diagram", "attributes"} each
    # (`tracing.collect`); kept whether or not WIKI_TRACE is on.
    path: tuple[dict, ...] = ()
    # The tokens of the turn: the sum over every model call (the condensation, the answer,
    # each round of the agent loop). A refusal the code makes before the model runs has 0.
    input_tokens: int = 0
    output_tokens: int = 0


class _Tokens:
    """The tokens of the model calls of one turn, summed from `result.usage()`."""

    def __init__(self) -> None:
        self.input = 0
        self.output = 0

    def add(self, result: Any) -> None:
        # Every Pydantic AI result has `usage` (a property in this version, a method in
        # older ones and in the test stand-ins); a stand-in without it counts 0.
        usage = getattr(result, "usage", None)
        if usage is None:
            return
        if not hasattr(usage, "input_tokens"):     # the method of the test stand-ins
            usage = usage()
        self.input += usage.input_tokens
        self.output += usage.output_tokens


def _condenser(agents: ChatAgents, language: str | None, tokens: _Tokens | None = None):
    """The `condense(question, history)` call of a turn, or None when the wiki turns
    condensation off (`[pre_retrieval] condense_question = false`) or has no condenser.
    Its tokens go to `tokens`."""
    if agents.condenser is None or not getattr(agents.config, "condense_question", True):
        return None

    async def condense(question: str, history: list) -> str:
        result = await agents.condenser.run(condense_user_prompt(question, history, language))
        if tokens is not None:
            tokens.add(result)
        return str(result.output)

    return condense


async def chat_turn_with_context(
    wiki: Wiki, agents: ChatAgents, messages: Sequence[Any], *,
    mode: str, conversation_id: str | None = None, open_page: str | None = None,
) -> ChatResult:
    """Answer the last message of `messages` in the pre-retrieval or strict mode.

    `open_page` is the id of the page the user is reading, or None. The context
    does not depend on `WIKI_TRACE`.
    """
    history = model_history(messages[:-1], agents.config.language)
    question = messages[-1].content
    language = agents.config.language
    attributes = _turn_attributes(messages, mode, conversation_id, language, history)
    R = tracing.READING
    tokens = _Tokens()

    if mode == PRE_RETRIEVAL:
        injected: list[ContextPassage] = []
        ran: list = []

        outputs: list[str] = []

        async def run_agent(prompt, prior):
            result = await agents.agent_pre_retrieval.run(
                prompt, deps=wiki.db_path, message_history=prior,
            )
            tokens.add(result)
            ran.append(result.all_messages())
            outputs.append(str(result.output))
            return result

        with tracing.collect() as path, tracing.root("turn", wiki.path, **attributes):
            tracing.mark("C1", R, **_history_counts(messages, history))
            tracing.mark("C2", R, mode=mode)
            answer = await pre_retrieval_answer(
                question, config=agents.config, db_path=wiki.db_path, workspace=wiki.path,
                history=history, language=language, run_agent=run_agent, open_page=open_page,
                on_context=injected.extend, condense=_condenser(agents, language, tokens),
            )
        run_messages = ran[-1] if ran else []
        table = advisory_table(run_messages)
        return ChatResult(
            answer, (*injected, *context_from_messages(run_messages)), (table,) if table else (),
            raw_answer=_raw_answer(outputs[-1], answer) if outputs else None, path=tuple(path),
            input_tokens=tokens.input, output_tokens=tokens.output,
        )

    if mode != STRICT:
        raise ValueError(f"chat_turn answers the pre-retrieval and strict modes, not {mode!r}")
    with tracing.collect() as path, tracing.root("turn", wiki.path, **attributes) as root:
        tracing.mark("C1", R, **_history_counts(messages, history))
        tracing.mark("C2", R, mode=mode)
        with tracing.node("T1", R) as t1:
            result = await agents.agent.run(
                _with_open_page(question, open_page, language),
                deps=wiki.db_path, message_history=history,
            )
            tokens.add(result)
            raw = result.output
            messages_run = result.all_messages()
            t1.set(tools_called=_tools_called(messages_run), raw_output=raw)
        # The checks of the answer, T2 to T5: the output workflow of the strict mode.
        checks = output_checks.AnswerChecks(raw, messages_run, language)
        answer = await output_checks.graph.run(state=checks, inputs=checks)
        root.set(raw_output=raw, final_answer=answer, grounded=checks.grounded,
                 refusal_substituted=checks.refusal_substituted)
    table = advisory_table(messages_run)
    return ChatResult(answer, tuple(context_from_messages(messages_run)), (table,) if table else (),
                      raw_answer=_raw_answer(raw, answer), path=tuple(path),
                      input_tokens=tokens.input, output_tokens=tokens.output)


async def chat_turn(
    wiki: Wiki, agents: ChatAgents, messages: Sequence[Any], *,
    mode: str, conversation_id: str | None = None, open_page: str | None = None,
) -> str:
    """Answer the last message of `messages` in the pre-retrieval or strict mode.

    `open_page` is the id of the page the user is reading, or None.
    """
    result = await chat_turn_with_context(
        wiki, agents, messages, mode=mode, conversation_id=conversation_id, open_page=open_page,
    )
    return result.answer


async def chat_turn_stream_with_context(
    wiki: Wiki, agents: ChatAgents, messages: Sequence[Any], *,
    conversation_id: str | None = None, open_page: str | None = None,
) -> AsyncIterator[str | ChatResult]:
    """Answer the last message of `messages` in the streaming mode.

    Yields the text chunks as they arrive, then one `ChatResult` as the last
    item, with the whole answer and the tool returns of the run.
    """
    history = model_history(messages[:-1], agents.config.language)
    question = messages[-1].content
    attributes = _turn_attributes(messages, STREAMING, conversation_id,
                                  agents.config.language, history)
    full_text = ""
    run_messages: list = []
    tokens = _Tokens()
    R = tracing.READING
    with tracing.collect() as path, tracing.root("turn", wiki.path, **attributes) as root:
        tracing.mark("C1", R, **_history_counts(messages, history))
        tracing.mark("C2", R, mode=STREAMING)
        with tracing.node("F1", R) as f1:
            # run_stream_events runs the whole agent — every model request and every
            # tool call — and streams the text as it arrives. run_stream took the first
            # text of the model as the final output and skipped a tool call that
            # followed it (cue-04: "Permíteme buscar en el wiki." and no search).
            async with agents.agent.run_stream_events(
                _with_open_page(question, open_page, agents.config.language),
                deps=wiki.db_path, message_history=history,
            ) as events:
                async for event in events:
                    if isinstance(event, AgentRunResultEvent):
                        run_messages = event.result.all_messages()
                        tokens.add(event.result)
                        continue
                    chunk = _text_of(event)
                    if not chunk:
                        continue
                    if isinstance(event, PartStartEvent) and full_text:
                        chunk = "\n\n" + chunk          # the text of a later model response
                    full_text += chunk
                    yield chunk
            f1.set(tools_called=_tools_called(run_messages))
        tracing.mark("F2", R, final_answer=full_text)
        root.set(raw_output=full_text, final_answer=full_text)
    yield ChatResult(full_text, tuple(context_from_messages(run_messages)), path=tuple(path),
                     input_tokens=tokens.input, output_tokens=tokens.output)


async def chat_turn_stream(
    wiki: Wiki, agents: ChatAgents, messages: Sequence[Any], *,
    conversation_id: str | None = None, open_page: str | None = None,
) -> AsyncIterator[str]:
    """Answer the last message of `messages` in the streaming mode, chunk by chunk.

    The streaming mode runs no check: each chunk is shown as it arrives.
    """
    async for item in chat_turn_stream_with_context(
        wiki, agents, messages, conversation_id=conversation_id, open_page=open_page,
    ):
        if isinstance(item, str):
            yield item


def save_answer(
    wiki: Wiki, config: WikiAssistantConfig, title: str, text: str, category: str,
    *, base_url: str, api_key: str, model: str,
) -> str:
    """Save a chat answer as a wiki page; return the status message."""
    from openai import OpenAI

    from domain.chat.wiki_tools import save_to_wiki

    client = OpenAI(base_url=base_url, api_key=api_key)
    return save_to_wiki(
        wiki.db_path, wiki.path, title.strip(), text, category,
        client=client, model=model, language=config.language if config else "en",
    )


_EXCHANGE_LABELS = {"es": ("Pregunta", "Respuesta"), "en": ("Question", "Answer")}


def conversation_markdown(messages: Sequence[Any], language: str) -> str:
    """The conversation as markdown, one question and its answer per exchange.

    Refused exchanges are dropped, as in `model_history`: a refusal holds no
    content for a page. A trailing question with no answer yet is dropped too.
    """
    question_label, answer_label = _EXCHANGE_LABELS.get(language, _EXCHANGE_LABELS["en"])
    blocks: list[str] = []
    question: str | None = None
    for message in strip_refused_exchanges(list(messages)):
        if message.role == "user":
            question = message.content.strip()
        elif message.role == "assistant" and question is not None:
            blocks.append(f"**{question_label}:** {question}\n\n"
                          f"**{answer_label}:**\n\n{message.content.strip()}")
            question = None
    return "\n\n---\n\n".join(blocks)


class EmptyConversation(ValueError):
    """The conversation has no answered question to save."""


def _conversation_content(wiki: Wiki, config: WikiAssistantConfig | None, messages: Sequence[Any]) -> tuple[str, str]:
    """The conversation as markdown and the language of the page.
    Raises `EmptyConversation` when no exchange was answered."""
    language = config.language if config else wiki.language
    content = conversation_markdown(messages, language)
    if not content:
        raise EmptyConversation("the conversation has no answered question to save")
    return content, language


def draft_conversation(
    wiki: Wiki, config: WikiAssistantConfig | None, title: str, messages: Sequence[Any],
    *, base_url: str, api_key: str, model: str,
):
    """Draft the concept page of a whole conversation; return a `PageDraft`.

    Step 1 of saving: the model structures the exchanges of
    `conversation_markdown` into a page (`wiki_tools.draft_wiki_page`). Nothing is
    written and nothing is committed. A conversation with no answered exchange is
    refused with `EmptyConversation`.
    """
    from openai import OpenAI

    from domain.chat import wiki_tools

    content, language = _conversation_content(wiki, config, messages)
    client = OpenAI(base_url=base_url, api_key=api_key)
    return wiki_tools.draft_wiki_page(
        wiki.db_path, wiki.path, title.strip(), content, "concept",
        client=client, model=model, language=language,
    )


def save_reviewed_page(wiki: Wiki, title: str, markdown: str) -> str:
    """Write the page the user approved, as given; return the status message.

    Step 2 of saving (`wiki_tools.write_wiki_page`), then one git commit,
    `chat: <title>`. The text is not sent to the model; the post-save repair is
    the deterministic one.
    """
    from domain.chat import wiki_tools
    from domain.tools.git_ops import auto_commit, init_wiki_repo

    title = title.strip()
    result = wiki_tools.write_wiki_page(
        wiki.db_path, wiki.path, title, markdown, "concept", language=wiki.language,
    )
    init_wiki_repo(wiki.path)
    auto_commit(wiki.path, f"chat: {title}")
    return result


def save_conversation(
    wiki: Wiki, config: WikiAssistantConfig | None, title: str, messages: Sequence[Any],
    *, base_url: str, api_key: str, model: str,
) -> str:
    """Save the whole conversation as a concept page; return the status message.

    A conversation always becomes a concept page: summary pages summarize a
    source document. `draft_conversation`, then `save_reviewed_page` on the
    draft, with no review in between. A conversation with no answered exchange is
    refused with `EmptyConversation`, a `ValueError`.
    """
    draft = draft_conversation(wiki, config, title, messages,
                               base_url=base_url, api_key=api_key, model=model)
    return save_reviewed_page(wiki, title, draft.markdown)
