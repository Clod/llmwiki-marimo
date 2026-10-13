"""The battery script, run with simulated agents and the fake verifier."""

import importlib.util
import json
from pathlib import Path

import pytest

from domain.tools.wiki_fs import create_page
from domain.verify import FakeVerifier
from services import chat
from services.wiki import open_wiki, save_page
from tests.unit.test_chat_context import BODY, agents as make_agents, tool_messages

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def battery():
    spec = importlib.util.spec_from_file_location("verify_battery_under_test", REPO_ROOT / "scripts" / "verify_battery.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def wiki(tmp_path):
    wiki = open_wiki(tmp_path / "tales")
    create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "cinderella", "Cinderella", BODY, [])
    save_page(wiki, "concepts/cinderella", BODY, None)
    return wiki


ANSWER = "Cinderella lost a glass slipper at the royal ball. The prince married a mermaid in Atlantis."


def test_the_question_file_ignores_comments_and_blank_lines(battery, tmp_path):
    path = tmp_path / "q.txt"
    path.write_text("# draft\n\nWho is Cinderella?\n  # indented comment\nWho is the prince?\n", encoding="utf-8")
    assert battery.read_questions(path) == ["Who is Cinderella?", "Who is the prince?"]


def test_the_shipped_question_file_has_about_ten_questions(battery):
    questions = battery.read_questions(REPO_ROOT / "scripts" / "verify_battery" / "finanzas-argentinas.txt")
    assert 9 <= len(questions) <= 14 and all(q.endswith("?") for q in questions)
    assert "DRAFT" in (REPO_ROOT / "scripts" / "verify_battery" / "finanzas-argentinas.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", [chat.PRE_RETRIEVAL, chat.STRICT, chat.STREAMING])
async def test_every_mode_is_asked_runs_times_and_verified(battery, wiki, mode):
    agents = make_agents(ANSWER, tool_messages())
    results = await battery.run_battery(
        wiki, agents, FakeVerifier(), ["Who is Cinderella?"], mode=mode, runs=2, model="sim")
    assert [r["run"] for r in results] == [1, 2]
    first = results[0]
    assert first["mode"] == mode and first["verifier"] == "fake" and first["model"] == "sim"
    assert first["checked"] == 2 and first["unsupported"] == 1 and first["supported_share"] == 0.5
    bad = [s for s in first["sentences"] if not s["supported"]]
    assert [s["text"] for s in bad] == ["The prince married a mermaid in Atlantis."]
    assert "/wiki/concepts/cinderella.md" in first["context_pages"] or mode != chat.PRE_RETRIEVAL


async def test_a_failing_question_is_recorded_and_the_battery_goes_on(battery, wiki):
    class Broken(FakeVerifier):
        def entailment(self, pairs):
            raise RuntimeError("boom")

    results = await battery.run_battery(wiki, make_agents(ANSWER, tool_messages()), Broken(),
                                        ["Who is Cinderella?", "Who is the prince?"], mode=chat.STRICT, runs=1)
    assert [r["error"] for r in results] == ["RuntimeError: boom"] * 2


async def test_the_files_hold_one_line_per_run_and_a_summary_per_question(battery, wiki, tmp_path):
    results = await battery.run_battery(
        wiki, make_agents(ANSWER, tool_messages()), FakeVerifier(),
        ["Who is Cinderella?", "Will it rain tomorrow in Rosario?"], mode=chat.PRE_RETRIEVAL, runs=2)
    path = tmp_path / "out.jsonl"
    battery.write_jsonl(results, path)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 4 and lines[0]["question"] == "Who is Cinderella?"

    summary = battery.render_summary(results)
    assert "## Who is Cinderella?" in summary and "Supported: 50% of 4 sentences." in summary
    assert "- The prince married a mermaid in Atlantis. (2/2)" in summary
    assert "## Will it rain tomorrow in Rosario?" in summary and "no sentence to verify" in summary
    assert "Mean supported share over 1 questions: 50%." in summary


def test_an_empty_result_list_has_a_summary(battery):
    assert "No results." in battery.render_summary([])
