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
from pathlib import Path
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from domain import tracing
from domain.chat.config import DEFAULT_NUMBER_TOLERANCE
from domain.chat.dataset_tools import format_rows_as_table
from domain.chat.refusal import (
    Refusal,
    render,
    roster_suggestions,
    search_suggestions,
)
from domain.chat.intent import IntentLists, base_collection, load_intent
from domain.chat.scope import (
    _mentions,
    _normalize,
    advisory_intent,
    # The input workflow reads it through this module, where the workshop
    # replaces it (`mock.patch.object(preretrieval, "collection_intent", ...)`).
    collection_intent,  # noqa: F401
    is_off_limits,
    matched_terms,
    mentions_known_data,
)
from domain.datasets.frontmatter import split_frontmatter
from domain.prompts import load_prompt
from domain.datasets.models import DatasetSource
from domain.datasets.source import LocalMarkdownSource
from domain.text.stopwords import STOPWORDS, stopwords
from domain.tools.search import search_chunks
from domain.tools.wiki_fs import concept_page_names
from domain.verify.models import ContextPassage
from domain.wiki_settings import language_for_db

# Default Tier-2 threshold, used when the config carries none; a wiki sets its
# own with `[pre_retrieval] min_coverage` (design_stemming.md, decision 12).
_MIN_COVERAGE = 0.2


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


TOPIC_CANDIDATES = 3


def retrieve_wiki(
    db_path: str, query: str, *, limit: int = 6, language: str | None = None,
    topics: Iterable[str] = (),
) -> list[str]:
    """Top curated-wiki chunks for `query` (Tier 1). Empty list if none.

    `topics` are the roster terms the question names (node Q5). The query joins its
    words with OR, and BM25 adds up the words that match: in "¿plazo fijo o FCI money
    market?", the three words of one topic outrank the two of the other, and the six
    hits can all belong to one topic. Each topic is searched alone. Its pages are its
    first `TOPIC_CANDIDATES` pages, plus the page whose title is the topic. A topic is
    missing when none of its pages is among the hits. For each missing topic, the
    chunk of the page titled with the topic, or else its best chunk, replaces the
    last hit, so the number of hits stays at most `limit`."""
    hits = _format_hits(
        search_chunks(db_path, _fts_query(query, language), limit=limit, scope="wiki", language=language)
    )
    added: list[str] = []
    for topic in topics:
        rows = [row for row in search_chunks(
            db_path, _fts_query(topic.replace("_", " "), language), limit=TOPIC_CANDIDATES * 4,
            scope="wiki", language=language) if (row.get("content") or "").strip()]
        if not rows or len(added) >= limit - 1:
            continue
        titled = [row for row in rows if _normalize(row.get("title") or "") == _normalize(topic)]
        candidates = _format_hits(rows)
        pages = set(list(dict.fromkeys(_hit_labels(candidates)))[:TOPIC_CANDIDATES])
        pages.update(_hit_labels(_format_hits(titled)))
        if not pages.intersection(_hit_labels(hits[:limit - len(added)] + added)):
            added.append(_format_hits(titled or rows)[0])
    return hits[:limit - len(added)] + added if added else hits


def alias_titles(topics: Iterable[str], data_aliases: dict[str, list[str]]) -> list[str]:
    """`topics` with each alias replaced by the name it stands for, without repeats.

    A page alias is not in the text of its page: "Culocenizón" is an alias of
    "Cenicienta" (`aliases.generated.toml`), and no page of the wiki holds the word.
    The topic search of `retrieve_wiki` finds the page by its title."""
    names = {_normalize(alias): name for name, aliases in data_aliases.items() for alias in aliases}
    return list(dict.fromkeys(names.get(_normalize(topic), topic) for topic in topics))


# The line that tells the model which aliases of the question the search replaced,
# per content language. Without it, "¿qué es el billete verde?" gets the pages of the
# dollar and no text that says "billete verde" is the dollar.
_SYNONYMS = {
    "es": ("Sinónimos de esta wiki que usa la pregunta: {pairs}.", "«{alias}» es otro nombre de «{name}»"),
    "en": ("Synonyms of this wiki that the question uses: {pairs}.", '"{alias}" is another name for "{name}"'),
}


def synonyms_line(matched: Iterable[str], data_aliases: dict[str, list[str]],
                  language: str | None) -> str:
    """The line that names each alias in `matched` with the name it stands for, or ""
    when the question names no alias."""
    names = {_normalize(alias): (alias, name) for name, aliases in data_aliases.items() for alias in aliases}
    pairs = [names[_normalize(term)] for term in matched if _normalize(term) in names]
    if not pairs:
        return ""
    line, pair = _SYNONYMS.get((language or "es").lower(), _SYNONYMS["en"])
    return line.format(pairs="; ".join(pair.format(alias=a, name=n) for a, n in dict.fromkeys(pairs)))


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


def named_categories(
    question: str,
    source: DatasetSource,
    data_aliases: dict[str, list[str]],
    language: str | None,
) -> list[tuple[str, list[str], list[str], list]]:
    """The categories the question names (node Q7a), in the order of the source.

    One tuple per named category: the category, its keys, the keys the question
    names, and the rows of the category. A category is named when the question
    names the category, one of its keys, or an alias of either: categories and
    aliases by stem, keys by whole word (decision 15).
    """
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
    return named


def rows_block(categoria: str, named_keys: list[str], rows: list) -> tuple[str, str, list[str]]:
    """The rows of the named keys of a category (node Q7c): the block to inject,
    the dataset file to cite, and the origin of each injected row."""
    key_rows = [row for row in rows if row.clave in named_keys]
    block = f"[datasets/{categoria}.md]\n{format_rows_as_table(categoria, key_rows)}"
    return block, f"{categoria}.md", [row.fuente for row in key_rows]


def key_names_block(
    categoria: str, keys: list[str], source: DatasetSource, language: str | None,
) -> tuple[str, tuple[str, list[str]]]:
    """The key names of a category, without values (node Q7d): the block to
    inject, and the entry of `DatasetInjection.key_names` (key column, keys)."""
    header = _KEY_NAMES_HEADER.get((language or "es").lower(), _KEY_NAMES_HEADER["en"])
    clave = str(source.attributes(categoria).get("clave") or "clave")
    block = header.format(categoria=categoria, clave=clave) + "\n" + "\n".join(f"- {k}" for k in keys)
    return block, (clave, keys)


def dataset_injection(
    question: str,
    source: DatasetSource,
    data_aliases: dict[str, list[str]],
    language: str | None,
) -> DatasetInjection:
    """Read the dataset the question names, for a Tier-1 context.

    For each named category (`named_categories`): when the question names
    keys of it, the code injects those keys' rows (`rows_block`); otherwise it
    injects only the category's key names, without values (`key_names_block`),
    so the model can say which keys have data — "Banco Comafi" is not a key of
    `plazo_fijo`, and the eight banks that are become visible.

    The function records no node. The input workflow
    (`domain/chat/workflow/input_checks.py`) runs the same three parts as the
    nodes Q7a, Q7b, Q7c and Q7d.
    """
    blocks: list[str] = []
    references: list[str] = []
    fuentes: list[str] = []
    key_names: dict[str, tuple[str, list[str]]] = {}
    for categoria, keys, named_keys, rows in named_categories(question, source, data_aliases, language):
        if named_keys:
            block, reference, origins = rows_block(categoria, named_keys, rows)
            blocks.append(block)
            references.append(reference)
            fuentes.extend(origins)
        else:
            block, key_names[categoria] = key_names_block(categoria, keys, source, language)
            blocks.append(block)
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
    blocks:  the blocks `context` joins, one per page or fragment.
    """

    action: str
    tier: str | None
    context: str | None
    verify: bool
    blocks: tuple[str, ...] = ()


_REFUSE = RetrievalPlan(action="refuse", tier=None, context=None, verify=False)


def tier1_plan(hits: list[str]) -> RetrievalPlan:
    """The Tier-1 plan: inject the wiki pages, or the overview and the index."""
    return RetrievalPlan("invoke", "curado", "\n\n".join(hits), False, tuple(hits))


def data_plan() -> RetrievalPlan:
    """The plan of a data question: inject nothing, the model calls the data tools."""
    return RetrievalPlan("invoke", None, None, False)


def tier2_plan(hits: list[str]) -> RetrievalPlan:
    """The Tier-2 plan: inject the source fragments, and verify the answer against them."""
    return RetrievalPlan("invoke", "crudo", "\n\n".join(hits), True, tuple(hits))


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

    The function records no node. The input workflow
    (`domain/chat/workflow/input_checks.py`) takes the same decisions in the
    same order, and records each one as its node: Q7, Q9, Q10, Q11 and Q12.
    """
    if is_off_limits(question, off_limits):
        return _REFUSE
    if wiki_hits and in_roster:
        return tier1_plan(wiki_hits)
    if collection_hits:
        return tier1_plan(collection_hits)
    if has_data:
        # A question that names known data goes to the tools (query_dataset /
        # advisory) before any raw-doc fallback: the dataset value/date beats
        # answering from raw prose — and a lexical raw-doc hit would otherwise
        # divert "¿a cuánto está el billete verde?" to Tier-2 and starve it of
        # the actual number.
        return data_plan()
    if doc_hits and in_roster:
        return tier2_plan(doc_hits)
    return _REFUSE


async def pre_retrieval_answer(
    question: str,
    *,
    config,
    db_path: str,
    workspace,
    history: list | None = None,
    language: str | None,
    run_agent,
    on_trace=None,
    open_page: str | None = None,
    on_context=None,
    condense=None,
    conversation=(),
    on_figures=None,
    sentence_verifier: bool = False,
    prior: list | None = None,
) -> str:
    """Run one turn through the hybrid pre-retrieval flow and return the answer.

    `open_page` is the id of the page the user is reading (`concepts/x`), or
    None. That page is injected first, and a question asked while reading it
    counts as covered, so "¿cuánto rinde?" read next to the plazo fijo UVA page
    is answered from it (node Q5a).

    The CODE retrieves (curated wiki, then raw docs) and decides the plan; the
    model is invoked (via the injected async `run_agent(prompt, history)`) only
    when there's grounding to give it, with the retrieved context prepended.
    Tier-2 (raw-doc) answers are verified with lexical overlap and warned; the
    off-limits / nothing-found cases refuse WITHOUT invoking the model.

    `run_agent` is injected so this is testable without an LLM; it must return an
    object with `.output` (str) and `.all_messages()`. `on_trace`, if given, is
    called with (raw, final, result, refusal_substituted) for the chat trace.
    `on_context`, if given, is called once with the `ContextPassage` list the
    prompt injected, before the model runs; it is not called on a refusal made
    before the model. It does not depend on the trace.

    `condense`, if given, is an async `condense(question, history) -> str` that
    rewrites a follow-up into a question that stands alone (`domain/chat/condense.py`).
    It runs only when the question alone passes neither the coverage gate nor the
    data check and no page is open; the gate and the search then use the rewrite,
    and the model still answers the original question (node Q5b).

    `conversation` holds the `(origin, text)` sources of the conversation that
    authorise the figures of the answer (`figure_check.conversation_sources`).
    `on_figures`, if given, is called once with the `FigureCheck` of node C3; it is
    not called when the turn ends in a refusal, which does not reach C3.

    `sentence_verifier` is True when the sentence verifier will check the answer
    after it is shown. A Tier-2 answer then skips the containment check (node
    Q17): the sentence verifier is its only comparison against the fragments.

    `prior` holds the prior messages of the chat (`.role`, `.content`); the nodes
    C2a to C2d of the input workflow build the history of the model from them.
    A caller without `prior` passes the history already built in `history`, and
    C2a to C2d leave that history as it is.
    """
    with tracing.root(
        "turn", workspace, mode="pre-retrieval", question=question, language=language,
    ):
        return await _pre_retrieval_turn(
            question, config=config, db_path=db_path, workspace=workspace,
            history=[] if history is None else history, language=language, run_agent=run_agent,
            on_trace=on_trace, open_page=open_page, on_context=on_context, condense=condense,
            conversation=conversation, on_figures=on_figures,
            sentence_verifier=sentence_verifier, prior=prior,
        )


# The note that tells the model which page the user is reading, so a question
# that names no topic ("¿cuánto rinde?") is read as a question about that page.
_OPEN_PAGE_NOTE = {
    "es": "(El usuario está leyendo la página /wiki/{page}.md. Si la pregunta se "
          "refiere a «esto» o no nombra su tema, se refiere a esa página.)\n\n",
    "en": "(The user is reading the page /wiki/{page}.md. If the question says "
          "\"this\" or names no topic, it refers to that page.)\n\n",
}


def with_open_page(question: str, open_page: str | None, language: str | None) -> str:
    """`question` preceded by the open-page note, or unchanged without a page."""
    if not open_page:
        return question
    lang = "es" if (language or "en").lower().startswith("es") else "en"
    return _OPEN_PAGE_NOTE[lang].format(page=open_page) + question


def build_prompt(context: str | None, synonyms: str, asked: str, language: str | None) -> str:
    """The prompt that the model receives (node Q12b): the injected `context`, with
    the `synonyms` line first, in the inject template, or the question `asked`
    alone, after the synonyms line, when nothing is injected."""
    if context:
        context = f"{synonyms}\n\n{context}" if synonyms else context
        return load_prompt("inject_template", language).format(context=context, question=asked)
    return f"{synonyms}\n\n{asked}" if synonyms else asked


def _open_page_block(workspace, open_page: str | None) -> str | None:
    """The open page as an injectable block, labelled like a search hit."""
    if not open_page:
        return None
    path = workspace / "wiki" / f"{open_page}.md"
    if not path.is_file():
        return None
    _, body = split_frontmatter(path.read_text(encoding="utf-8"))
    return f"[/wiki/{open_page}.md]\n{body.strip()}" if body.strip() else None


def context_passage(block: str) -> ContextPassage:
    """The page and text of an injected block `[label]\\ntext`.

    A wiki page and a dataset file keep their label (`/wiki/concepts/x.md`,
    `datasets/x.md`); a source document is named by its file name.
    """
    match = re.match(r"\[([^\]\n]*)\]\s*(.*)", block, re.DOTALL)
    if not match:
        return ContextPassage("", block)
    label, text = match.group(1).strip(), match.group(2)
    if not label.lstrip("/").startswith(("wiki/", "datasets/")):
        label = label.rsplit("/", 1)[-1]
    return ContextPassage(label, text)


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


@dataclass
class PreRetrievalTurn:
    """The state of one pre-retrieval turn: the inputs, and what each check found.

    One object per turn. `ensure_roster`, `blacklist`, `detect_data`,
    `match_terms`, `suggestions` and `refuse` read and write it; so do the steps
    of the input and the output workflows (`domain/chat/workflow/`).
    """

    question: str
    config: Any
    db_path: str
    workspace: Any
    history: list
    language: str | None
    stem_language: str
    source: DatasetSource
    open_page: str | None = None
    condense: Any = None
    on_trace: Any = None
    # The sentence verifier checks the answer after it is shown: a Tier-2 answer
    # skips the containment check (node Q17).
    sentence_verifier: bool = False
    # What the gate and the search examine: the question, or its rewrite (Q5d).
    search: str = ""
    off_limits: bool = False
    # The text that the condenser returned (Q5c).
    rewrite: str = ""
    # The coverage roster (`ensure_roster`), built once per turn.
    roster_built: bool = False
    aliases: list[str] = field(default_factory=list)
    categories: set[str] = field(default_factory=set)
    keys: set[str] = field(default_factory=set)
    roster_terms: set[str] = field(default_factory=set)
    # The data detection (`detect_data`) and the roster match (`match_terms`).
    has_data: bool = False
    matched: list[str] = field(default_factory=list)
    in_roster: bool = False
    open_block: str | None = None
    wiki_hits: list[str] = field(default_factory=list)
    doc_hits: list[str] = field(default_factory=list)
    collection_hits: list[str] = field(default_factory=list)
    plan: RetrievalPlan | None = None
    intent: IntentLists | None = None   # resolved by `intent_lists`, once per turn
    injection: DatasetInjection = field(default_factory=lambda: DatasetInjection([], [], []))
    # The categories the question names (Q7a), not yet read, and the category
    # that Q7c or Q7d reads (Q7b): tuples of `named_categories`.
    named: list[tuple[str, list[str], list[str], list]] = field(default_factory=list)
    category: tuple[str, list[str], list[str], list] | None = None
    # The prior messages of the chat, from which C2a to C2d build `history`; None
    # when the caller passed `history` already built. `mode` is what C2 records.
    # `kept` is the history between C2a and C2d.
    prior: list | None = None
    mode: str = "pre-retrieval"
    kept: list = field(default_factory=list)
    # The question with the open-page note (C4), the synonyms line (Q12a) and
    # the prompt that the model receives (Q12b).
    asked: str = ""
    synonyms: str = ""
    prompt: str = ""

    def __post_init__(self) -> None:
        if not self.search:
            self.search = self.question

    @property
    def noted_page(self) -> str | None:
        """The page that the note of C4 names: the open page, when Q5a found it."""
        return self.open_page if self.open_block is not None else None


def ensure_roster(turn: PreRetrievalTurn) -> None:
    """The coverage roster of nodes Q4a and Q5: the aliases, the dataset
    categories and keys, and the roster terms (categories and concept-page
    titles). Built once per turn: the second call does nothing."""
    if turn.roster_built:
        return
    turn.roster_built = True
    turn.aliases = [alias for names in turn.config.data_aliases.values() for alias in names]
    vocabulary = build_vocabulary(turn.source)
    turn.categories = set(turn.source.categories())
    # Dataset categories, concept-page titles and aliases are compared by
    # stem; dataset keys (proper names, codes) by whole word (decision 15).
    turn.keys = vocabulary - turn.categories
    # "In the padrón" = the question names something the wiki actually covers
    # (a dataset term OR a concept page name OR a known alias). Tier-2 (raw
    # docs) is allowed ONLY for a covered topic, so an uncovered question
    # can't pull a tangential chunk as a fig leaf — that was the CEDEARs leak.
    turn.roster_terms = turn.categories | set(concept_page_names(turn.db_path))


def blacklist(turn: PreRetrievalTurn, text: str) -> bool:
    """Node Q3 on `text`: whether `text` names a blacklist term, by whole
    normalized word, never by stem (decision 5)."""
    turn.off_limits = is_off_limits(text, turn.config.off_limits)
    return turn.off_limits


def intent_lists(turn: PreRetrievalTurn) -> IntentLists:
    """The expression lists of the intent predicates for this turn: the `[intent]`
    section of the config, and the base lists for the keys it lacks. Resolved once
    per turn: the advisory base runs the activation of each domain."""
    if turn.intent is None:
        section = getattr(turn.config, "intent_section", {}) or {}
        language = getattr(turn.config, "language", None) or turn.language or "en"
        if turn.workspace is None:
            turn.intent = IntentLists(
                language=language, collection=tuple(section.get("collection", base_collection(language))),
            )
        else:
            turn.intent = load_intent(Path(turn.workspace), language, section)
    return turn.intent


def detect_data(turn: PreRetrievalTurn, text: str) -> None:
    """The data detection (`has_data`) on `text`: node Q4a on the question, node
    Q5f on the rewrite. `ensure_roster` runs first."""
    # Route to the tools either by a NAMED data term or by generic advisory
    # intent ("$1M, 3 meses, ¿qué alternativas?") — the latter names no
    # instrument but still belongs on the query_dataset/estimar_alternativas
    # path, not a refusal.
    intent = intent_lists(turn)
    turn.has_data = mentions_known_data(
        text, turn.categories, turn.aliases, language=turn.stem_language, exact=turn.keys,
    ) or advisory_intent(text, intent.advisory, intent.advisory_shapes)


def match_terms(turn: PreRetrievalTurn, text: str) -> None:
    """The roster match (`matched`, `in_roster`) on `text`: node Q5 on the
    question, node Q5g on the rewrite. `ensure_roster` runs first."""
    turn.matched = matched_terms(
        text, sorted(turn.roster_terms), turn.aliases, language=turn.stem_language,
        exact=sorted(turn.keys),
    )
    turn.in_roster = bool(turn.matched)


def suggestions(turn: PreRetrievalTurn) -> list[tuple[str, str]]:
    """Roster pages for a question in the roster (decision 13), else the search."""
    if turn.in_roster:
        return roster_suggestions(turn.db_path, turn.search, turn.categories, turn.stem_language)
    return search_suggestions(turn.db_path, turn.search, turn.stem_language)


def refuse(node_id: str, turn: PreRetrievalTurn, refusal: Refusal, *, raw: str = "", result=None) -> str:
    """Render the refusal, record its node, and report it to the caller."""
    text = render(refusal, turn.language)
    tracing.mark(node_id, tracing.READING, cause=refusal.cause.value, final_answer=text,
                 suggestions=[title for title, _ in refusal.suggestions])
    if turn.on_trace:
        turn.on_trace(raw=raw, final=text, result=result, refusal_substituted=True)
    return text


async def _pre_retrieval_turn(
    question: str, *, config, db_path, workspace, history, language, run_agent, on_trace,
    open_page: str | None = None, on_context=None, condense=None, conversation=(), on_figures=None,
    sentence_verifier: bool = False, prior: list | None = None,
) -> str:
    """The body of `pre_retrieval_answer`, inside the turn's root span.

    The input workflow (`domain/chat/workflow/input_checks.py`, `graph`) builds
    the history (C1 to C2d), runs the checks of the question and builds the
    prompt (C4, Q12a, Q12b). When the input workflow ends in a refusal, the model
    is not called. Else the agent runs here, between the two workflows, and
    records Q13, Q14, Q15 or Q16. The output workflow
    (`domain/chat/workflow/output_checks.py`, `graph`) runs the checks of the
    answer and the additions Q20a to Q20d.
    """
    from domain.chat.workflow import input_checks, output_checks

    turn = PreRetrievalTurn(
        question, config, db_path, workspace, history, language,
        stem_language=language or language_for_db(db_path),
        source=LocalMarkdownSource(workspace / "datasets"),
        open_page=open_page, condense=condense, on_trace=on_trace,
        sentence_verifier=sentence_verifier, prior=prior,
    )
    outcome = await input_checks.graph.run(state=turn, inputs=turn)
    if isinstance(outcome, input_checks.Refused):
        return outcome.text

    # The nodes C4, Q12a and Q12b of the input workflow built the prompt.
    plan, prompt = turn.plan, turn.prompt
    if on_context is not None:
        on_context([context_passage(block) for block in plan.blocks])
    with tracing.node(
        outcome.node, tracing.READING,
        tier=plan.tier or "data", context_chars=len(plan.context or ""),
    ) as answer_node:
        answer_node.payload("prompt", prompt, channel="prompts")
        result = await run_agent(prompt, turn.history)
        answer_node.set(raw_output=result.output)

    checks = output_checks.AnswerChecks(
        result.output, result.all_messages(), language, result=result, turn=turn,
        tolerance=getattr(config, "number_tolerance", DEFAULT_NUMBER_TOLERANCE),
        conversation=tuple(conversation),
    )
    answer = await output_checks.graph.run(state=checks, inputs=checks)
    if on_figures is not None and checks.figures is not None:
        on_figures(checks.figures)
    return answer
