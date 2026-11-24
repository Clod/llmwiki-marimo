"""The pages the question names enter the prompt first, whole (node Q_SEARCH_WIKI). No LLM.

On the frozen finance demo, BM25 leaves out the page a question names: «¿qué es un
FCI?», with the alias `FCI`, got six chunks of pages that mention FCI, and the two
chunks of «FCI Money Market» were 7th and 8th.
"""

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from domain.chat import preretrieval
from domain.chat.config import load_config

DEMO = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "wikis" / "finanzas-argentinas"


@pytest.fixture
def demo(tmp_path):
    workspace = tmp_path / "demo"
    shutil.copytree(DEMO, workspace, ignore=shutil.ignore_patterns("traces", "chat_trace.jsonl"))
    return workspace


def _with_alias(workspace, line):
    config = workspace / "wiki_config.toml"
    config.write_text(config.read_text(encoding="utf-8").replace(
        "[alias_datos]", f"[alias_datos]\n{line}", 1), encoding="utf-8")


def _first_block(workspace, question):
    prompts = []

    async def run_agent(prompt, history):
        prompts.append(prompt)
        return SimpleNamespace(output="Respuesta.", all_messages=lambda: [])

    config = load_config(workspace)
    asyncio.run(preretrieval.pre_retrieval_answer(
        question, config=config, db_path=str(workspace / ".llmwiki" / "index.db"),
        workspace=workspace, history=[], language=config.language, run_agent=run_agent,
    ))
    blocks = [line for line in prompts[0].splitlines() if line.startswith("[/wiki/")]
    return prompts[0], blocks


def test_a_page_named_by_its_alias_comes_first_and_whole(demo):
    _with_alias(demo, '"FCI Money Market" = ["FCI"]')
    prompt, blocks = _first_block(demo, "¿qué es un FCI?")
    assert blocks[0] == "[/wiki/concepts/fci-money-market.md]"
    assert blocks.count("[/wiki/concepts/fci-money-market.md]") == 1
    # The whole page: the topics of the front-matter are dropped, the body is in.
    page = (demo / "wiki" / "concepts" / "fci-money-market.md").read_text(encoding="utf-8")
    assert preretrieval._page_text(page) in prompt


def test_a_page_named_by_a_generated_alias_comes_first(demo):
    _, blocks = _first_block(demo, "¿qué son los bonos DL?")
    assert blocks[0] == "[/wiki/concepts/bonos-dolar-linked.md]"


def test_every_page_the_question_names_comes_before_the_search(demo):
    # «plazo fijo» and «UVA» are titles, «plazo fijo indexado a UVA» an alias of «Plazo fijo UVA».
    _, blocks = _first_block(demo, "¿qué es un plazo fijo indexado a UVA?")
    assert set(blocks[:3]) == {"[/wiki/concepts/uva.md]", "[/wiki/concepts/plazo-fijo-uva.md]",
                               "[/wiki/concepts/plazo-fijo.md]"}


def test_a_topic_gives_every_page_that_declares_it(demo):
    config = load_config(demo)
    named, topical = preretrieval.named_pages(
        demo, str(demo / ".llmwiki" / "index.db"), ["liquidez transitoria"], config.data_aliases)
    assert named == []
    assert set(preretrieval._hit_labels(topical)) == {
        "/wiki/concepts/cauciones-bursatiles.md", "/wiki/concepts/fci-money-market.md",
        "/wiki/summaries/12-cauciones-bursatiles.md"}


def test_a_dataset_key_and_a_category_without_a_page_give_no_page(demo):
    config = load_config(demo)
    named, topical = preretrieval.named_pages(
        demo, str(demo / ".llmwiki" / "index.db"), ["MEP", "dolar"], config.data_aliases, keys=["MEP"])
    assert (named, topical) == ([], [])
