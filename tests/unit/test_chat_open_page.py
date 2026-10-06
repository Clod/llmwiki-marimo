"""The page the user is reading reaches the chat turn (node Q5a). No LLM."""

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from domain.chat.preretrieval import pre_retrieval_answer
from services.chat import _with_open_page

DEMO = Path(__file__).resolve().parents[2] / "examples" / "finanzas-argentinas"


@pytest.fixture
def demo(tmp_path):
    workspace = tmp_path / "demo"
    shutil.copytree(DEMO, workspace, ignore=shutil.ignore_patterns("traces", "chat_trace.jsonl"))
    return workspace


def _turn(workspace, question, open_page):
    prompts = []

    async def run_agent(prompt, history):
        prompts.append(prompt)
        return SimpleNamespace(output="Un plazo fijo UVA rinde inflación más una tasa.",
                               all_messages=lambda: [])

    cfg = SimpleNamespace(off_limits=["cedear"], data_aliases={})
    answer = asyncio.run(pre_retrieval_answer(
        question, config=cfg, db_path=str(workspace / ".llmwiki" / "index.db"),
        workspace=workspace, history=[], language="es", run_agent=run_agent,
        open_page=open_page,
    ))
    return answer, prompts


def test_question_without_topic_is_refused_without_an_open_page(demo):
    answer, prompts = _turn(demo, "¿Cuánto rinde?", None)
    assert answer.startswith("No puedo responder esta pregunta")
    assert prompts == []


def test_question_without_topic_is_answered_from_the_open_page(demo):
    answer, prompts = _turn(demo, "¿Cuánto rinde?", "concepts/plazo-fijo-uva")
    assert answer.startswith("Un plazo fijo UVA rinde")
    assert prompts[0].index("[/wiki/concepts/plazo-fijo-uva.md]") < 200   # injected first


def test_a_missing_open_page_changes_nothing(demo):
    answer, _ = _turn(demo, "¿Cuánto rinde?", "concepts/no-existe")
    assert answer.startswith("No puedo responder esta pregunta")


def test_tool_modes_get_the_open_page_as_a_note():
    assert _with_open_page("¿Cuánto rinde?", "concepts/plazo-fijo-uva", "es").startswith(
        "(El usuario está leyendo la página /wiki/concepts/plazo-fijo-uva.md.")
    assert _with_open_page("q", None, "es") == "q"
