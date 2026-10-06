"""One chat turn, in any of the three chat modes, for any user interface.

`build_agents` builds the two agents of a wiki; `chat_turn` answers a turn in
the pre-retrieval or the strict mode; `chat_turn_stream` answers it in the
streaming mode, chunk by chunk. Each turn is one `turn` root span
(domain/tracing.py). The caller passes the conversation as objects with `.role`
("user" or "assistant") and `.content`, the shape `mo.ui.chat` delivers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from domain import tracing
from domain.chat.agent import create_agent
from domain.chat.config import WikiAssistantConfig, load_config
from domain.chat.guardrail import enforce_grounding, has_grounding, refusal_for, strip_refused_exchanges
from domain.chat.history import trim_history
from domain.chat.postprocess import answer_with_table, ensure_citation
from domain.chat.preretrieval import pre_retrieval_answer, with_open_page
from services.wiki import Wiki

PRE_RETRIEVAL = "pre-retrieval"
STRICT = "strict"
STREAMING = "streaming"

# Retrieval-mode block for the pre-retrieval agent: it has NO wiki-search
# tools, so its prompt must say so (otherwise the shared system prompt's search
# steps name tools it cannot call). The data and advisory tools stay.
_PRE_RETRIEVAL_PROMPT = (
    "\n\n## Modo pre-retrieval\n"
    "NO tenés herramientas de búsqueda de wiki (search_wiki_fts, "
    "read_wiki_page, search_source_chunks): ignorá cualquier paso que las "
    "mencione. Las páginas relevantes del wiki ya te vienen inyectadas en el "
    "CONTEXTO de cada pregunta — respondé exclusivamente desde ese contexto, "
    "citando la fuente; si no alcanza, decilo. Las herramientas de datos "
    "(query_dataset) y de cálculo (estimar_alternativas) sí siguen disponibles."
)


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


def build_agents(wiki: Wiki, base_url: str, api_key: str, model: str) -> ChatAgents:
    """Build the chat agents of `wiki`, with the finance overlay when its data
    satisfies the finance manifest."""
    from domain.finance_argentina.agent_tool import activate as activate_finance

    config = load_config(wiki.path)
    finance_tools, finance_prompt = activate_finance(wiki.path)
    common = dict(
        system_prompt=config.system_prompt, language=config.language,
        workspace=wiki.path, extra_tools=finance_tools,
    )
    agent = create_agent(
        base_url, api_key, model, extra_prompt=finance_prompt, include_wiki_tools=True, **common,
    )
    agent_pre_retrieval = create_agent(
        base_url, api_key, model, extra_prompt=(finance_prompt or "") + _PRE_RETRIEVAL_PROMPT,
        include_wiki_tools=False, **common,
    )
    return ChatAgents(config=config, agent=agent, agent_pre_retrieval=agent_pre_retrieval)


def model_history(messages: Sequence[Any]) -> list:
    """The prior turns as Pydantic AI messages, without refused exchanges.

    A citation-less refusal left in the context primes the model to answer the
    next question without a citation too (verified), so refused exchanges are
    dropped before the history is trimmed and converted.
    """
    history: list = []
    for message in trim_history(strip_refused_exchanges(list(messages))):
        if message.role == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=message.content)]))
        elif message.role == "assistant":
            history.append(ModelResponse(parts=[TextPart(content=message.content)]))
    return history


def _turn_attributes(messages: Sequence[Any], mode: str, conversation_id: str | None,
                     language: str, history: list) -> dict:
    return dict(
        conversation_id=conversation_id,
        turn=sum(1 for m in messages if m.role == "user"),
        mode=mode, question=messages[-1].content, language=language,
        history_messages=len(history),
    )


async def chat_turn(
    wiki: Wiki, agents: ChatAgents, messages: Sequence[Any], *,
    mode: str, conversation_id: str | None = None, open_page: str | None = None,
) -> str:
    """Answer the last message of `messages` in the pre-retrieval or strict mode.

    `open_page` is the id of the page the user is reading, or None.
    """
    history = model_history(messages[:-1])
    question = messages[-1].content
    language = agents.config.language
    attributes = _turn_attributes(messages, mode, conversation_id, language, history)

    if mode == PRE_RETRIEVAL:
        async def run_agent(prompt, prior):
            return await agents.agent_pre_retrieval.run(
                prompt, deps=wiki.db_path, message_history=prior,
            )

        with tracing.root("turn", wiki.path, **attributes):
            return await pre_retrieval_answer(
                question, config=agents.config, db_path=wiki.db_path, workspace=wiki.path,
                history=history, language=language, run_agent=run_agent, open_page=open_page,
            )

    if mode != STRICT:
        raise ValueError(f"chat_turn answers the pre-retrieval and strict modes, not {mode!r}")
    with tracing.root("turn", wiki.path, **attributes) as root:
        result = await agents.agent.run(
            _with_open_page(question, open_page, language),
            deps=wiki.db_path, message_history=history,
        )
        raw = result.output
        messages_run = result.all_messages()
        answer = enforce_grounding(raw, messages_run, refusal=refusal_for(language))
        refusal_substituted = answer != raw
        # Deterministic post-processing (domain/chat/postprocess.py): guarantee
        # the advisory table and a source citation regardless of whether the
        # model reproduced them under history priming. Both no-op on a refusal.
        answer = ensure_citation(answer_with_table(answer, messages_run), messages_run)
        root.set(raw_output=raw, final_answer=answer, grounded=has_grounding(messages_run),
                 refusal_substituted=refusal_substituted)
    return answer


async def chat_turn_stream(
    wiki: Wiki, agents: ChatAgents, messages: Sequence[Any], *,
    conversation_id: str | None = None, open_page: str | None = None,
) -> AsyncIterator[str]:
    """Answer the last message of `messages` in the streaming mode, chunk by chunk.

    The streaming mode runs no check: each chunk is shown as it arrives.
    """
    history = model_history(messages[:-1])
    question = messages[-1].content
    attributes = _turn_attributes(messages, STREAMING, conversation_id,
                                  agents.config.language, history)
    full_text = ""
    with tracing.root("turn", wiki.path, **attributes) as root:
        async with agents.agent.run_stream(
            _with_open_page(question, open_page, agents.config.language),
            deps=wiki.db_path, message_history=history,
        ) as result:
            async for chunk in result.stream_text(delta=True):
                full_text += chunk
                yield chunk
        root.set(raw_output=full_text, final_answer=full_text)


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
