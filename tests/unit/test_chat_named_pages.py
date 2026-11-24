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


def _named(workspace, terms, keys=()):
    config = load_config(workspace)
    return preretrieval.named_pages(
        workspace, str(workspace / ".llmwiki" / "index.db"), terms, config.data_aliases, keys=keys)


def test_a_page_named_by_its_alias_comes_first_and_whole(demo):
    _with_alias(demo, '"FCI Money Market" = ["FCI"]')
    prompt, blocks = _first_block(demo, "¿qué es un FCI?")
    # The summary of the topic first, then its concept page.
    assert blocks[:2] == ["[/wiki/summaries/07-fci-money-market.md]",
                          "[/wiki/concepts/fci-money-market.md]"]
    assert blocks.count("[/wiki/concepts/fci-money-market.md]") == 1
    # The whole page: the topics of the front-matter are dropped, the body is in.
    page = (demo / "wiki" / "concepts" / "fci-money-market.md").read_text(encoding="utf-8")
    assert preretrieval._page_text(page) in prompt


def test_a_page_named_by_a_generated_alias_comes_first(demo):
    # «Bonos Dólar Linked», the name the alias stands for, is in the title of the summary.
    _, blocks = _first_block(demo, "¿qué son los bonos DL?")
    assert blocks[:2] == ["[/wiki/summaries/05-bonos-dolar-linked-y-duales.md]",
                          "[/wiki/concepts/bonos-dolar-linked.md]"]


def test_every_page_the_question_names_comes_before_the_search(demo):
    # «plazo fijo» and «UVA» are titles, «plazo fijo indexado a UVA» an alias of «Plazo fijo UVA».
    # «plazo fijo» is in the title of the summary «10 Plazos Fijos», «UVA» in «04 Bonos Cer Y Uva».
    _, blocks = _first_block(demo, "¿qué es un plazo fijo indexado a UVA?")
    assert set(blocks[:5]) == {"[/wiki/summaries/10-plazos-fijos.md]",
                               "[/wiki/summaries/04-bonos-cer-y-uva.md]",
                               "[/wiki/concepts/uva.md]", "[/wiki/concepts/plazo-fijo-uva.md]",
                               "[/wiki/concepts/plazo-fijo.md]"}


def test_a_topic_gives_every_page_that_declares_it(demo):
    summaries, named, topical = _named(demo, ["liquidez transitoria"])
    assert named == []
    assert preretrieval._hit_labels(summaries) == ["/wiki/summaries/12-cauciones-bursatiles.md"]
    assert set(preretrieval._hit_labels(topical)) == {
        "/wiki/concepts/cauciones-bursatiles.md", "/wiki/concepts/fci-money-market.md"}


def test_a_dataset_key_and_a_category_without_a_page_give_no_page(demo):
    assert _named(demo, ["MEP", "cedears"], keys=["MEP"]) == ([], [], [])


def test_a_category_contained_in_a_summary_title_gives_that_summary(demo):
    # No concept page is titled «dolar»; the summary «05 Bonos Dólar Linked Y Duales» contains it.
    summaries, named, topical = _named(demo, ["dolar"])
    assert preretrieval._hit_labels(summaries) == ["/wiki/summaries/05-bonos-dolar-linked-y-duales.md"]
    assert (named, topical) == ([], [])


def test_a_summary_enters_by_its_title_without_declaring_the_topic(demo):
    summaries, named, _ = _named(demo, ["FCI Money Market"])
    assert preretrieval._hit_labels(summaries) == ["/wiki/summaries/07-fci-money-market.md"]
    assert preretrieval._hit_labels(named) == ["/wiki/concepts/fci-money-market.md"]


def test_a_summary_enters_by_its_topics_without_naming_it_in_the_title(demo):
    summaries, _, topical = _named(demo, ["fondos comunes de inversión"])
    assert "/wiki/summaries/07-fci-money-market.md" in preretrieval._hit_labels(summaries)
    assert "/wiki/summaries/07-fci-money-market.md" not in preretrieval._hit_labels(topical)


def test_a_short_term_is_matched_as_a_whole_word_of_the_title(demo):
    summaries, _, _ = _named(demo, ["UVA"])
    assert preretrieval._hit_labels(summaries) == ["/wiki/summaries/04-bonos-cer-y-uva.md"]


def test_a_dataset_key_gives_no_summary(demo):
    summaries, _, _ = _named(demo, ["UVA"], keys=["UVA"])
    assert summaries == []
