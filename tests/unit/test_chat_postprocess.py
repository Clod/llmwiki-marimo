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
    "| plazo_fijo | Banco Credicoop | 90d | 41,84% | $90.000 | 2026-06-25 | bcra.gob.ar |"
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

UNCHECKED = "Las cifras del texto anterior las escribió el modelo, y el código no las verificó."
COMPUTED = "Las cifras de la tabla siguiente las calculó el código con los datos de su columna «fuente»."


def test_answer_with_table_appends_below_prose_with_the_note():
    messages = [_user("..."), _tool_return("estimar_alternativas", TABLE)]
    prose = "Para invertir $1.000.000, aquí van algunas alternativas: 1. Plazo fijo..."
    out = answer_with_table(prose, messages, "es")
    assert out == f"{prose}\n\n{UNCHECKED} {COMPUTED}\n\n{TABLE}"


def test_answer_with_table_note_of_a_prose_without_figures_names_only_the_table():
    messages = [_user("..."), _tool_return("estimar_alternativas", TABLE)]
    prose = "El plazo fijo rinde más que el fondo money market."
    assert answer_with_table(prose, messages, "es") == f"{prose}\n\n{COMPUTED}\n\n{TABLE}"


def test_answer_with_table_note_in_english():
    messages = [_user("..."), _tool_return("estimar_alternativas", TABLE)]
    out = answer_with_table("It yields 40%.", messages, "en")
    assert "The figures in the text above were written by the model" in out
    assert "The figures in the table below were computed by the code" in out


def test_answer_with_table_noop_without_advisory():
    messages = [_user("¿Qué es una caución?"), _tool_return("read_wiki_page", WIKI_READ)]
    prose = "Una caución es una operación de corto plazo..."
    assert answer_with_table(prose, messages, "es") == prose


def test_answer_with_table_replaces_the_copy_the_model_pasted():
    messages = [_user("..."), _tool_return("estimar_alternativas", TABLE)]
    # The model copied the table, its title and a changed figure: the copy goes,
    # the prose stays, and the user sees the tool's table once, with its figures.
    pasted = TABLE.replace("$90.000", "$95.000")
    answer = f"Aquí van:\n\n{pasted}\n\nEl plazo fijo rinde más."
    assert answer_with_table(answer, messages, "es") == (
        f"Aquí van:\n\nEl plazo fijo rinde más.\n\n{COMPUTED}\n\n{TABLE}"
    )


def test_answer_with_table_keeps_a_table_whose_header_the_model_changed():
    messages = [_user("..."), _tool_return("estimar_alternativas", TABLE)]
    changed = "| instrumento | ganancia |\n|---|---|\n| plazo fijo | $90.000 |"
    assert answer_with_table(changed, messages, "es") == (
        f"{changed}\n\n{UNCHECKED} {COMPUTED}\n\n{TABLE}"
    )


def test_answer_with_table_of_an_answer_that_is_only_the_copy():
    messages = [_user("..."), _tool_return("estimar_alternativas", TABLE)]
    assert answer_with_table(TABLE, messages, "es") == f"{COMPUTED}\n\n{TABLE}"


# ── ensure_citation ──────────────────────────────────────────────────────────

def test_wiki_page_is_a_referencia_not_a_fuente():
    # A wiki page / document is the internal artifact -> "Referencia".
    messages = [_user("¿qué es una caución?"), _tool_return("read_wiki_page", WIKI_READ)]
    answer = "Una caución es una operación de corto plazo..."
    out = ensure_citation(answer, messages)
    assert out.endswith("Referencia: cauciones-bursatiles.md")
    assert "Fuente:" not in out


def test_the_index_and_the_overview_are_read_but_not_cited():
    # The strict prompt asks the model to read the index first; the index found the page,
    # and the page is the source of the answer.
    messages = [_user("¿qué es una caución?"),
                _tool_return("read_wiki_page", "[wiki page: wiki/index.md]\n\n# Índice\n- Cauciones"),
                _tool_return("read_wiki_page", "[wiki page: wiki/overview.md]\n\n# Resumen\nLa wiki trata..."),
                _tool_return("read_wiki_page", WIKI_READ)]
    out = ensure_citation("Una caución es una operación de corto plazo...", messages)
    assert out.endswith("Referencia: cauciones-bursatiles.md")


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


# ── looks_cited ─────────────────────────────────────────────────────────────

def test_looks_cited_recognizes_wiki_page_and_source_document():
    from domain.chat.postprocess import looks_cited

    assert looks_cited("Ver [Caución](/wiki/concepts/caucion.md).")
    # A curated pre-retrieval answer cites the SOURCE document, not a .md.
    assert looks_cited("El plazo fijo tradicional… Referencia: 10 Plazos Fijos.docx")
    assert not looks_cited("Eso no está en mi base de conocimiento.")
    assert not looks_cited("")


def test_mark_uncited_leaves_an_answer_that_names_its_source():
    from domain.chat.postprocess import mark_uncited
    assert mark_uncited("Según cauciones.md, es un préstamo.", [], "es") == "Según cauciones.md, es un préstamo."


def test_mark_uncited_uses_the_wiki_language():
    from domain.chat.postprocess import mark_uncited
    assert mark_uncited("It is a loan.", [], "en").endswith(
        "Reference: none (the answer rests on no page read and no data queried)")


def test_the_uncited_note_is_not_verified_as_a_sentence():
    from domain.chat.postprocess import mark_uncited
    from domain.verify.checkable import is_checkable
    from domain.verify.models import Sentence
    answer = mark_uncited("Una caución es un préstamo de corto plazo.", [], "es")
    note = answer.split("\n\n")[-1]
    start = answer.index(note)
    assert not is_checkable(Sentence(start, start + len(note), note), answer, [])


# ── The note of the pages injected into the prompt (pre-retrieval) ───────────

PAGES = ["caucion-bursatil.md", "byma.md"]


def test_injected_pages_are_the_wiki_pages_with_no_tool():
    from domain.chat.postprocess import injected_pages
    from domain.verify.models import ContextPassage
    passages = [
        ContextPassage("/wiki/concepts/caucion-bursatil.md", "a"),
        ContextPassage("/wiki/concepts/caucion-bursatil.md", "b"),
        ContextPassage("datasets/caucion.md", "c"),
        ContextPassage("/wiki/concepts/byma.md", "d"),
        ContextPassage("/wiki/concepts/read.md", "e", tool="read_wiki_page"),
    ]
    assert injected_pages(passages) == PAGES


def test_an_answer_that_names_no_injected_page_says_it_cannot_tell_which_one_it_used():
    from domain.chat.postprocess import mark_injected
    answer = mark_injected("Una caución es un préstamo.", PAGES, "es")
    assert answer == (
        "Una caución es un préstamo.\n\nReferencia: el modelo recibió estas páginas de la "
        "wiki, y no se puede determinar cuáles usó la respuesta: caucion-bursatil.md, "
        "byma.md."
    )


def test_one_injected_page_is_named_in_the_singular():
    from domain.chat.postprocess import mark_injected
    assert mark_injected("It is a loan.", ["byma.md"], "en").endswith(
        "Reference: the model received this wiki page, and whether the answer used it "
        "cannot be determined: byma.md.")


def test_an_answer_that_names_an_injected_page_or_has_none_gets_no_note():
    from domain.chat.postprocess import mark_injected
    assert mark_injected("Según byma.md, opera BYMA.", PAGES, "es") == "Según byma.md, opera BYMA."
    assert mark_injected("Una caución es un préstamo.", [], "es") == "Una caución es un préstamo."


def test_the_verification_splits_the_injected_pages_and_reports_the_shift():
    from domain.chat.postprocess import mark_injected, resolve_injected
    before = mark_injected("Una caución es un préstamo.", PAGES, "es")
    after, edits = resolve_injected(before, PAGES, {"byma.md"}, "es")
    assert after == (
        "Una caución es un préstamo.\n\nReferencia según el verificador de oraciones: "
        "aparentemente usadas: byma.md. Aparentemente no usadas: caucion-bursatil.md. "
        "Una página usada respalda al menos una oración de la respuesta."
    )
    assert edits == [(before.index("Referencia:"), len(after) - len(before))]


def test_no_used_page_is_written_as_none():
    from domain.chat.postprocess import mark_injected, resolve_injected
    after, _ = resolve_injected(mark_injected("It is a loan.", PAGES, "en"), PAGES, set(), "en")
    assert "apparently used: none. Apparently not used: caucion-bursatil.md, byma.md." in after


def test_the_reference_line_of_the_model_is_labelled_and_the_split_goes_below_it():
    from domain.chat.postprocess import resolve_injected
    answer = "Es un préstamo.\n\n**Referencia:** byma.md, caucion-bursatil.md\nOtra línea."
    after, edits = resolve_injected(answer, PAGES, {"caucion-bursatil.md"}, "es")
    assert after == (
        "Es un préstamo.\n\n**Referencia según el modelo:** byma.md, caucion-bursatil.md"
        "\n\nReferencia según el verificador de oraciones: aparentemente usadas: "
        "caucion-bursatil.md. Aparentemente no usadas: byma.md. Una página usada respalda "
        "al menos una oración de la respuesta.\nOtra línea."
    )
    label_at = answer.index(":**")
    line_end = answer.index("\nOtra")
    assert edits == [(label_at, len(" según el modelo")), (line_end, len(after) - len(answer) - len(" según el modelo"))]
    # The text after the two edits keeps its place once shifted.
    shift = sum(change for _, change in edits)
    assert after[answer.index("Otra") + shift:] == "Otra línea."


def test_an_answer_with_no_note_and_no_reference_line_is_not_changed():
    from domain.chat.postprocess import resolve_injected
    assert resolve_injected("Una caución es un préstamo.", PAGES, set(), "es") == (
        "Una caución es un préstamo.", [])


def test_an_answer_with_a_reference_line_gets_the_note_as_an_addition():
    from domain.chat.postprocess import mark_injected, resolve_injected
    answer = mark_injected("El MEP está a $1.185.\n\nReferencia: dolar.md", PAGES, "es")
    assert answer == (
        "El MEP está a $1.185.\n\nReferencia: dolar.md\n\nAdemás, el modelo recibió estas "
        "páginas de la wiki: caucion-bursatil.md, byma.md. No se puede determinar si la "
        "respuesta las usó."
    )
    assert "Referencia: el modelo recibió" not in answer
    assert mark_injected("It is $1,185.\n\nReference: dolar.md", ["byma.md"], "en").endswith(
        "In addition, the model received this wiki page: byma.md. "
        "Whether the answer used it cannot be determined.")
    after, _ = resolve_injected(answer, PAGES, {"byma.md"}, "es")
    assert "Además" not in after and "Referencia según el verificador de oraciones" in after


def test_the_word_referencia_in_the_prose_is_not_a_citation():
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
    from domain.chat.postprocess import ensure_citation
    messages = [
        ModelResponse(parts=[ToolCallPart(tool_name="query_dataset", args={"categoria": "dolar"},
                                          tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="query_dataset", tool_call_id="c1",
                                           content="**1 dataset row(s)** for categoria='dolar': | MEP | 1185 |")]),
    ]
    answer = "El MEP está a $1.185. Cotización de referencia: los valores pueden cambiar."
    assert ensure_citation(answer, messages, extra_references=["dolar.md"]).endswith("Referencia: dolar.md")
    cited = "El MEP está a $1.185 (Referencia: dolar.md)."
    assert ensure_citation(cited, messages, extra_references=["dolar.md"]) == cited


def test_a_citation_in_the_middle_of_a_line_gets_the_note_as_an_addition():
    from domain.chat.postprocess import mark_injected
    for answer in ("Fuente: ambito.com (datos en Referencia: datasets/dolar.md). Es un dato del día.",
                   "Fuente: ambito.com (datasets/dolar.md, clave MEP)"):
        assert mark_injected(answer, PAGES, "es").endswith(
            "Además, el modelo recibió estas páginas de la wiki: caucion-bursatil.md, byma.md. "
            "No se puede determinar si la respuesta las usó.")
    assert "Referencia: el modelo recibió" in mark_injected("El MEP está a $1.185.", PAGES, "es")


def test_no_sentence_of_the_addition_is_verified():
    from domain.chat.postprocess import mark_injected
    from domain.verify.checkable import is_checkable
    from domain.verify.sentences import split_sentences
    answer = mark_injected("El MEP está a $1.185.\n\nReferencia: dolar.md", PAGES, "es")
    checked = [s.text for s in split_sentences(answer) if is_checkable(s, answer, [])]
    assert checked == ["El MEP está a $1.185."]


def test_no_sentence_of_either_note_is_verified():
    from domain.chat.postprocess import mark_injected, resolve_injected
    from domain.verify.checkable import is_checkable
    from domain.verify.sentences import split_sentences
    first = mark_injected("Una caución es un préstamo de corto plazo.", PAGES, "es")
    second, _ = resolve_injected(first, PAGES, {"byma.md"}, "es")
    third, _ = resolve_injected(
        "Una caución es un préstamo de corto plazo.\n\nReferencia: byma.md. Es un resumen.",
        PAGES, {"byma.md"}, "es")
    for answer in (first, second, third):
        checked = [s.text for s in split_sentences(answer) if is_checkable(s, answer, [])]
        assert checked == ["Una caución es un préstamo de corto plazo."]


def test_a_reference_line_of_the_model_with_injected_pages_is_labelled_and_completed():
    # The model cited one of the two pages in its own line: the line is labelled as the
    # model's, and the note lists both pages the model received.
    from domain.chat.postprocess import mark_injected
    answer = mark_injected(
        "Una caución es un préstamo.\n\nReferencia: wiki/concepts/caucion-bursatil.md", PAGES, "es")
    assert answer == (
        "Una caución es un préstamo.\n\nReferencia según el modelo: "
        "wiki/concepts/caucion-bursatil.md\n\nAdemás, el modelo recibió estas páginas de la "
        "wiki: caucion-bursatil.md, byma.md. No se puede determinar si la respuesta las usó."
    )


def test_the_verification_replaces_the_note_below_a_labelled_reference_of_the_model():
    from domain.chat.postprocess import mark_injected, resolve_injected
    before = mark_injected("Es un préstamo.\n\nReferencia: caucion-bursatil.md", PAGES, "es")
    after, _ = resolve_injected(before, PAGES, {"caucion-bursatil.md"}, "es")
    assert after.count("según el modelo") == 1
    assert "Además" not in after
    assert after.endswith("Aparentemente no usadas: byma.md. Una página usada respalda al menos "
                          "una oración de la respuesta.")
