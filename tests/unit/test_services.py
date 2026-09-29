"""Tests for base/services/: the logic every user interface calls. No LLM."""

import asyncio
import subprocess
from types import SimpleNamespace

import pytest

from domain.chat.guardrail import REFUSAL_EN
from domain.tools.wiki_fs import create_page
from services import chat, ingest
from services.wiki import (
    PageConflict,
    list_pages,
    open_wiki,
    page_links,
    page_version,
    read_page,
    save_page,
)

_BODY = "Cinderella lost a glass slipper at the royal ball and the prince searched the kingdom. " * 4


@pytest.fixture
def wiki(tmp_path):
    return open_wiki(tmp_path / "tales")


def _page(wiki, slug, title, body=_BODY):
    return create_page(wiki.db_path, wiki.path, "/wiki/concepts/", slug, title, body, [])


# ── services.wiki ────────────────────────────────────────────────────────────

def test_open_wiki_creates_folders_index_and_workspace_row(wiki):
    assert wiki.wiki_dir.is_dir() and wiki.sources_dir.is_dir()
    assert wiki.language == "en"
    assert ingest.wiki_stats(wiki) == {"sources": 0, "pages": 0}


def test_list_and_read_pages(wiki):
    _page(wiki, "cinderella", "Cinderella")
    assert list_pages(wiki) == ["concepts/cinderella"]
    assert "glass slipper" in read_page(wiki, "concepts/cinderella")
    assert read_page(wiki, "concepts/missing") == ""


def test_a_page_id_cannot_escape_the_wiki(wiki):
    with pytest.raises(ValueError):
        read_page(wiki, "../../etc/passwd")


def test_page_links_resolve_relative_to_the_page():
    content = ("See [the prince](prince.md), [the tale](../summaries/cinderella.md), "
               "[a site](https://example.com) and ![img](pic.md).")
    pages = ["concepts/prince", "summaries/cinderella"]
    assert page_links(content, "concepts/cinderella", pages) == {
        "the prince": "concepts/prince", "the tale": "summaries/cinderella",
    }


def test_save_page_keeps_front_matter_bumps_version_and_commits(wiki):
    _page(wiki, "cinderella", "Cinderella")
    version = page_version(wiki, "concepts/cinderella")
    new_version = save_page(wiki, "concepts/cinderella", "Edited body. " + _BODY, version)
    text = read_page(wiki, "concepts/cinderella")
    assert text.startswith("---\n") and "title: Cinderella" in text
    assert "Edited body." in text
    assert new_version == version + 1
    log = subprocess.run(["git", "-C", str(wiki.path), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert "edit: concepts/cinderella" in log


def test_save_page_refuses_a_stale_version(wiki):
    _page(wiki, "cinderella", "Cinderella")
    version = page_version(wiki, "concepts/cinderella")
    save_page(wiki, "concepts/cinderella", "First edit. " + _BODY, version)
    with pytest.raises(PageConflict):
        save_page(wiki, "concepts/cinderella", "Second edit. " + _BODY, version)


# ── services.chat ────────────────────────────────────────────────────────────

def _msg(role, content):
    return SimpleNamespace(role=role, content=content)


def test_model_history_drops_refused_exchanges():
    history = chat.model_history([
        _msg("user", "capital of France?"), _msg("assistant", REFUSAL_EN),
        _msg("user", "who is Cinderella?"), _msg("assistant", "A girl in a tale."),
    ])
    assert [type(m).__name__ for m in history] == ["ModelRequest", "ModelResponse"]


class _Agent:
    def __init__(self, output):
        self.output = output
        self.prompts = []

    async def run(self, prompt, deps, message_history):
        self.prompts.append(prompt)
        return SimpleNamespace(output=self.output, all_messages=lambda: [])


def _agents(output):
    config = SimpleNamespace(language="en", off_limits=[], data_aliases={},
                             system_prompt="", suggested_prompts=[])
    return chat.ChatAgents(config=config, agent=_Agent(output), agent_pre_retrieval=_Agent(output))


def test_strict_turn_without_tool_evidence_is_refused(wiki):
    agents = _agents("Paris.")
    answer = asyncio.run(chat.chat_turn(
        wiki, agents, [_msg("user", "capital of France?")], mode=chat.STRICT,
    ))
    assert answer == REFUSAL_EN
    assert agents.agent.prompts == ["capital of France?"]


def test_pre_retrieval_turn_refuses_before_the_model(wiki):
    agents = _agents("should not run")
    answer = asyncio.run(chat.chat_turn(
        wiki, agents, [_msg("user", "Will it rain tomorrow in Rosario?")], mode=chat.PRE_RETRIEVAL,
    ))
    assert answer.startswith("I cannot answer this question: it names none")
    assert agents.agent_pre_retrieval.prompts == []


def test_chat_turn_rejects_the_streaming_mode(wiki):
    with pytest.raises(ValueError):
        asyncio.run(chat.chat_turn(wiki, _agents("x"), [_msg("user", "q")], mode=chat.STREAMING))


# ── services.ingest ──────────────────────────────────────────────────────────

def test_related_pages_of_no_source_is_empty(wiki):
    assert ingest.related_pages(wiki.db_path, []) == set()


def test_list_sources_and_stats_count_the_index(wiki):
    _page(wiki, "cinderella", "Cinderella")
    assert ingest.list_sources(wiki) == []
    assert ingest.wiki_stats(wiki) == {"sources": 0, "pages": 1}


def test_delete_stale_pages_with_none_stale(wiki):
    _page(wiki, "cinderella", "Cinderella")
    assert ingest.delete_stale_pages(wiki) == (0, [])
