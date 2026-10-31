"""Deterministic post-processing of chat answers.

Runs AFTER the guardrail in the read app's respond(). It provides two guarantees
the system prompt alone could NOT hold under multi-turn history priming (gpt-4o,
temp=0 still drifts to prose / drops citations once a conversation accumulates):

  - `answer_with_table`: if the advisory tool ran, its verbatim markdown table
    (ranked options + `fuente` column + nominal/real disclaimer + "no estimable"
    table) is APPENDED below the model's answer. The table therefore never
    depends on the model reproducing it.
  - `ensure_citation`: if the answer names no source but the run DELIBERATELY
    used one (read a wiki page, queried a dataset), an honest `Fuente:` line is
    appended. Search results are NOT counted — "buscar ≠ usar" — to avoid
    citing candidate pages the model only listed but never used.

These are pure functions over the run's message log (`result.all_messages()`):
no LLM, no I/O. The advisory table and the page/dataset source names already
live in the tools' return values that pydantic-ai records — we read them back.

Known limitation (deferred, see project memory 'chat grounding post-process'):
`ensure_citation` names what was deliberately retrieved; it does not verify that
the source actually supports every claim (answer-vs-source verification). If we
observe over-citing, add a lenient lexical-overlap second filter.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from collections.abc import Collection, Iterable

from pydantic_ai.messages import ModelMessage

from domain.chat.guardrail import is_tool_result
from domain.extensions import roles

if TYPE_CHECKING:
    from domain.verify.models import ContextPassage

# Tools whose returns represent a DELIBERATE use of a source (not a search
# candidate): the page the model opened (READ_TOOL), and the dataset values it
# looked up. The dataset tools are the evidence tools of `domain.extensions.roles()`
# other than READ_TOOL: `query_dataset` and the data tools of the domains, which
# return the `format_rows_as_table` table. search_wiki_fts / search_source_chunks
# are intentionally excluded.
READ_TOOL = "read_wiki_page"

_WIKI_PAGE_RE = re.compile(r"\[wiki page:\s*([^\]]+)\]")
# query_dataset returns start with: **N dataset row(s)** for categoria='dolar':
_DATASET_CAT_RE = re.compile(r"categoria='([^']+)'")

# A tool "return" that means "nothing found / not applicable" is not substantive.
_NEGATIVE_MARKERS = (
    "no dataset rows found",
    "no hay opciones",
    "no options",
    "page not found",
    "invalid path",
    "error reading",
    "no wiki pages found",
    "no results found",
)


def _tool_returns(messages: list[ModelMessage]):
    for message in messages or []:
        for part in getattr(message, "parts", []):
            if is_tool_result(part):
                yield part


def _is_substantive(content: str) -> bool:
    low = content.strip().lower()
    return bool(low) and not any(marker in low for marker in _NEGATIVE_MARKERS)


# ── advisory table ───────────────────────────────────────────────────────────

def advisory_table(messages: list[ModelMessage]) -> str | None:
    """Verbatim markdown of the LAST substantive return of a tool whose role is
    `verbatim` (`domain.extensions.roles()`; the finance advisory), or None if no such
    tool ran / returned nothing usable."""
    verbatim = roles().verbatim
    table: str | None = None
    for part in _tool_returns(messages):
        if part.tool_name in verbatim:
            content = str(part.content)
            if _is_substantive(content):
                table = content.strip()
    return table


def _without_copy(answer: str, table: str) -> str:
    """`answer` without the parts it copied from the tool's `table`.

    A copied table is a run of lines that start with `|`, from a line equal to a
    header row of `table` (the first row of each of its tables): it is removed with
    every figure in it, changed or not. Any other line equal to a line of `table`
    (its title, its warning about nominal figures) is removed too. A table whose
    header the model changed is not found, and stays."""
    lines = [line.strip() for line in table.splitlines()]
    headers = {line for previous, line in zip([""] + lines, lines)
               if line.startswith("|") and not previous.startswith("|")}
    others = {line for line in lines if line and not line.startswith("|")}
    kept: list[str] = []
    in_copy = False
    for line in answer.splitlines():
        stripped = line.strip()
        in_copy = stripped in headers or (in_copy and stripped.startswith("|"))
        if not in_copy and stripped not in others:
            kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def answer_with_table(answer: str, messages: list[ModelMessage]) -> str:
    """Put the advisory table below the model's answer, in place of any copy of it
    (`_without_copy`). The prose around the table stays, so a comparison or the
    prose of a single instrument is preserved. A table the model copied is not
    checked against the tool's, so the copy goes and the figures the user sees
    always come from the tool; the prompt still asks for the copy, which is the
    only table the streaming mode shows."""
    table = advisory_table(messages)
    if not table:
        return answer
    prose = _without_copy(answer, table)
    return f"{prose}\n\n{table}" if prose else table


# ── citation ─────────────────────────────────────────────────────────────────

def _dataset_fuentes(table_md: str) -> list[str]:
    """The `fuente` (last) column of a query_dataset markdown table."""
    fuentes: list[str] = []
    for raw in table_md.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        last = cells[-1] if cells else ""
        if not last or last.lower() == "fuente" or set(last) <= {"-"}:
            continue  # skip header row, separator row, empty
        fuentes.append(last)
    return fuentes


def _dedupe(names: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def deliberate_sources(messages: list[ModelMessage]) -> tuple[list[str], list[str]]:
    """`(referencias, fuentes)` the run DELIBERATELY used, de-duplicated:
      - referencias: internal artifacts — the wiki page read (`read_wiki_page`)
        and the dataset FILE a value came from (a dataset tool, `<categoria>.md`);
      - fuentes: external origins of dataset values (the `fuente` column of a
        dataset tool, e.g. ambito.com, bcra.gob.ar).
    A dataset value therefore yields BOTH a fuente (origin) and a referencia
    (file). Search candidates are excluded (buscar ≠ usar)."""
    references: list[str] = []
    fuentes: list[str] = []
    dataset_tools = roles().evidence - {READ_TOOL}
    for part in _tool_returns(messages):
        content = str(part.content)
        if not _is_substantive(content):
            continue
        if part.tool_name == READ_TOOL:
            match = _WIKI_PAGE_RE.search(content)
            if match:
                references.append(match.group(1).strip().rsplit("/", 1)[-1])  # basename
        elif part.tool_name in dataset_tools:
            fuentes.extend(_dataset_fuentes(content))
            cat = _DATASET_CAT_RE.search(content)
            if cat:
                references.append(f"{cat.group(1)}.md")  # the dataset file
    return _dedupe(references), _dedupe(fuentes)


def ensure_citation(
    answer: str,
    messages: list[ModelMessage],
    *,
    extra_references: Iterable[str] = (),
    extra_fuentes: Iterable[str] = (),
) -> str:
    """Append attribution the answer is missing, distinguishing the internal
    artifact from the external origin: `Referencia:` for a wiki page or dataset
    file, `Fuente:` for a dataset's external origin.

    Each label is decided independently, so a dólar answer that already mentions
    its origin inline still gets its dataset `Referencia:` added. No-op when the
    answer already carries that kind of attribution (the word, or a named source
    — including an advisory table's `fuente` column) and on refusals.

    `extra_references` / `extra_fuentes` cite dataset rows the code injected
    into the context itself (pre-retrieval Tier 1, decision 16): those rows
    carry no tool call, so `deliberate_sources` cannot see them."""
    references, fuentes = deliberate_sources(messages)
    references = _dedupe([*references, *extra_references])
    fuentes = _dedupe([*fuentes, *extra_fuentes])
    if not references and not fuentes:
        return answer
    low = answer.lower()
    add_fuentes = [] if ("fuente" in low or any(f in answer for f in fuentes)) else fuentes
    add_refs = [] if ("referencia" in low or any(r in answer for r in references)) else references
    if not add_fuentes and not add_refs:
        return answer
    lines: list[str] = []
    if add_fuentes:
        lines.append(f"Fuente: {', '.join(add_fuentes)}")
    if add_refs:
        lines.append(f"Referencia: {', '.join(add_refs)}")
    return f"{answer.rstrip()}\n\n" + "\n".join(lines)


# The note of an answer that cites nothing (strict mode, node T4). The model may
# answer from a search result (`search_wiki_fts`, `search_source_chunks`), which
# `deliberate_sources` does not cite: a search candidate is not a page used. The
# answer is delivered, marked, rather than refused.
# The note is one line that opens like a citation line, so the sentence verifier
# skips it as a text the code wrote (`domain/verify/checkable.py`, `_CITATION_LINE`).
_UNCITED_NOTE = {
    "es": "Referencia: ninguna (la respuesta no se apoya en ninguna página leída ni en ningún dato consultado)",
    "en": "Reference: none (the answer rests on no page read and no data queried)",
}


def mark_uncited(answer: str, messages: list[ModelMessage], language: str | None) -> str:
    """`answer` with a closing note when it cites nothing: no page was read, no data
    was queried, and the answer names no source itself. Otherwise `answer` as it is.

    Run after `ensure_citation`, on an answer that is not a refusal."""
    references, fuentes = deliberate_sources(messages)
    low = (answer or "").lower()
    if references or fuentes or "referencia" in low or "fuente" in low or looks_cited(answer):
        return answer
    note = _UNCITED_NOTE["es" if (language or "en").lower().startswith("es") else "en"]
    return f"{answer.rstrip()}\n\n{note}"


# The notes of a pre-retrieval answer that names none of the wiki pages the code
# injected into its prompt. The code knows which pages the model received; it does
# not know which ones the answer used. The first note says so. The sentence
# verifier, when it runs, replaces it with the second note, which splits the pages
# by whether they support at least one sentence of the answer. When the model wrote
# its own reference line, the verifier keeps that line, labels it as the model's,
# and adds the second note below it. Both notes open like a citation line, for the
# reason `_UNCITED_NOTE` gives.
_INJECTED_NOTE = {
    "es": ("Referencia: indeterminada. El modelo recibió esta página de la wiki: {names}. "
           "No se puede determinar si la respuesta la usó.",
           "Referencia: indeterminada. El modelo recibió estas páginas de la wiki: {names}. "
           "No se puede determinar cuáles usó la respuesta."),
    "en": ("Reference: undetermined. The model received this wiki page: {names}. "
           "Whether the answer used it cannot be determined.",
           "Reference: undetermined. The model received these wiki pages: {names}. "
           "Which of them the answer used cannot be determined."),
}
_VERIFIED_NOTE = {
    "es": ("Referencia según el verificador de oraciones: aparentemente usadas: {used}. "
           "Aparentemente no usadas: {unused}. Una página usada respalda al menos una "
           "oración de la respuesta.", "ninguna"),
    "en": ("Reference according to the sentence verifier: apparently used: {used}. "
           "Apparently not used: {unused}. A used page supports at least one sentence "
           "of the answer.", "none"),
}
# The reference line the model wrote, and the label that names its author.
_MODEL_REFERENCE = re.compile(
    r"^[\s>*_\-]*(referencias?|references?)(?=\s*[*_]*\s*:)", re.IGNORECASE | re.MULTILINE,
)
_MODEL_LABEL = {"es": " según el modelo", "en": " according to the model"}


def _lang(language: str | None) -> str:
    return "es" if (language or "en").lower().startswith("es") else "en"


def injected_pages(passages: Iterable[ContextPassage]) -> list[str]:
    """The file names of the wiki pages the code injected into the prompt, in order,
    de-duplicated: the passages with no tool whose page is under `/wiki/`."""
    return _dedupe([p.page.rsplit("/", 1)[-1] for p in passages
                    if p.tool is None and p.page.lstrip("/").startswith("wiki/")])


def mark_injected(answer: str, pages: list[str], language: str | None) -> str:
    """`answer` with the undetermined-reference note when the code injected wiki
    `pages` (file names) and the answer names none of them. Otherwise `answer` as
    it is."""
    if not pages or any(page in answer for page in pages):
        return answer
    note = _INJECTED_NOTE[_lang(language)][len(pages) > 1].format(names=", ".join(pages))
    return f"{answer.rstrip()}\n\n{note}"


def resolve_injected(
    answer: str, pages: list[str], used: Collection[str], language: str | None,
) -> tuple[str, list[tuple[int, int]]]:
    """`answer` with the note that splits the injected `pages` into apparently used
    (in `used`) and apparently not used.

    The note replaces the undetermined-reference note of `pages`. With no such note,
    the first reference line the model wrote is labelled as the model's, and the
    note goes below it. With neither, or with no `pages`, `answer` stays as it is.

    Returns the new answer and its edits, as `(offset in answer, change of length)`
    in order, so the offsets of the text after each edit can be shifted."""
    if not pages:
        return answer, []
    lang = _lang(language)
    template, none = _VERIFIED_NOTE[lang]
    note = template.format(
        used=", ".join(p for p in pages if p in used) or none,
        unused=", ".join(p for p in pages if p not in used) or none,
    )
    for variants in _INJECTED_NOTE.values():
        old = variants[len(pages) > 1].format(names=", ".join(pages))
        at = answer.find(old)
        if at >= 0:
            return answer[:at] + note + answer[at + len(old):], [(at, len(note) - len(old))]
    match = _MODEL_REFERENCE.search(answer)
    if not match:
        return answer, []
    label_at = match.end(1)
    line_end = answer.find("\n", label_at)
    line_end = len(answer) if line_end < 0 else line_end
    label, below = _MODEL_LABEL[lang], f"\n\n{note}"
    text = answer[:label_at] + label + answer[label_at:line_end] + below + answer[line_end:]
    return text, [(label_at, len(label)), (line_end, len(below))]


_CITATION_MARKERS = (".md", ".docx", ".doc", ".pdf", ".txt", ".csv")


def looks_cited(text: str) -> bool:
    """Heuristic: a grounded answer names its source.

    A curated wiki page (``*.md``) or the source document it came from
    (``*.docx`` / ``*.pdf`` …) — the pre-retrieval curated path cites the source
    document, so matching only ``.md`` would miss it and read as uncited.
    """
    return any(marker in (text or "") for marker in _CITATION_MARKERS)
