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
from dataclasses import dataclass, field
from typing import Any

from domain import tracing
from domain.chat.dataset_tools import format_rows_as_table
from domain.chat.refusal import (
    Refusal,
    render,
    roster_suggestions,
    search_suggestions,
)
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
    blocks:  the blocks `context` joins, one per page or fragment.
    """

    action: str
    tier: str | None
    context: str | None
    verify: bool
    blocks: tuple[str, ...] = ()


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
        tracing.mark("Q7", R, answer=bool(wiki_hits), wiki_fragments=len(wiki_hits))
    if wiki_hits and in_roster:
        return RetrievalPlan("invoke", "curado", "\n\n".join(wiki_hits), False, tuple(wiki_hits))
    tracing.mark("Q9", R, answer=bool(collection_hits), collection_pages=len(collection_hits))
    if collection_hits:
        return RetrievalPlan("invoke", "curado", "\n\n".join(collection_hits), False, tuple(collection_hits))
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
        return RetrievalPlan("invoke", "crudo", "\n\n".join(doc_hits), True, tuple(doc_hits))
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
    open_page: str | None = None,
    on_context=None,
    condense=None,
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
    """
    with tracing.root(
        "turn", workspace, mode="pre-retrieval", question=question, language=language,
    ):
        return await _pre_retrieval_turn(
            question, config=config, db_path=db_path, workspace=workspace,
            history=history, language=language, run_agent=run_agent, on_trace=on_trace,
            open_page=open_page, on_context=on_context, condense=condense,
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

    One object per turn. `build_roster`, `blacklist`, `match_roster`, `screen`,
    `suggestions` and `refuse` read and write it; so do the steps of the input
    and the output workflows (`domain/chat/workflow/`).
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
    # What the gate and the search examine: the question, or its rewrite (Q5b).
    search: str = ""
    off_limits: bool = False
    # The coverage roster (`build_roster`).
    aliases: list[str] = field(default_factory=list)
    categories: set[str] = field(default_factory=set)
    keys: set[str] = field(default_factory=set)
    roster_terms: set[str] = field(default_factory=set)
    # The data detection and the roster match (`match_roster`).
    has_data: bool = False
    matched: list[str] = field(default_factory=list)
    in_roster: bool = False
    open_block: str | None = None
    wiki_hits: list[str] = field(default_factory=list)
    doc_hits: list[str] = field(default_factory=list)
    collection_hits: list[str] = field(default_factory=list)
    plan: RetrievalPlan | None = None
    injection: DatasetInjection = field(default_factory=lambda: DatasetInjection([], [], []))

    def __post_init__(self) -> None:
        if not self.search:
            self.search = self.question


def build_roster(turn: PreRetrievalTurn) -> None:
    """The coverage roster of node Q5: the aliases, the dataset categories and
    keys, and the roster terms (categories and concept-page titles)."""
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


def match_roster(turn: PreRetrievalTurn, text: str) -> None:
    """Node Q5 on `text`: the data detection (`has_data`) and the roster match
    (`matched`, `in_roster`). `build_roster` runs first."""
    # Route to the tools either by a NAMED data term or by generic advisory
    # intent ("$1M, 3 meses, ¿qué alternativas?") — the latter names no
    # instrument but still belongs on the query_dataset/estimar_alternativas
    # path, not a refusal.
    turn.has_data = mentions_known_data(
        text, turn.categories, turn.aliases, language=turn.stem_language, exact=turn.keys,
    ) or advisory_intent(text)
    turn.matched = matched_terms(
        text, sorted(turn.roster_terms), turn.aliases, language=turn.stem_language,
        exact=sorted(turn.keys),
    )
    turn.in_roster = bool(turn.matched)


def screen(turn: PreRetrievalTurn, text: str) -> None:
    """The blacklist, the data detection and the roster on `text`: `blacklist`,
    then `match_roster` when `text` names no blacklist term. Node Q5b runs it on
    the rewrite; nodes Q3 and Q5 run its two parts on the question."""
    if not blacklist(turn, text):
        match_roster(turn, text)


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
    open_page: str | None = None, on_context=None, condense=None,
) -> str:
    """The body of `pre_retrieval_answer`, inside the turn's root span.

    The input workflow (`domain/chat/workflow/input_checks.py`) runs the checks
    of the question. When it ends in a refusal, the model is not called. Else
    the agent runs here, between the two workflows, and the output workflow
    (`domain/chat/workflow/output_checks.py`) runs the checks of the answer.
    """
    from domain.chat.workflow import input_checks, output_checks

    turn = PreRetrievalTurn(
        question, config, db_path, workspace, history, language,
        stem_language=language or language_for_db(db_path),
        source=LocalMarkdownSource(workspace / "datasets"),
        open_page=open_page, condense=condense, on_trace=on_trace,
    )
    outcome = await input_checks.graph.run(state=turn, inputs=turn)
    if isinstance(outcome, input_checks.Refused):
        return outcome.text

    plan = turn.plan
    asked = with_open_page(question, open_page if turn.open_block is not None else None, language)
    prompt = (
        load_prompt("inject_template", language).format(context=plan.context, question=asked)
        if plan.context else asked
    )
    if on_context is not None:
        on_context([context_passage(block) for block in plan.blocks])
    with tracing.node(
        outcome.node, tracing.READING,
        tier=plan.tier or "data", context_chars=len(plan.context or ""),
    ) as answer_node:
        answer_node.payload("prompt", prompt, channel="prompts")
        result = await run_agent(prompt, history)
        answer_node.set(raw_output=result.output)

    checks = output_checks.AnswerChecks(
        result.output, result.all_messages(), language, result=result, turn=turn,
    )
    return await output_checks.graph.run(state=checks, inputs=checks)
