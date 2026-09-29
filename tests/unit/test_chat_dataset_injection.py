"""Tests for decision 16 of design_stemming.md: in pre-retrieval Tier 1, the code
injects the dataset the question names next to the wiki pages (nodes Q7a–Q7d).

`dataset_injection` runs against the real datasets of the finance demo, so the
cases are the measured ones: Banco Galicia (a key, its rows are injected), Banco
Comafi (not a key, the key names are injected), and a concept question.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from domain.chat import preretrieval
from domain.chat.postprocess import ensure_citation
from domain.chat.preretrieval import dataset_injection, pre_retrieval_answer
from domain.datasets.source import LocalMarkdownSource

DATASETS = Path(__file__).resolve().parents[2] / "examples" / "finanzas-argentinas" / "datasets"
SOURCE = LocalMarkdownSource(DATASETS)
ALIASES = {"dolar": ["billete verde", "divisa"]}
BANKS = ["Banco Nación", "Banco Galicia", "Banco Provincia", "Banco Santander",
         "BBVA Argentina", "Banco Macro", "Banco Credicoop", "Brubank"]


def _inject(question):
    return dataset_injection(question, SOURCE, ALIASES, "es")


def test_named_key_injects_that_keys_rows():
    out = _inject("¿Qué tasa de plazo fijo ofrece el Banco Galicia?")
    assert len(out.blocks) == 1
    block = out.blocks[0]
    assert block.startswith("[datasets/plazo_fijo.md]")
    assert "5 dataset row(s)" in block            # 30d, 60d, 90d, 180d, 365d
    assert "Banco Galicia" in block and "Banco Nación" not in block
    assert out.references == ["plazo_fijo.md"]
    assert out.fuentes == ["bcra.gob.ar"]


def test_unknown_key_injects_only_the_key_names():
    out = _inject("¿Qué tasa de plazo fijo ofrece el Banco Comafi?")
    assert len(out.blocks) == 1
    block = out.blocks[0]
    assert "Valores de `entidad` con datos" in block
    assert [line[2:] for line in block.splitlines()[1:]] == BANKS
    assert "|" not in block                        # names only, no values
    assert out.references == [] and out.fuentes == []


def test_concept_question_about_a_category_also_gets_the_key_names():
    # Accepted in decision 16: the code cannot tell an unknown key from no key.
    out = _inject("¿Cómo funciona un plazo fijo UVA?")
    assert len(out.blocks) == 1 and "Banco Nación" in out.blocks[0]
    assert out.references == []


def test_key_alone_names_its_category():
    out = _inject("¿A cuánto está el MEP?")
    assert len(out.blocks) == 1
    assert out.blocks[0].startswith("[datasets/dolar.md]") and "MEP" in out.blocks[0]
    assert out.references == ["dolar.md"]


def test_alias_names_its_category():
    out = _inject("¿A cuánto está el billete verde?")
    assert len(out.blocks) == 1 and "[datasets/dolar.md]" in out.blocks[0]


def test_question_without_dataset_terms_injects_nothing():
    out = _inject("¿Qué es el riesgo de liquidez?")
    assert out.blocks == [] and out.references == [] and out.fuentes == []


def test_english_header():
    out = dataset_injection("plazo fijo rates?", SOURCE, {}, "en")
    assert "Values of `entidad` with data" in out.blocks[0]


def test_ensure_citation_cites_injected_rows():
    answer = ensure_citation(
        "El Banco Galicia paga 32,5% a 30 días.", [],
        extra_references=["plazo_fijo.md"], extra_fuentes=["bcra.gob.ar"],
    )
    assert answer.endswith("Fuente: bcra.gob.ar\nReferencia: plazo_fijo.md")


# ── pre_retrieval_answer: the injection reaches the prompt and the citation ──

class _FakeResult:
    def __init__(self, output):
        self.output = output

    def all_messages(self):
        return []


def _run_turn(question, output, monkeypatch):
    prompts = []

    async def run_agent(prompt, history):
        prompts.append(prompt)
        return _FakeResult(output)

    monkeypatch.setattr(preretrieval, "retrieve_wiki",
                        lambda db, q, **k: ["[/wiki/concepts/plazo-fijo-tradicional.md]\nUn plazo fijo..."])
    monkeypatch.setattr(preretrieval, "concept_page_names", lambda db: ["plazo fijo tradicional"])
    monkeypatch.setattr(preretrieval, "LocalMarkdownSource", lambda p: SOURCE)
    cfg = SimpleNamespace(off_limits=[], data_aliases=ALIASES)
    answer = asyncio.run(pre_retrieval_answer(
        question, config=cfg, db_path="db", workspace=Path("/tmp/wp"),
        history=[], language="es", run_agent=run_agent,
    ))
    return prompts, answer


def test_tier1_prompt_carries_pages_and_rows_and_answer_is_cited(monkeypatch):
    prompts, answer = _run_turn(
        "¿Qué tasa de plazo fijo ofrece el Banco Galicia?",
        "El Banco Galicia paga 32,5% a 30 días.", monkeypatch,
    )
    assert "plazo-fijo-tradicional.md" in prompts[0]     # the page
    assert "5 dataset row(s)" in prompts[0]              # the rows (Q7c)
    assert "Referencia: plazo_fijo.md" in answer
    assert "Fuente: bcra.gob.ar" in answer


def test_tier1_prompt_carries_key_names_for_an_unknown_bank(monkeypatch):
    prompts, answer = _run_turn(
        "¿Qué tasa de plazo fijo ofrece el Banco Comafi?",
        "Banco Comafi no figura en los datos.", monkeypatch,
    )
    assert "Brubank" in prompts[0]                       # the key names (Q7d)
    assert "Referencia" not in answer                    # no rows were injected


# ── offer_available_keys: the answer names the keys that have data ──────────

def _dataset_call(categoria, clave):
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    return ModelResponse(parts=[ToolCallPart(
        tool_name="query_dataset", args={"categoria": categoria, "clave": clave},
    )])


def test_unknown_key_requested_appends_the_keys_with_data():
    from domain.chat.preretrieval import offer_available_keys

    injection = _inject("¿Qué tasa de plazo fijo ofrece el Banco Comafi?")
    out = offer_available_keys(
        "No encontré datos sobre el Banco Comafi.",
        [_dataset_call("plazo_fijo", "Banco Comafi")], injection, "es",
    )
    assert out.endswith(
        "Datos disponibles en `plazo_fijo` (entidad): " + ", ".join(BANKS) + "."
    )


def test_no_key_requested_leaves_a_concept_answer_alone():
    from domain.chat.preretrieval import offer_available_keys

    injection = _inject("¿Cómo funciona un plazo fijo UVA?")
    answer = "Un plazo fijo UVA ajusta el capital por inflación."
    assert offer_available_keys(answer, [], injection, "es") == answer


def test_answer_that_already_names_a_key_is_left_alone():
    from domain.chat.preretrieval import offer_available_keys

    injection = _inject("¿Qué tasa de plazo fijo ofrece el Banco Comafi?")
    answer = "Comafi no figura; sí tengo datos del Banco Nación y de Brubank."
    calls = [_dataset_call("plazo_fijo", "Banco Comafi")]
    assert offer_available_keys(answer, calls, injection, "es") == answer
