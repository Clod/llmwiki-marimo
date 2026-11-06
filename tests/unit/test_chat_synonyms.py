"""The prompt names the aliases the search replaced (`synonyms_line`). No LLM."""

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from domain.chat.preretrieval import pre_retrieval_answer, synonyms_line

DEMO = Path(__file__).resolve().parents[2] / "examples" / "finanzas-argentinas"


@pytest.fixture
def demo(tmp_path):
    workspace = tmp_path / "demo"
    shutil.copytree(DEMO, workspace, ignore=shutil.ignore_patterns("traces", "chat_trace.jsonl"))
    return workspace


def _prompt(workspace, question, data_aliases):
    prompts = []

    async def run_agent(prompt, history):
        prompts.append(prompt)
        return SimpleNamespace(output="El billete verde es el dólar.", all_messages=lambda: [])

    cfg = SimpleNamespace(off_limits=[], data_aliases=data_aliases)
    asyncio.run(pre_retrieval_answer(
        question, config=cfg, db_path=str(workspace / ".llmwiki" / "index.db"),
        workspace=workspace, history=[], language="es", run_agent=run_agent,
    ))
    return prompts[0]


def test_an_alias_of_the_question_is_named_before_the_pages(demo):
    prompt = _prompt(demo, "¿qué es el billete verde?", {"dolar": ["billete verde", "divisa"]})
    line = "Sinónimos de esta wiki que usa la pregunta: «billete verde» es otro nombre de «dolar»."
    assert line in prompt
    assert prompt.index(line) < prompt.index("\n[")          # before the first page


def test_a_question_without_an_alias_gets_no_line(demo):
    prompt = _prompt(demo, "¿qué es el plazo fijo?", {"dolar": ["billete verde", "divisa"]})
    assert "Sinónimos" not in prompt


def test_the_line_keeps_the_spelling_of_the_config():
    assert synonyms_line(["fondo comun de inversion"], {"FCI Money Market": ["fondo común de inversión"]},
                         "es") == ("Sinónimos de esta wiki que usa la pregunta: "
                                   "«fondo común de inversión» es otro nombre de «FCI Money Market».")
    assert synonyms_line(["FCI"], {"FCI Money Market": ["FCI"]}, "en") == (
        'Synonyms of this wiki that the question uses: "FCI" is another name for "FCI Money Market".')
    assert synonyms_line(["plazo fijo"], {"FCI Money Market": ["FCI"]}, "es") == ""
