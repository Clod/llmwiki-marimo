"""Tests for domain/chat/postprocess.py — deterministic answer post-processing.

Runs AFTER the guardrail in respond(). Two guarantees the prompt alone could not
hold under multi-turn priming:
  - answer_with_table: append the advisory tool's verbatim markdown table.
  - ensure_citation: append an honest `Fuente:` line from DELIBERATE source uses
    (wiki pages read, datasets queried) — never from mere search candidates.

Uses real pydantic_ai message/part classes, like test_chat_guardrail.py.
"""

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)

from domain.chat.postprocess import (
    advisory_table,
    answer_with_table,
    ensure_citation,
)


# ── message fabricators (no LLM) ─────────────────────────────────────────────

def _tool_return(tool_name: str, content: str) -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart(tool_name=tool_name, content=content, tool_call_id="c1")])


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


# The exact markdown estimar_alternativas returns (trimmed for the test).
TABLE = (
    "**Alternativas con ganancia estimada** (ordenadas por ganancia)\n"
    "| opción | clave | plazo | TEA | ganancia est. | al fecha | fuente |\n"
    "|---|---|---|---|---|---|---|\n"
    "| plazo_fijo | Banco Credicoop | 90d | 41.84% | $90,000 | 2026-06-25 | bcra.gob.ar |"
)

WIKI_READ = "[wiki page: wiki/concepts/cauciones-bursatiles.md]\n\n# Cauciones\nUna caución es..."

DATASET_TABLE = (
    "**1 dataset row(s)** for categoria='dolar':\n"
    "| clave | metrica | valor | unidad | dims | as_of | fuente |\n"
    "|---|---|---|---|---|---|---|\n"
    "| MEP | compra | 1180 | ARS | - | 2026-06-25 | ambito.com |"
)


# ── advisory_table ───────────────────────────────────────────────────────────

def test_advisory_table_returns_verbatim_when_tool_ran():
    messages = [
        _user("Tengo $1.000.000 por 3 meses, ¿qué me conviene?"),
        _tool_return("estimar_alternativas", TABLE),
        _assistant("Te conviene el plazo fijo..."),
    ]
    assert advisory_table(messages) == TABLE


def test_advisory_table_none_when_tool_not_called():
    messages = [_user("¿Qué es una caución?"), _tool_return("read_wiki_page", WIKI_READ)]
    assert advisory_table(messages) is None


def test_advisory_table_none_on_empty_or_no_options():
    messages = [_user("..."), _tool_return("estimar_alternativas", "No hay opciones elegibles.")]
    assert advisory_table(messages) is None


def test_advisory_table_takes_last_call():
    messages = [
        _tool_return("estimar_alternativas", "vieja"),
        _tool_return("estimar_alternativas", TABLE),
    ]
    assert advisory_table(messages) == TABLE


# ── answer_with_table ────────────────────────────────────────────────────────

def test_answer_with_table_appends_below_prose():
    messages = [_user("..."), _tool_return("estimar_alternativas", TABLE)]
    prose = "Para invertir $1.000.000, aquí van algunas alternativas: 1. Plazo fijo..."
    out = answer_with_table(prose, messages)
    assert out.startswith(prose)
    assert TABLE in out
    assert out == f"{prose}\n\n{TABLE}"


def test_answer_with_table_noop_without_advisory():
    messages = [_user("¿Qué es una caución?"), _tool_return("read_wiki_page", WIKI_READ)]
    prose = "Una caución es una operación de corto plazo..."
    assert answer_with_table(prose, messages) == prose


def test_answer_with_table_idempotent_when_model_already_pasted():
    messages = [_user("..."), _tool_return("estimar_alternativas", TABLE)]
    # The model already included the table (fresh-session happy path).
    answer = f"Aquí van:\n\n{TABLE}"
    assert answer_with_table(answer, messages) == answer  # no duplicate


# ── ensure_citation ──────────────────────────────────────────────────────────

def test_wiki_page_is_a_referencia_not_a_fuente():
    # A wiki page / document is the internal artifact -> "Referencia".
    messages = [_user("¿qué es una caución?"), _tool_return("read_wiki_page", WIKI_READ)]
    answer = "Una caución es una operación de corto plazo..."
    out = ensure_citation(answer, messages)
    assert out.endswith("Referencia: cauciones-bursatiles.md")
    assert "Fuente:" not in out


def test_dataset_yields_both_fuente_origin_and_referencia_file():
    # A dataset value has BOTH: an external origin (ambito.com -> Fuente) and the
    # dataset file it came from (dolar.md -> Referencia).
    messages = [_user("¿dólar MEP?"), _tool_return("query_dataset", DATASET_TABLE)]
    answer = "El dólar MEP está a 1180 (compra) y 1185 (venta)."
    out = ensure_citation(answer, messages)
    assert "Fuente: ambito.com" in out
    assert "Referencia: dolar.md" in out


def test_dataset_adds_referencia_when_origin_already_inline():
    # The real dólar bug: the model mentions the origin (ambito.com) inline but
    # gives no referencia. We add the dataset file as Referencia and do NOT
    # duplicate the origin.
    messages = [_user("¿dólar MEP?"), _tool_return("query_dataset", DATASET_TABLE)]
    answer = "El dólar MEP está a 1180/1185, proporcionados por ambito.com."
    out = ensure_citation(answer, messages)
    assert "Referencia: dolar.md" in out
    assert out.count("ambito.com") == 1  # no duplicate Fuente


def test_page_and_dataset_emit_both_labels():
    messages = [
        _user("..."),
        _tool_return("read_wiki_page", WIKI_READ),
        _tool_return("query_dataset", DATASET_TABLE),
    ]
    out = ensure_citation("Respuesta sin cita.", messages)
    assert "Referencia: cauciones-bursatiles.md" in out
    assert "Fuente: ambito.com" in out


def test_ensure_citation_no_duplicate_when_already_referenced():
    messages = [_user("..."), _tool_return("read_wiki_page", WIKI_READ)]
    answer = "Una caución es... Referencia: cauciones-bursatiles.md"
    assert ensure_citation(answer, messages) == answer


def test_ensure_citation_noop_on_refusal_no_tools():
    messages = [_user("¿capital de Francia?")]
    answer = "Eso no está en mi base de conocimiento."
    assert ensure_citation(answer, messages) == answer


def test_ensure_citation_ignores_search_only_buscar_no_es_usar():
    # Model searched but never read/queried -> nothing deliberate to cite.
    search_hit = "**5 wiki result(s)** for 'x':\n\n**/wiki/concepts/cauciones-bursatiles.md** p.1 › ..."
    messages = [_user("..."), _tool_return("search_wiki_fts", search_hit)]
    answer = "Una respuesta cualquiera sin cita."
    assert ensure_citation(answer, messages) == answer  # search candidates are NOT cited


def test_ensure_citation_skips_when_advisory_table_present():
    # Composition: after answer_with_table appends the table (which carries a
    # `fuente` column), ensure_citation must not add a redundant line.
    messages = [_user("..."), _tool_return("estimar_alternativas", TABLE)]
    answer = f"Alternativas:\n\n{TABLE}"  # already has the fuente column
    assert ensure_citation(answer, messages) == answer
