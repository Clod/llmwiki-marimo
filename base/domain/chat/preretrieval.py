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

from domain import tracing
from domain.chat.guardrail import enforce_grounding, refusal_for
from domain.chat.overlap import coverage
from domain.chat.postprocess import answer_with_table, ensure_citation
from domain.chat.scope import (
    _normalize,
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
# Tier-2 threshold of `is_supported` (overlap.py); the trace records it next to
# the measured coverage.
_MIN_COVERAGE = 0.2
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
    """Turn search_chunks rows into injectable, attributed text blocks.

    The label is `path + filename` — the page, not its directory. `path` alone
    is `/wiki/concepts/` for *every* concept page, so labelling by it handed the
    model six blocks it could not tell apart, in a mode whose prompt asks it to
    cite. `filename` was already in the row and unused.

    Front-matter is stripped, for the reason `retrieve_collection_pages` gives
    for stripping it there: the model is being handed context to answer FROM,
    not page metadata. It was ~8% of the injected context on the shipped demo.
    The two changes belong together — the `sources:` line inside that
    front-matter was, in practice, what the model cited from, so removing it
    without first making the label identify the page would have taken away the
    attribution and put nothing back.
    """
    blocks: list[str] = []
    for row in rows:
        raw = (row.get("content") or "").strip()
        if not raw:
            continue
        _, body = split_frontmatter(raw)
        content = body.strip() or raw  # a chunk that is *only* front-matter keeps it
        label = f"{row.get('path') or ''}{row.get('filename') or ''}"
        blocks.append(f"[{label}]\n{content}" if label else content)
    return blocks


# Ubiquitous function words, PER LANGUAGE. OR-joining these would make an
# off-topic question ("¿la capital de Francia?") match nearly every page — so we
# drop them (along with 1-2 char tokens) before building the query. The roster
# gate is the real coverage authority; this just keeps the lexical match on
# content words.
#
# ── ADDING A LANGUAGE ────────────────────────────────────────────────────────
# A new wiki language needs an entry here. Without one it falls back to English
# (`_stopwords` below), which filters little in another language and leaves the
# original defect: a word as common as "the" matches nearly every chunk, so
# `wiki_hits` is never empty, and Tier 2 — reached only when `wiki_hits` IS
# empty — becomes unreachable. That is exactly what happened to English before
# `en` was added here. See `docs/manual/workflows.md` §6.7.
#
# The sets must stay language-SPECIFIC rather than merged into one. A function
# word in one language is often a content word in another: Spanish "son" (they
# are) is English "son", which appears in six chunks of the fairy-tale corpus.
# Merging the sets would silently drop the most important word of "Who is the
# king's son?".
_STOPWORDS: dict[str, frozenset[str]] = {
    "es": frozenset({
        "que", "qué", "los", "las", "una", "unos", "unas", "con", "por", "para",
        "del", "como", "cómo", "son", "sos", "está", "estan", "están", "este",
        "esta", "esto", "estos", "estas", "cual", "cuál", "cuales", "cuáles",
        "quien", "quién", "dame", "hago", "estoy", "más", "mas", "pero", "sus",
        "nos", "les", "ese", "esa", "eso", "aquel", "sobre", "entre", "desde",
        "hasta", "donde", "dónde", "cuando", "cuándo", "muy", "hay", "tengo",
    }),
    # Same categories as the Spanish set: articles, prepositions, pronouns,
    # auxiliaries, question words, and the verbs a question is phrased with
    # ("tell me", "explain") — the counterparts of "dame"/"hago"/"tengo".
    # Deliberately excluded because they can be content in a wiki: "may"
    # (month), "will" is kept (auxiliary use dominates), "son" is NOT here (it
    # is a content word in English — see the note above).
    "en": frozenset({
        "the", "and", "for", "are", "was", "were", "has", "have", "had", "but",
        "not", "you", "your", "this", "that", "these", "those", "with", "from",
        "into", "about", "what", "which", "who", "whom", "whose", "when",
        "where", "why", "how", "does", "did", "can", "could", "would", "should",
        "will", "there", "their", "them", "they", "its", "his", "her", "hers",
        "our", "ours", "any", "all", "some", "more", "most", "than", "then",
        "also", "been", "being", "over", "under", "between", "off", "very",
        "just", "only", "such", "too", "tell", "give", "show", "explain",
        "describe", "please",
    }),
}


def _stopwords(language: str | None) -> frozenset[str]:
    """The stop-word set for `language`, falling back to English.

    English is the fallback because it is the project's default wiki language
    (`wiki_settings.load_wiki_language` resolves an absent or unknown value to
    `"en"`), so this matches what such a wiki actually generates.
    """
    return _STOPWORDS.get((language or "en").lower(), _STOPWORDS["en"])


def _fts_query(text: str, language: str | None = None) -> str:
    """Turn a natural-language question into a safe FTS5 MATCH expression.

    FTS5 reads bare ',', '?', '¿', quotes, etc. as syntax, so passing a raw
    question to MATCH raised OperationalError — silently swallowed by
    search_chunks, which then returned no hits and left the pre-retrieval gate
    with nothing to inject. We tokenize to word characters, drop `language`'s
    stop words and 1-2 char tokens, and OR the rest, quoting each so an FTS
    keyword (OR/AND/NOT/NEAR) that happens to be a word can't act as an
    operator. Empty string when nothing meaningful remains — search_chunks
    short-circuits that to [].
    """
    stop = _stopwords(language)
    tokens = [
        t for t in re.findall(r"\w+", text, flags=re.UNICODE)
        if len(t) > 2 and t.lower() not in stop
    ]
    return " OR ".join(f'"{t}"' for t in tokens)


def retrieve_wiki(
    db_path: str, query: str, *, limit: int = 6, language: str | None = None
) -> list[str]:
    """Top curated-wiki chunks for `query` (Tier 1). Empty list if none."""
    return _format_hits(
        search_chunks(db_path, _fts_query(query, language), limit=limit, scope="wiki")
    )


def retrieve_source_chunks(
    db_path: str, query: str, *, limit: int = 4, language: str | None = None
) -> list[str]:
    """Top raw source-document chunks for `query` (Tier 2). Empty if none."""
    return _format_hits(
        search_chunks(db_path, _fts_query(query, language), limit=limit, scope="sources")
    )


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
    # Each decision is recorded as its node of the reading diagram
    # (design_stemming.md, section 1.2), in the order the code evaluates it.
    R = tracing.READING
    if is_off_limits(question, off_limits):
        return _REFUSE
    tracing.mark("Q7", R, answer=bool(wiki_hits and in_roster),
                 wiki_pages=len(wiki_hits), in_roster=in_roster)
    if wiki_hits and in_roster:
        return RetrievalPlan("invoke", "curado", "\n\n".join(wiki_hits), False)
    tracing.mark("Q9", R, answer=bool(collection_hits), collection_pages=len(collection_hits))
    if collection_hits:
        return RetrievalPlan("invoke", "curado", "\n\n".join(collection_hits), False)
    tracing.mark("Q10", R, answer=has_data)
    if has_data:
        # A question that names known data goes to the tools (query_dataset /
        # advisory) before any raw-doc fallback: the dataset value/date beats
        # answering from raw prose — and a lexical raw-doc hit would otherwise
        # divert "¿a cuánto está el billete verde?" to Tier-2 and starve it of
        # the actual number.
        return RetrievalPlan("invoke", None, None, False)
    tracing.mark("Q11", R, answer=bool(doc_hits and in_roster),
                 source_fragments=len(doc_hits), in_roster=in_roster)
    if doc_hits and in_roster:
        return RetrievalPlan("invoke", "crudo", "\n\n".join(doc_hits), True)
    tracing.mark("Q12", R, answer=in_roster)
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
    with tracing.root(
        "turn", workspace, mode="pre-retrieval", question=question, language=language,
    ):
        return await _pre_retrieval_turn(
            question, config=config, db_path=db_path, workspace=workspace,
            history=history, language=language, run_agent=run_agent, on_trace=on_trace,
        )


def _hit_labels(hits: list[str]) -> list[str]:
    """The page label of each injected block: its first line, without brackets."""
    return [h.split("\n", 1)[0].strip("[]") for h in hits]


def _answer_node(plan: RetrievalPlan, wiki_hits: list[str], in_roster: bool) -> str:
    """The reading-diagram node that injects the plan's context and runs the model."""
    if plan.tier == "curado":
        return "Q13" if (wiki_hits and in_roster) else "Q14"
    if plan.tier == "crudo":
        return "Q16"
    return "Q15"


async def _pre_retrieval_turn(
    question: str, *, config, db_path, workspace, history, language, run_agent, on_trace,
) -> str:
    """The body of `pre_retrieval_answer`, inside the turn's root span."""
    R = tracing.READING
    refusal = refusal_for(language)
    tracing.mark("Q2", R, normalized=_normalize(question))
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
    coverage_roster = set(vocabulary) | set(concept_page_names(db_path))
    in_roster = mentions_known_data(question, coverage_roster, aliases)
    tracing.mark("Q5", R, answer=in_roster, roster_size=len(coverage_roster),
                 aliases=len(aliases))

    off_limits = is_off_limits(question, config.off_limits)
    tracing.mark("Q3", R, answer=off_limits)
    if off_limits:
        wiki_hits, doc_hits, collection_hits = [], [], []
    else:
        with tracing.node("Q6", R, fts_query=_fts_query(question, language)) as q6:
            wiki_hits = retrieve_wiki(db_path, question, language=language)
            q6.set(pages=_hit_labels(wiki_hits))
        if not wiki_hits and in_roster:
            with tracing.node("Q8", R, fts_query=_fts_query(question, language)) as q8:
                doc_hits = retrieve_source_chunks(db_path, question, language=language)
                q8.set(fragments=_hit_labels(doc_hits))
        else:
            doc_hits = []
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
        cause = "R1" if off_limits else ("R2b" if in_roster else "R2a")
        tracing.mark(cause, R, final_answer=refusal)
        if on_trace:
            on_trace(raw="", final=refusal, result=None, refusal_substituted=True)
        return refusal

    prompt = (
        _INJECT_TEMPLATE.format(context=plan.context, question=question)
        if plan.context else question
    )
    with tracing.node(
        _answer_node(plan, wiki_hits, in_roster), R,
        tier=plan.tier or "data", context_chars=len(plan.context or ""),
    ) as answer_node:
        answer_node.payload("prompt", prompt, channel="prompts")
        result = await run_agent(prompt, history)
        raw = result.output
        messages = result.all_messages()
        answer_node.set(raw_output=raw)

    # Tier 2 (raw doc): verify the answer actually draws on the retrieved chunk.
    if plan.verify:
        score = coverage(raw, plan.context or "")
        supported = score >= _MIN_COVERAGE
        tracing.mark("Q17", R, answer=supported, coverage=round(score, 3),
                     min_coverage=_MIN_COVERAGE)
        if not supported:
            tracing.mark("R3", R, final_answer=refusal)
            if on_trace:
                on_trace(raw=raw, final=refusal, result=result, refusal_substituted=True)
            return refusal

    # Grounding: injected context IS the grounding for Tier 1/2 (skip the
    # tool-based guardrail, which would wrongly refuse a context-only answer);
    # for a data/advisory question (no injected context) keep the guardrail.
    if plan.context is None:
        gated = enforce_grounding(raw, messages, refusal=refusal)
        grounded = not (gated == refusal and raw != refusal)
        tracing.mark("Q19", R, answer=grounded)
        if not grounded:
            tracing.mark("R4", R, final_answer=refusal)
    else:
        gated = raw
    refusal_substituted = gated == refusal and raw != refusal

    tabled = answer_with_table(gated, messages)
    answer = ensure_citation(tabled, messages)
    citation_added = answer != tabled
    if plan.tier == "crudo" and answer != refusal:
        answer = f"{answer}\n\n{_TIER2_WARNING}"
        tracing.mark("Q18", R)

    if not refusal_substituted:
        tracing.mark("Q21", R, final_answer=answer, citation_added=citation_added)
    if on_trace:
        on_trace(raw=raw, final=answer, result=result, refusal_substituted=refusal_substituted)
    return answer
