"""The text the system prompt gains when the wiki has datasets is a prompt file per
language (`prompts/dataset_addendum.<lang>.md`): a Spanish wiki received it in English."""

import pytest

from domain import prompts
from domain.chat.agent import create_agent
from tests.unit.test_chat_agent_datasets import _VALID_DATASET as DATASET

# The value of `_DATASET_PROMPT_ADDENDUM` (base/domain/chat/agent.py) before the move.
OLD_EN = (
    "\n## Structured datasets — current values\n\n"
    "This wiki also has structured datasets (periodically-refreshed tabular "
    "values such as rates, prices, or stats) — distinct from the prose wiki "
    "pages. For a question about a **current value** in a known category, call "
    "query_dataset(categoria, clave?, metrica?) instead of recalling a number "
    "from a wiki page or from memory: dataset values change over time and must "
    "always be quoted verbatim with their as_of date and fuente, exactly like any "
    "other citation. If query_dataset returns no rows, say so plainly — never "
    "estimate or recall a dataset value from memory."
)



def test_the_english_file_equals_the_old_text():
    assert prompts.load_prompt("dataset_addendum", "en") == OLD_EN


def test_the_spanish_file_is_a_translation_that_keeps_the_tool_call():
    es = prompts.load_prompt("dataset_addendum", "es")
    assert es != OLD_EN and es.startswith("\n## ")
    assert "query_dataset(categoria, clave?, metrica?)" in es
    assert "as_of" in es and "fuente" in es
    assert "This wiki" not in es


@pytest.mark.parametrize("language", ["en", "es"])
def test_a_wiki_with_datasets_gets_the_text_of_its_language(tmp_path, language):
    (tmp_path / "datasets").mkdir()
    (tmp_path / "datasets" / "plazo_fijo.md").write_text(DATASET, encoding="utf-8")
    agent = create_agent(base_url="http://localhost:1234/v1", api_key="k", model="m",
                         system_prompt="Base.", workspace=tmp_path, language=language)
    (prompt,) = agent._system_prompts
    assert prompts.load_prompt("dataset_addendum", language) in prompt
