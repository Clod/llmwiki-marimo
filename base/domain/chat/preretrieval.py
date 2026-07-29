"""Hybrid pre-retrieval orchestration (WIP).

The code retrieves before the model answers, instead of leaving retrieval to the
model. This module assembles the deterministic pieces (`scope`, `overlap`) with
the dataset vocabulary and the per-wiki lists.

First piece: `build_vocabulary` — the closed "lista de datos" (dataset
categories + their keys) that `scope.mentions_known_data` checks a question
against. Everything here is derived from a `DatasetSource`; no LLM.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from domain.chat.guardrail import enforce_grounding, refusal_for
from domain.chat.overlap import is_supported
from domain.chat.postprocess import answer_with_table, ensure_citation
from domain.chat.scope import (
    advisory_intent,
    collection_intent,
    is_off_limits,
    mentions_known_data,
)
from domain.datasets.frontmatter import split_frontmatter
from domain.datasets.models import DatasetSource
from domain.datasets.source import LocalMarkdownSource
from domain.tools.search import search_chunks
from domain.tools.wiki_fs import concept_page_names

_INJECT_TEMPLATE = (
    "Respondé la pregunta usando EXCLUSIVAMENTE el siguiente contexto recuperado "
    "del wiki, citando la fuente. Si el contexto no alcanza para responder, decilo.\n\n"
    "{context}\n\n---\nPregunta: {question}"
)
_TIER2_WARNING = (
    "> ⚠️ Esta respuesta proviene de un documento fuente sin página curada del "
    "wiki; verificá."
)


def build_vocabulary(source: DatasetSource) -> set[str]:
    """The set of known data terms: every dataset category plus every key in it.

    Keys can repeat across rows (one per metrica); the set dedupes them.
    """
    vocabulary: set[str] = set()
    for categoria in source.categories():
        vocabulary.add(categoria)
        for row in source.query(categoria):
            vocabulary.add(row.clave)
    return vocabulary


def _format_hits(rows: list[dict]) -> list[str]:
    """Turn search_chunks rows into injectable, attributed text blocks."""
    blocks: list[str] = []
    for row in rows:
        content = (row.get("content") or "").strip()
        if not content:
            continue
        label = row.get("path") or row.get("filename") or ""
        blocks.append(f"[{label}]\n{content}" if label else content)
    return blocks


# Ubiquitous Spanish function words. OR-joining these would make an off-topic
# question ("¿la capital de Francia?") match nearly every page — so we drop them
# (along with 1-2 char tokens) before building the query. The roster gate is the
# real coverage authority; this just keeps the lexical match on content words.
_FTS_STOPWORDS = frozenset({
    "que", "qué", "los", "las", "una", "unos", "unas", "con", "por", "para",
    "del", "como", "cómo", "son", "sos", "está", "estan", "están", "este",
    "esta", "esto", "estos", "estas", "cual", "cuál", "cuales", "cuáles",
    "quien", "quién", "dame", "hago", "estoy", "más", "mas", "pero", "sus",
    "nos", "les", "ese", "esa", "eso", "aquel", "sobre", "entre", "desde",
    "hasta", "donde", "dónde", "cuando", "cuándo", "muy", "hay", "tengo",
})


def _fts_query(text: str) -> str:
    """Turn a natural-language question into a safe FTS5 MATCH expression.

    FTS5 reads bare ',', '?', '¿', quotes, etc. as syntax, so passing a raw
    question to MATCH raised OperationalError — silently swallowed by
    search_chunks, which then returned no hits and left the pre-retrieval gate
    with nothing to inject. We tokenize to word characters, drop stopwords and
    1-2 char tokens, and OR the rest, quoting each so an FTS keyword
    (OR/AND/NOT/NEAR) that happens to be a word can't act as an operator. Empty
    string when nothing meaningful remains — search_chunks short-circuits that
    to [].
    """
    tokens = [
        t for t in re.findall(r"\w+", text, flags=re.UNICODE)
        if len(t) > 2 and t.lower() not in _FTS_STOPWORDS
    ]
    return " OR ".join(f'"{t}"' for t in tokens)


def retrieve_wiki(db_path: str, query: str, *, limit: int = 6) -> list[str]:
    """Top curated-wiki chunks for `query` (Tier 1). Empty list if none."""
    return _format_hits(search_chunks(db_path, _fts_query(query), limit=limit, scope="wiki"))


def retrieve_source_chunks(db_path: str, query: str, *, limit: int = 4) -> list[str]:
    """Top raw source-document chunks for `query` (Tier 2). Empty if none."""
    return _format_hits(search_chunks(db_path, _fts_query(query), limit=limit, scope="sources"))


def retrieve_collection_pages(workspace) -> list[str]:
    """Read the pages that describe the WIKI AS A WHOLE — overview then index —
    straight from disk, for a collection-level question (Tier 1 "curado").

    `wiki/overview.md` and `wiki/index.md` never get a `documents` row, chunks,
    or FTS entries: the agentic system prompt reaches them by literally reading
    the file as step 1, but `search_chunks` (what `retrieve_wiki` queries) never
    sees them. So for a question like "what tales are in this wiki?" there is
    nothing to find in the index, no matter how the FTS query is built — the
    evidence has to come from disk, the same place the agentic mode gets it.

    Overview first: it is narrative, written to be read start to finish, and
    the better context for a collection question. Index is a bare listing —
    still useful, but second. Front-matter is stripped (`split_frontmatter`):
    the model is being handed context to answer FROM, not page metadata.
    Returns `[]` when neither file exists — the natural "nothing to inject"
    case, which is also what makes this branch a no-op on a wiki with no
    collection pages instead of a separate special case.
    """
    pages: list[str] = []
    for rel in ("wiki/overview.md", "wiki/index.md"):
        path = workspace / rel
        if not path.exists():
            continue
        _, body = split_frontmatter(path.read_text(encoding="utf-8"))
        body = body.strip()
        if body:
            pages.append(f"[{rel}]\n{body}")
    return pages


@dataclass(frozen=True)
class RetrievalPlan:
    """What to do with a question after the deterministic gate + retrieval.

    action: "invoke" (call the model) | "refuse" (answer "no lo tengo", no LLM).
    tier:   "curado" (Tier 1 wiki page) | "crudo" (Tier 2 raw doc) | None (data
            question — tools only, no injected context).
    context: the retrieved text to inject, or None.
    verify:  run answer-vs-source overlap after the answer (Tier 2 only).
    """

    action: str
    tier: str | None
    context: str | None
    verify: bool


_REFUSE = RetrievalPlan(action="refuse", tier=None, context=None, verify=False)


def plan_retrieval(
    question: str,
    *,
    off_limits: Iterable[str],
    wiki_hits: list[str],
    doc_hits: list[str],
    has_data: bool,
    in_roster: bool,
    collection_hits: list[str] = [],
) -> RetrievalPlan:
    """Decide the plan. Order: blacklist first (refuse), then — for a covered
    topic — curated wiki (Tier 1); then a collection-level question (Tier 1,
    the overview/index read from disk); then a data/advisory question (tools
    only); then raw docs (Tier 2, verify) as the covered-topic fallback; else
    refuse without invoking the model.

    `has_data` and `in_roster` are computed by the caller (`mentions_known_data`
    against the dataset vocab, and against the full coverage padrón). BOTH tiers
    are gated on `in_roster`: lexical FTS can match a curated or raw chunk on a
    shared word for an UNcovered topic, so the padrón — not the search hit — is
    the authority on coverage. This is what stops a tangential chunk from leaking
    general knowledge (the CEDEARs leak), on Tier 1 as well as Tier 2.

    `collection_hits` sits AFTER the named-roster Tier 1 check and BEFORE
    `has_data`. After, because a question can be both collection-shaped and
    name covered subjects — "compare Cinderella and Snow White" hits the
    roster, and the two concept pages beat the overview. Before `has_data`, so
    a collection question on a datasets wiki ("what data do you have?") reaches
    the overview instead of the tools. `collection_hits` is only ever non-empty
    when the wiki actually has an overview/index page to inject (see
    `retrieve_collection_pages`), so this branch is a no-op — not a special
    case — on a wiki without one.
    """
    if is_off_limits(question, off_limits):
        return _REFUSE
    if wiki_hits and in_roster:
        return RetrievalPlan("invoke", "curado", "\n\n".join(wiki_hits), False)
    if collection_hits:
        return RetrievalPlan("invoke", "curado", "\n\n".join(collection_hits), False)
    if has_data:
        # A question that names known data goes to the tools (query_dataset /
        # advisory) before any raw-doc fallback: the dataset value/date beats
        # answering from raw prose — and a lexical raw-doc hit would otherwise
        # divert "¿a cuánto está el billete verde?" to Tier-2 and starve it of
        # the actual number.
        return RetrievalPlan("invoke", None, None, False)
    if doc_hits and in_roster:
        return RetrievalPlan("invoke", "crudo", "\n\n".join(doc_hits), True)
    return _REFUSE


async def pre_retrieval_answer(
    question: str,
    *,
    config,
    db_path: str,
    workspace,
    history: list,
    language: str | None,
    run_agent,
    on_trace=None,
) -> str:
    """Run one turn through the hybrid pre-retrieval flow and return the answer.

    The CODE retrieves (curated wiki, then raw docs) and decides the plan; the
    model is invoked (via the injected async `run_agent(prompt, history)`) only
    when there's grounding to give it, with the retrieved context prepended.
    Tier-2 (raw-doc) answers are verified with lexical overlap and warned; the
    off-limits / nothing-found cases refuse WITHOUT invoking the model.

    `run_agent` is injected so this is testable without an LLM; it must return an
    object with `.output` (str) and `.all_messages()`. `on_trace`, if given, is
    called with (raw, final, result, refusal_substituted) for the chat trace.
    """
    refusal = refusal_for(language)
    aliases = [alias for names in config.data_aliases.values() for alias in names]
    vocabulary = build_vocabulary(LocalMarkdownSource(workspace / "datasets"))
    # Route to the tools either by a NAMED data term or by generic advisory intent
    # ("$1M, 3 meses, ¿qué alternativas?") — the latter names no instrument but
    # still belongs on the query_dataset/estimar_alternativas path, not a refusal.
    has_data = mentions_known_data(question, vocabulary, aliases) or advisory_intent(question)
    # "In the padrón" = the question names something the wiki actually covers
    # (a dataset term OR a concept page name OR a known alias). Tier-2 (raw docs)
    # is allowed ONLY for a covered topic, so an uncovered question can't pull a
    # tangential chunk as a fig leaf — that was the CEDEARs leak.
    coverage = set(vocabulary) | set(concept_page_names(db_path))
    in_roster = mentions_known_data(question, coverage, aliases)

    if is_off_limits(question, config.off_limits):
        wiki_hits, doc_hits, collection_hits = [], [], []
    else:
        wiki_hits = retrieve_wiki(db_path, question)
        doc_hits = (
            retrieve_source_chunks(db_path, question)
            if (not wiki_hits and in_roster) else []
        )
        # Collection-shaped question ("what tales are in this wiki?") — inject
        # the overview/index read from disk. Empty when the wiki has neither
        # page, so an ordinary wiki with no collection page falls through to
        # has_data/doc_hits/refuse exactly as it did before this branch existed.
        collection_hits = retrieve_collection_pages(workspace) if collection_intent(question) else []

    plan = plan_retrieval(
        question, off_limits=config.off_limits,
        wiki_hits=wiki_hits, doc_hits=doc_hits, has_data=has_data, in_roster=in_roster,
        collection_hits=collection_hits,
    )

    if plan.action == "refuse":
        if on_trace:
            on_trace(raw="", final=refusal, result=None, refusal_substituted=True)
        return refusal

    prompt = (
        _INJECT_TEMPLATE.format(context=plan.context, question=question)
        if plan.context else question
    )
    result = await run_agent(prompt, history)
    raw = result.output
    messages = result.all_messages()

    # Tier 2 (raw doc): verify the answer actually draws on the retrieved chunk.
    if plan.verify and not is_supported(raw, plan.context or ""):
        if on_trace:
            on_trace(raw=raw, final=refusal, result=result, refusal_substituted=True)
        return refusal

    # Grounding: injected context IS the grounding for Tier 1/2 (skip the
    # tool-based guardrail, which would wrongly refuse a context-only answer);
    # for a data/advisory question (no injected context) keep the guardrail.
    gated = enforce_grounding(raw, messages, refusal=refusal) if plan.context is None else raw
    refusal_substituted = gated == refusal and raw != refusal

    answer = ensure_citation(answer_with_table(gated, messages), messages)
    if plan.tier == "crudo" and answer != refusal:
        answer = f"{answer}\n\n{_TIER2_WARNING}"

    if on_trace:
        on_trace(raw=raw, final=answer, result=result, refusal_substituted=refusal_substituted)
    return answer
