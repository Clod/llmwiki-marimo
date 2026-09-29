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
from dataclasses import dataclass, field, replace

from domain import tracing
from domain.chat.dataset_tools import format_rows_as_table
from domain.chat.guardrail import has_grounding
from domain.chat.refusal import (
    Refusal,
    RefusalCause,
    fixed_sentence,
    render,
    roster_suggestions,
    search_suggestions,
)
from domain.chat.overlap import coverage
from domain.chat.postprocess import answer_with_table, ensure_citation
from domain.chat.scope import (
    _mentions,
    _normalize,
    advisory_intent,
    collection_intent,
    is_off_limits,
    off_limits_term,
    mentions_known_data,
)
from domain.datasets.frontmatter import split_frontmatter
from domain.datasets.models import DatasetSource
from domain.datasets.source import LocalMarkdownSource
from domain.text.stemming import stem_text
from domain.text.stopwords import STOPWORDS, stopwords
from domain.tools.search import search_chunks
from domain.tools.wiki_fs import concept_page_names
from domain.wiki_settings import language_for_db

_INJECT_TEMPLATE = (
    "Respondé la pregunta usando EXCLUSIVAMENTE el siguiente contexto recuperado "
    "del wiki, citando la fuente. Si el contexto no alcanza para responder, decilo.\n\n"
    "{context}\n\n---\nPregunta: {question}"
)
# Default Tier-2 threshold, used when the config carries none; a wiki sets its
# own with `[pre_retrieval] min_coverage` (design_stemming.md, decision 12).
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
# The stop-word sets live in domain/text/stopwords.py, shared with the Tier-2
# verification (overlap.py). The two names stay importable from here.
_STOPWORDS = STOPWORDS
_stopwords = stopwords


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
        search_chunks(db_path, _fts_query(query, language), limit=limit, scope="wiki", language=language)
    )


def retrieve_source_chunks(
    db_path: str, query: str, *, limit: int = 4, language: str | None = None
) -> list[str]:
    """Top raw source-document chunks for `query` (Tier 2). Empty if none."""
    return _format_hits(
        search_chunks(db_path, _fts_query(query, language), limit=limit, scope="sources", language=language)
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


# Header of the key-names block of node Q7d, per content language.
_KEY_NAMES_HEADER = {
    "es": "[datasets/{categoria}.md] Valores de `{clave}` con datos en este dataset:",
    "en": "[datasets/{categoria}.md] Values of `{clave}` with data in this dataset:",
}
# The line the code appends when the model asked for a key the category lacks.
_KEYS_OFFERED = {
    "es": "Datos disponibles en `{categoria}` ({clave}): {keys}.",
    "en": "Data available in `{categoria}` ({clave}): {keys}.",
}


@dataclass(frozen=True)
class DatasetInjection:
    """The dataset context a Tier-1 turn adds next to the wiki pages (decision 16).

    blocks:     the text blocks to inject — a row table (node Q7c) or a list of
                key names (node Q7d) per category the question names.
    references: the dataset files whose ROWS were injected, for the citation.
    fuentes:    the external origins of those rows, for the citation.
    """

    blocks: list[str]
    references: list[str]
    fuentes: list[str]
    # Categories injected as key names (Q7d): categoria -> (key column, keys).
    key_names: dict[str, tuple[str, list[str]]] = field(default_factory=dict)


def _names_term(
    question: str, term: str, data_aliases: dict[str, list[str]], *,
    language: str, stem_term: bool,
) -> bool:
    """True if the question names `term`, or one of its data aliases.

    A category (`stem_term`) is compared by stem; a dataset key by whole
    normalized word; an alias always by stem (design_stemming.md, decision 15).
    """
    matched = (
        mentions_known_data(question, [term], language=language) if stem_term
        else _mentions(_normalize(question), term)
    )
    return matched or mentions_known_data(
        question, [], data_aliases.get(term, []), language=language,
    )


def dataset_injection(
    question: str,
    source: DatasetSource,
    data_aliases: dict[str, list[str]],
    language: str | None,
) -> DatasetInjection:
    """Nodes Q7a–Q7d: read the dataset the question names, for a Tier-1 context.

    A category is named when the question names the category, one of its keys,
    or an alias of either: categories and aliases by stem, keys by whole word
    (decision 15). For each named category: when the question names
    keys of it, the code injects those keys' rows (Q7c); otherwise it injects
    only the category's key names, without values (Q7d), so the model can say
    which keys have data — "Banco Comafi" is not a key of `plazo_fijo`, and the
    eight banks that are become visible.
    """
    R = tracing.READING
    stem_language = language or "en"
    named: list[tuple[str, list[str], list[str], list]] = []
    for categoria in source.categories():
        rows = source.query(categoria)
        keys = list(dict.fromkeys(row.clave for row in rows))
        named_keys = [
            k for k in keys
            if _names_term(question, k, data_aliases, language=stem_language, stem_term=False)
        ]
        if named_keys or _names_term(
            question, categoria, data_aliases, language=stem_language, stem_term=True,
        ):
            named.append((categoria, keys, named_keys, rows))
    tracing.mark("Q7a", R, answer=bool(named), categories=[n[0] for n in named])

    header = _KEY_NAMES_HEADER.get((language or "es").lower(), _KEY_NAMES_HEADER["en"])
    blocks: list[str] = []
    references: list[str] = []
    fuentes: list[str] = []
    key_names: dict[str, tuple[str, list[str]]] = {}
    for categoria, keys, named_keys, rows in named:
        tracing.mark("Q7b", R, category=categoria, answer=bool(named_keys), keys=named_keys)
        if named_keys:
            key_rows = [row for row in rows if row.clave in named_keys]
            blocks.append(f"[datasets/{categoria}.md]\n{format_rows_as_table(categoria, key_rows)}")
            references.append(f"{categoria}.md")
            fuentes.extend(row.fuente for row in key_rows)
            tracing.mark("Q7c", R, category=categoria, keys=named_keys, rows=len(key_rows))
        else:
            clave = str(source.attributes(categoria).get("clave") or "clave")
            key_names[categoria] = (clave, keys)
            blocks.append(
                header.format(categoria=categoria, clave=clave) + "\n"
                + "\n".join(f"- {k}" for k in keys)
            )
            tracing.mark("Q7d", R, category=categoria, key_names=keys)
    return DatasetInjection(blocks, references, list(dict.fromkeys(fuentes)), key_names)


def _requested_keys(messages: list) -> list[tuple[str, str]]:
    """(categoria, clave) of every `query_dataset` call that named a key."""
    requested: list[tuple[str, str]] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if getattr(part, "part_kind", None) != "tool-call":
                continue
            if getattr(part, "tool_name", None) != "query_dataset":
                continue
            try:
                args = part.args_as_dict()
            except Exception:  # noqa: BLE001 — unparseable args name no key
                continue
            if args.get("categoria") and args.get("clave"):
                requested.append((str(args["categoria"]), str(args["clave"])))
    return requested


def offer_available_keys(
    answer: str, messages: list, injection: DatasetInjection, language: str | None,
) -> str:
    """Append the keys that have data when the model asked for one that does not.

    Applies to a category injected as key names (Q7d): when the model called
    `query_dataset` with a key that category lacks — "Banco Comafi" in
    `plazo_fijo` — and the answer names none of the category's keys, the code
    appends one line listing them, so the user can ask again for one that has
    data. A concept question makes no such call, so its answer is left alone.
    """
    template = _KEYS_OFFERED.get((language or "es").lower(), _KEYS_OFFERED["en"])
    lines: list[str] = []
    for categoria, clave in _requested_keys(messages):
        if categoria not in injection.key_names:
            continue
        column, keys = injection.key_names[categoria]
        if clave in keys or any(k in answer for k in keys):
            continue
        line = template.format(categoria=categoria, clave=column, keys=", ".join(keys))
        if line not in lines:
            lines.append(line)
    if not lines:
        return answer
    return f"{answer.rstrip()}\n\n" + "\n".join(lines)


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
    if in_roster:
        tracing.mark("Q7", R, answer=bool(wiki_hits), wiki_pages=len(wiki_hits))
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
    stem_language = language or language_for_db(db_path)
    tracing.mark("Q2", R, normalized=_normalize(question))

    # The blacklist first (design_stemming.md, decision 14): a blacklisted
    # question is refused before the datasets are read or the index searched.
    # The blacklist compares whole normalized words, never stems (decision 5).
    off_limits = is_off_limits(question, config.off_limits)
    tracing.mark("Q3", R, answer=off_limits)

    has_data = in_roster = False
    categories: set[str] = set()
    wiki_hits: list[str] = []
    doc_hits: list[str] = []
    collection_hits: list[str] = []
    source = LocalMarkdownSource(workspace / "datasets")
    if not off_limits:
        tracing.mark("Q4", R, stemmed=stem_text(question, stem_language))
        aliases = [alias for names in config.data_aliases.values() for alias in names]
        vocabulary = build_vocabulary(source)
        categories = set(source.categories())
        # Dataset categories, concept-page titles and aliases are compared by
        # stem; dataset keys (proper names, codes) by whole word (decision 15).
        keys = vocabulary - categories
        # Route to the tools either by a NAMED data term or by generic advisory
        # intent ("$1M, 3 meses, ¿qué alternativas?") — the latter names no
        # instrument but still belongs on the query_dataset/estimar_alternativas
        # path, not a refusal.
        has_data = mentions_known_data(
            question, categories, aliases, language=stem_language, exact=keys,
        ) or advisory_intent(question)
        # "In the padrón" = the question names something the wiki actually covers
        # (a dataset term OR a concept page name OR a known alias). Tier-2 (raw
        # docs) is allowed ONLY for a covered topic, so an uncovered question
        # can't pull a tangential chunk as a fig leaf — that was the CEDEARs leak.
        roster_terms = categories | set(concept_page_names(db_path))
        in_roster = mentions_known_data(
            question, roster_terms, aliases, language=stem_language, exact=keys,
        )
        tracing.mark("Q5", R, answer=in_roster, roster_size=len(roster_terms) + len(keys),
                     aliases=len(aliases))

        # The wiki search runs only for a question in the roster: the plan uses
        # its pages only then (decision 14).
        if in_roster:
            with tracing.node("Q6", R, fts_query=_fts_query(question, language)) as q6:
                wiki_hits = retrieve_wiki(db_path, question, language=stem_language)
                q6.set(pages=_hit_labels(wiki_hits))
            if not wiki_hits:
                with tracing.node("Q8", R, fts_query=_fts_query(question, language)) as q8:
                    doc_hits = retrieve_source_chunks(db_path, question, language=stem_language)
                    q8.set(fragments=_hit_labels(doc_hits))
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

    def refuse(node_id: str, refusal: Refusal, *, raw: str = "", result=None) -> str:
        """Render the refusal, record its node, and report it to the caller."""
        text = render(refusal, language)
        tracing.mark(node_id, R, cause=refusal.cause.value, final_answer=text,
                     suggestions=[title for title, _ in refusal.suggestions])
        if on_trace:
            on_trace(raw=raw, final=text, result=result, refusal_substituted=True)
        return text

    def suggestions() -> list[tuple[str, str]]:
        """Roster pages for a question in the roster (decision 13), else the search."""
        if in_roster:
            return roster_suggestions(db_path, question, categories, stem_language)
        return search_suggestions(db_path, question, stem_language)

    if plan.action == "refuse":
        if off_limits:
            return refuse("R1", Refusal(
                RefusalCause.BLACKLIST, term=off_limits_term(question, config.off_limits),
            ))
        if in_roster:
            return refuse("R2b", Refusal(RefusalCause.NOTHING_FOUND, suggestions=suggestions()))
        return refuse("R2a", Refusal(RefusalCause.NOT_IN_ROSTER, suggestions=suggestions()))

    # Tier 1 from wiki pages: the code adds the dataset the question names
    # (decision 16) — the pages hold no values, and the model, told to answer
    # from the context, does not reliably call query_dataset on its own.
    injection = DatasetInjection([], [], [])
    if _answer_node(plan, wiki_hits, in_roster) == "Q13":
        injection = dataset_injection(question, source, config.data_aliases, language)
        if injection.blocks:
            plan = replace(plan, context="\n\n".join([plan.context or "", *injection.blocks]))

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
        min_coverage = getattr(config, "min_coverage", _MIN_COVERAGE)
        score = coverage(raw, plan.context or "", stem_language)
        supported = score >= min_coverage
        tracing.mark("Q17", R, answer=supported, coverage=round(score, 3),
                     min_coverage=min_coverage)
        if not supported:
            document = _hit_labels(doc_hits)[0].rsplit("/", 1)[-1] if doc_hits else ""
            return refuse("R3", Refusal(
                RefusalCause.UNSUPPORTED, document=document, suggestions=suggestions(),
            ), raw=raw, result=result)

    # Grounding: injected context IS the grounding for Tier 1/2 (skip the
    # tool-based guardrail, which would wrongly refuse a context-only answer);
    # for a data/advisory question (no injected context) keep the guardrail.
    if plan.context is None:
        grounded = has_grounding(messages)
        tracing.mark("Q19", R, answer=grounded)
        if not grounded:
            return refuse("R4", Refusal(RefusalCause.UNGROUNDED, suggestions=suggestions()),
                          raw=raw, result=result)
    gated = raw

    # The model's own refusal (cause 5): the code keeps the model's text and
    # adds the cause line and the suggestions (decision 8).
    opens_with_fixed = raw.lstrip().startswith(fixed_sentence(language))
    tracing.mark("Q20", R, answer=opens_with_fixed)
    if opens_with_fixed:
        return refuse("R5", Refusal(
            RefusalCause.MODEL, model_text=raw, suggestions=suggestions(),
        ), raw=raw, result=result)
    refusal_substituted = False

    with_table = answer_with_table(gated, messages)
    tabled = offer_available_keys(with_table, messages, injection, language)
    keys_offered = tabled != with_table
    answer = ensure_citation(
        tabled, messages,
        extra_references=injection.references, extra_fuentes=injection.fuentes,
    )
    citation_added = answer != tabled
    if plan.tier == "crudo":
        answer = f"{answer}\n\n{_TIER2_WARNING}"
        tracing.mark("Q18", R)

    if not refusal_substituted:
        tracing.mark("Q21", R, final_answer=answer, citation_added=citation_added,
                     keys_offered=keys_offered)
    if on_trace:
        on_trace(raw=raw, final=answer, result=result, refusal_substituted=refusal_substituted)
    return answer
