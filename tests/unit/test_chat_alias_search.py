"""Tests for the search of a page alias by the name it stands for
(`preretrieval.alias_titles`, node Q6). The end-to-end test runs on a copy of the
fairy-tale demo, with a stand-in for the model. No LLM.
"""

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

from domain.chat import preretrieval
from domain.chat.config import load_config

DEMO = Path(__file__).resolve().parents[2] / "examples" / "cuentos-de-hadas"


def test_an_alias_is_replaced_by_the_name_it_stands_for():
    aliases = {"Cenicienta": ["Culocenizón"], "Hermanos Grimm": ["Jacob Grimm", "Wilhelm Grimm"]}
    assert preretrieval.alias_titles(["culocenizon"], aliases) == ["Cenicienta"]
    assert preretrieval.alias_titles(["Cenicienta", "Culocenizón"], aliases) == ["Cenicienta"]
    assert preretrieval.alias_titles(["plazo_fijo"], aliases) == ["plazo_fijo"]


def test_a_question_that_names_an_alias_receives_the_page_of_the_name(tmp_path):
    # Battery `pil-06`: no page holds the word "Culocenizón", and the question ended in R4.
    workspace = tmp_path / "cuentos"
    shutil.copytree(DEMO, workspace, ignore=shutil.ignore_patterns(".git", "traces", "chat_trace.jsonl"))
    config = load_config(workspace)
    passages = []

    async def run_agent(prompt, history):
        return SimpleNamespace(output="Es Cenicienta.", all_messages=lambda: [])

    asyncio.run(preretrieval.pre_retrieval_answer(
        "¿Quién es Culocenizón?", config=config, db_path=str(workspace / ".llmwiki" / "index.db"),
        workspace=workspace, history=[], language=config.language, run_agent=run_agent,
        on_context=passages.extend))
    assert "/wiki/concepts/cenicienta.md" in [p.page for p in passages]
