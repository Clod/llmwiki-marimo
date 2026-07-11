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

from pydantic_ai.messages import ModelMessage, ToolReturnPart

# The advisory tool (domain.finance_argentina.agent_tool). Its return is a
# complete markdown block we surface verbatim.
ADVISORY_TOOL = "estimar_alternativas"

# Tools whose returns represent a DELIBERATE use of a source (not a search
# candidate): the page the model opened, and the dataset value it looked up.
# search_wiki_fts / search_source_chunks are intentionally excluded.
READ_TOOL = "read_wiki_page"
DATASET_TOOL = "query_dataset"

# Header cell of the advisory ranked table — its presence means a table is
# already in the text (model complied), so we don't append a duplicate.
_ADVISORY_TABLE_MARKER = "| opción"

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
            if isinstance(part, ToolReturnPart):
                yield part


def _is_substantive(content: str) -> bool:
    low = content.strip().lower()
    return bool(low) and not any(marker in low for marker in _NEGATIVE_MARKERS)


# ── advisory table ───────────────────────────────────────────────────────────

def advisory_table(messages: list[ModelMessage]) -> str | None:
    """Verbatim markdown of the LAST substantive `estimar_alternativas` return,
    or None if the advisory tool did not run / returned nothing usable."""
    table: str | None = None
    for part in _tool_returns(messages):
        if part.tool_name == ADVISORY_TOOL:
            content = str(part.content)
            if _is_substantive(content):
                table = content.strip()
    return table


def answer_with_table(answer: str, messages: list[ModelMessage]) -> str:
    """Append the advisory table below the model's answer (append, not replace,
    so comparison / single-instrument prose is preserved). Idempotent: if the
    answer already contains the table, leave it untouched."""
    table = advisory_table(messages)
    if not table:
        return answer
    if _ADVISORY_TABLE_MARKER in answer:
        return answer  # model already pasted a table — no duplicate
    return f"{answer.rstrip()}\n\n{table}"


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
        and the dataset FILE a value came from (`query_dataset`, `<categoria>.md`);
      - fuentes: external origins of dataset values (`query_dataset` `fuente`
        column, e.g. ambito.com, bcra.gob.ar).
    A dataset value therefore yields BOTH a fuente (origin) and a referencia
    (file). Search candidates are excluded (buscar ≠ usar)."""
    references: list[str] = []
    fuentes: list[str] = []
    for part in _tool_returns(messages):
        content = str(part.content)
        if not _is_substantive(content):
            continue
        if part.tool_name == READ_TOOL:
            match = _WIKI_PAGE_RE.search(content)
            if match:
                references.append(match.group(1).strip().rsplit("/", 1)[-1])  # basename
        elif part.tool_name == DATASET_TOOL:
            fuentes.extend(_dataset_fuentes(content))
            cat = _DATASET_CAT_RE.search(content)
            if cat:
                references.append(f"{cat.group(1)}.md")  # the dataset file
    return _dedupe(references), _dedupe(fuentes)


def ensure_citation(answer: str, messages: list[ModelMessage]) -> str:
    """Append attribution the answer is missing, distinguishing the internal
    artifact from the external origin: `Referencia:` for a wiki page or dataset
    file, `Fuente:` for a dataset's external origin.

    Each label is decided independently, so a dólar answer that already mentions
    its origin inline still gets its dataset `Referencia:` added. No-op when the
    answer already carries that kind of attribution (the word, or a named source
    — including an advisory table's `fuente` column) and on refusals."""
    references, fuentes = deliberate_sources(messages)
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
