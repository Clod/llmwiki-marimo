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
    delete_page,
    list_pages,
    open_wiki,
    page_links,
    page_version,
    read_page,
    save_page,
    source_view,
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


def test_delete_page_removes_file_and_row_and_commits(wiki):
    _page(wiki, "cinderella", "Cinderella")
    assert delete_page(wiki, "concepts/cinderella") is True
    assert "concepts/cinderella" not in list_pages(wiki)
    assert page_version(wiki, "concepts/cinderella") is None
    log = subprocess.run(["git", "-C", str(wiki.path), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert "delete: concepts/cinderella" in log


def test_delete_page_refuses_the_three_fixed_pages(wiki):
    for page in ("index", "overview", "log"):
        with pytest.raises(ValueError):
            delete_page(wiki, page)


def test_delete_page_of_a_missing_page_returns_false(wiki):
    assert delete_page(wiki, "concepts/no-such-page") is False


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


def _mark_stale(wiki, page):
    from domain.tools.db import get_connection
    with get_connection(wiki.db_path) as conn:
        conn.execute("UPDATE documents SET stale_since = datetime('now') WHERE relative_path = ?",
                     (f"wiki/{page}.md",))
        conn.commit()


def test_list_stale_pages_names_each_stale_page(wiki):
    _page(wiki, "cinderella", "Cinderella")
    _page(wiki, "slipper", "Glass slipper")
    _mark_stale(wiki, "concepts/slipper")
    assert [p["title"] for p in ingest.list_stale_pages(wiki)] == ["Glass slipper"]


def test_delete_stale_pages_deletes_and_commits(wiki):
    _page(wiki, "slipper", "Glass slipper")
    _mark_stale(wiki, "concepts/slipper")
    deleted, _ = ingest.delete_stale_pages(wiki)
    assert deleted == 1 and "concepts/slipper" not in list_pages(wiki)
    log = subprocess.run(["git", "-C", str(wiki.path), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert "delete stale pages: 1" in log


def test_delete_source_of_an_unknown_id_fails_with_a_message(wiki):
    ok, message = ingest.delete_source(wiki, "no-such-id")
    assert ok is False and message


def test_tool_status_reports_libreoffice_and_java(monkeypatch):
    import domain.ingestion.extractor as extractor
    monkeypatch.setattr(extractor, "check_libreoffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(extractor, "check_java", lambda: None)
    monkeypatch.setattr(extractor, "libreoffice_can_convert", lambda: True)
    assert ingest.tool_status() == {
        "libreoffice": "/usr/bin/soffice", "libreoffice_found": "/usr/bin/soffice", "java": None,
    }


def test_tool_status_does_not_report_a_libreoffice_that_cannot_convert(monkeypatch):
    import domain.ingestion.extractor as extractor
    monkeypatch.setattr(extractor, "check_libreoffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(extractor, "libreoffice_can_convert", lambda: False)
    monkeypatch.setattr(extractor, "check_java", lambda: "/usr/bin/java")
    status = ingest.tool_status()
    assert status["libreoffice"] is None
    assert status["libreoffice_found"] == "/usr/bin/soffice"


def test_tool_status_with_no_libreoffice_does_not_probe(monkeypatch):
    import domain.ingestion.extractor as extractor
    monkeypatch.setattr(extractor, "check_libreoffice", lambda: None)
    monkeypatch.setattr(extractor, "libreoffice_can_convert", lambda: pytest.fail("probed"))
    monkeypatch.setattr(extractor, "check_java", lambda: None)
    assert ingest.tool_status() == {"libreoffice": None, "libreoffice_found": None, "java": None}


# ── services.chat: save a conversation as a page ─────────────────────────────

def test_conversation_markdown_keeps_every_answered_exchange_in_order():
    messages = [
        _msg("user", "Who lost a slipper?"), _msg("assistant", "Cinderella."),
        _msg("user", "capital of France?"), _msg("assistant", REFUSAL_EN),
        _msg("user", "Who searched for her?"), _msg("assistant", "The prince."),
        _msg("user", "A question still waiting for its answer"),
    ]
    text = chat.conversation_markdown(messages, "en")
    assert text.index("Who lost a slipper?") < text.index("Cinderella.") \
        < text.index("Who searched for her?") < text.index("The prince.")
    assert "France" not in text and REFUSAL_EN not in text   # refused exchange dropped
    assert "still waiting" not in text                      # unanswered question dropped
    assert text.startswith("**Question:**")


def test_conversation_markdown_labels_follow_the_wiki_language():
    text = chat.conversation_markdown([_msg("user", "¿Qué es?"), _msg("assistant", "Un plazo fijo.")], "es")
    assert text.startswith("**Pregunta:** ¿Qué es?") and "**Respuesta:**" in text


def _fake_draft(wiki, monkeypatch):
    """Replace the model call of the draft step; return what it was given."""
    import domain.chat.wiki_tools as wiki_tools

    given = {}

    def draft_wiki_page(db_path, workspace, title, content, category, **kwargs):
        given.update(title=title, content=content, category=category)
        return wiki_tools.PageDraft(title, category, "slipper-story", "wiki/concepts/slipper-story.md",
                                    f"# {title}\n\n{content}", False)

    monkeypatch.setattr(wiki_tools, "draft_wiki_page", draft_wiki_page)
    return given


_MESSAGES = [_msg("user", "Who lost a slipper?"), _msg("assistant", "Cinderella."),
             _msg("user", "Who searched for her?"), _msg("assistant", "The prince.")]


def _git_log(wiki):
    return subprocess.run(["git", "-C", str(wiki.path), "log", "--oneline"], capture_output=True, text=True).stdout


def test_save_conversation_saves_the_whole_conversation_and_commits(wiki, monkeypatch):
    given = _fake_draft(wiki, monkeypatch)
    result = chat.save_conversation(wiki, None, " Slipper story ", _MESSAGES,
                                    base_url="http://x", api_key="k", model="m")
    assert result.startswith("Created wiki/concepts/slipper-story.md")
    assert given["title"] == "Slipper story" and given["category"] == "concept"
    assert "Cinderella." in given["content"] and "The prince." in given["content"]
    assert "The prince." in read_page(wiki, "concepts/slipper-story")
    assert "chat: Slipper story" in _git_log(wiki)


def test_draft_conversation_writes_nothing_and_commits_nothing(wiki, monkeypatch):
    given = _fake_draft(wiki, monkeypatch)
    before = sorted(p for p in wiki.path.rglob("*") if p.is_file() and ".git" not in p.parts)
    draft = chat.draft_conversation(wiki, None, "Slipper story", _MESSAGES,
                                    base_url="http://x", api_key="k", model="m")
    assert draft.path == "wiki/concepts/slipper-story.md" and "The prince." in draft.markdown
    assert given["title"] == "Slipper story"
    assert sorted(p for p in wiki.path.rglob("*") if p.is_file() and ".git" not in p.parts) == before
    assert "chat:" not in _git_log(wiki)
    assert list_pages(wiki) == []


def test_draft_conversation_refuses_a_conversation_without_answers(wiki):
    with pytest.raises(chat.EmptyConversation):
        chat.draft_conversation(wiki, None, "Empty", [_msg("user", "hello?")],
                                base_url="http://x", api_key="k", model="m")


def test_save_reviewed_page_writes_the_given_text_and_commits(wiki):
    text = "# Slipper story\n\nA sentence the user edited.\n"
    result = chat.save_reviewed_page(wiki, " Slipper story ", text)
    assert result.startswith("Created wiki/concepts/slipper-story.md")
    assert "A sentence the user edited." in read_page(wiki, "concepts/slipper-story")
    assert "chat: Slipper story" in _git_log(wiki)
    assert chat.save_reviewed_page(wiki, "Slipper story", text + "More.\n").startswith("Updated ")


def test_save_conversation_refuses_a_conversation_without_answers(wiki):
    with pytest.raises(ValueError):
        chat.save_conversation(wiki, None, "Empty", [_msg("user", "hello?")],
                               base_url="http://x", api_key="k", model="m")


# ── services.wiki.source_view: what the browser shows for a source ──────────

def _source_row(wiki, doc_id, filename):
    from domain.tools.db import get_connection
    with get_connection(wiki.db_path) as conn:
        conn.execute(
            "INSERT INTO documents (id, user_id, filename, relative_path, path, source_kind, file_type, status) "
            "VALUES (?, 'local', ?, ?, 'sources/', 'source', 'docx', 'ready')",
            (doc_id, filename, f"sources/{filename}"),
        )
        conn.commit()


def test_source_view_of_a_pdf_is_the_pdf(wiki):
    (wiki.sources_dir / "Tale.pdf").write_bytes(b"%PDF-1.4")
    assert source_view(wiki, "Tale.pdf") == wiki.sources_dir / "Tale.pdf"


def test_source_view_of_a_docx_is_the_pdf_ingestion_cached(wiki):
    (wiki.sources_dir / "Tale.docx").write_bytes(b"PK")
    _source_row(wiki, "doc-1", "Tale.docx")
    cached = wiki.path / ".llmwiki" / "cache" / "local" / "doc-1" / "converted.pdf"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"%PDF-1.4")
    assert source_view(wiki, "Tale.docx") == cached


def test_source_view_converts_a_docx_whose_pdf_is_missing(wiki, monkeypatch):
    import domain.ingestion.extractor as extractor

    (wiki.sources_dir / "Tale.docx").write_bytes(b"PK")
    _source_row(wiki, "doc-1", "Tale.docx")
    calls = []
    monkeypatch.setattr(extractor, "convert_office_to_pdf",
                        lambda path, cache_dir: calls.append((path.name, cache_dir.name)) or cache_dir / "converted.pdf")
    source_view(wiki, "Tale.docx")
    assert calls == [("Tale.docx", "doc-1")]


@pytest.mark.parametrize("name", ["notas.md", "notas.txt"])
def test_source_view_of_a_text_file_is_the_file_itself(wiki, name):
    (wiki.sources_dir / name).write_text("hola", encoding="utf-8")
    assert source_view(wiki, name) == wiki.sources_dir / name


@pytest.mark.parametrize("name", ["Tale.odt", "Tale.rtf", "Tale.doc", "Tale.docx"])
def test_source_view_of_every_office_format_is_the_cached_pdf(wiki, name):
    (wiki.sources_dir / name).write_bytes(b"x")
    _source_row(wiki, "doc-1", name)
    cached = wiki.path / ".llmwiki" / "cache" / "local" / "doc-1" / "converted.pdf"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"%PDF-1.4")
    assert source_view(wiki, name) == cached


@pytest.mark.parametrize("name", ["Tale.odt", "Tale.rtf", "Tale.doc"])
def test_source_view_converts_an_office_file_whose_pdf_is_missing(wiki, monkeypatch, name):
    import domain.ingestion.extractor as extractor

    (wiki.sources_dir / name).write_bytes(b"x")
    _source_row(wiki, "doc-1", name)
    calls = []
    monkeypatch.setattr(extractor, "convert_office_to_pdf",
                        lambda path, cache_dir: calls.append(path.name) or cache_dir / "converted.pdf")
    source_view(wiki, name)
    assert calls == [name]


def test_source_view_refuses_a_name_outside_the_sources_folder(wiki):
    with pytest.raises(FileNotFoundError):
        source_view(wiki, "../.env")


def test_scan_sources_passes_the_wiki_language(wiki, monkeypatch):
    import domain.ingestion as ingestion
    seen = {}
    monkeypatch.setattr(ingestion, "scan_and_ingest",
                        lambda ws, db, client, model, cb, language: seen.update(ws=ws, language=language) or [])
    assert ingest.scan_sources(wiki, None, "m") == []
    assert seen == {"ws": wiki.path, "language": "en"}


def test_regenerate_summaries_commits_the_regenerated_pages(wiki, monkeypatch):
    import domain.ingestion as ingestion

    def fake_regenerate(ws, db, client, model, cb, language):
        _page(wiki, "regenerated", "Regenerated")
        return [SimpleNamespace(status="ingested")]

    monkeypatch.setattr(ingestion, "regenerate_wiki_pages", fake_regenerate)
    assert len(ingest.regenerate_summaries(wiki, None, "m")) == 1
    log = subprocess.run(["git", "-C", str(wiki.path), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert "regenerate summaries: 1" in log


# ── services.ingest: the name of an uploaded file ────────────────────────────

@pytest.mark.parametrize("raw, clean", [
    ("Tale.pdf", "Tale.pdf"),
    ("../../x.pdf", "x.pdf"),
    ("/etc/y.DOCX", "y.DOCX"),
    ("C:\\Users\\me\\z.pdf", "z.pdf"),
    ("a/b/../c.pdf", "c.pdf"),
])
def test_upload_name_keeps_the_last_path_component(raw, clean):
    assert ingest.Upload(raw, b"x").name == clean


@pytest.mark.parametrize("raw", ["", ".", "..", "../..", "a/..", ".pdf", ".hidden.pdf", "dir/.x.pdf", "/"])
def test_upload_refuses_empty_dot_and_hidden_names(raw):
    with pytest.raises(ingest.InvalidUploadName):
        ingest.Upload(raw, b"x")


@pytest.mark.parametrize("raw", ["notes.csv", "noextension", "a.pdf.exe"])
def test_upload_refuses_extensions_the_ingestion_does_not_read(raw):
    with pytest.raises(ingest.UnsupportedUploadType):
        ingest.Upload(raw, b"x")


def test_ingest_uploads_cannot_write_outside_sources(wiki, monkeypatch, tmp_path):
    import domain.ingestion as ingestion

    seen = []
    monkeypatch.setattr(ingestion, "ingest_file", lambda path, *a, **k: seen.append(path) or SimpleNamespace(
        status="failed", doc_id=None))
    ingest.ingest_uploads(wiki, [ingest.Upload("../../x.pdf", b"x")], None, "m")
    assert seen == [wiki.sources_dir / "x.pdf"]
    assert (wiki.sources_dir / "x.pdf").read_bytes() == b"x"
    assert not (wiki.sources_dir.parent.parent / "x.pdf").exists()
# ── services.wiki.reindex ────────────────────────────────────────────────────

def test_reindex_rebuilds_the_index_of_the_wiki_from_its_files(wiki, monkeypatch):
    from domain.ingestion import extractor
    from services.wiki import reindex

    monkeypatch.setattr(extractor, "extract", lambda path, cache_dir: ([(1, "Cinderella lost a slipper.")], "fake"))
    (wiki.sources_dir / "Tale.pdf").write_bytes(b"%PDF-1.4")
    _page(wiki, "cinderella", "Cinderella")
    lines = []
    report = reindex(wiki, progress=lines.append)
    assert (report.sources_indexed, report.wiki_pages_indexed) == (1, 1)
    assert [s["filename"] for s in ingest.list_sources(wiki)] == ["Tale.pdf"]
    assert ingest.wiki_stats(wiki) == {"sources": 1, "pages": 1}
    assert lines and (wiki.path / ".llmwiki" / "index.db.bak").is_file()
# ── services.wiki.graph ──────────────────────────────────────────────────────

def _source(wiki, filename):
    from domain.tools.db import get_connection

    with get_connection(wiki.db_path) as conn:
        user = "local"
        conn.execute(
            "INSERT INTO documents (id, user_id, filename, relative_path, path, source_kind, file_type, status) "
            "VALUES (?, ?, ?, ?, '/sources/', 'source', 'pdf', 'ready')",
            (f"src-{filename}", user, filename, f"sources/{filename}"))
        conn.commit()


def _graph_wiki(wiki):
    from services.wiki import graph  # noqa: F401

    _page(wiki, "alpha", "Alpha")
    _page(wiki, "beta", "Beta")
    _page(wiki, "lonely", "Lonely")
    create_page(wiki.db_path, wiki.path, "/wiki/summaries/", "doc", "Doc", _BODY, [])
    (wiki.wiki_dir / "index.md").write_text("# Index\n", encoding="utf-8")
    _source(wiki, "report.pdf")
    save_page(wiki, "concepts/alpha",
              _BODY + "\n[b](beta.md) [b again](beta.md) [me](alpha.md) [s](../summaries/doc.md)\n"
              "\n## Sources\n\n- report.pdf\n", None)
    return wiki


def test_graph_has_a_node_per_page_and_source_without_the_fixed_pages(wiki):
    from services.wiki import graph

    data = graph(_graph_wiki(wiki))
    nodes = {n["id"]: n for n in data["nodes"]}
    assert set(nodes) == {"concepts/alpha", "concepts/beta", "concepts/lonely", "summaries/doc",
                          "source:report.pdf"}
    assert nodes["concepts/alpha"]["kind"] == "concept" and nodes["summaries/doc"]["kind"] == "summary"
    assert nodes["source:report.pdf"]["kind"] == "source"
    assert nodes["concepts/alpha"]["title"] == "Alpha"


def test_graph_edges_have_no_duplicates_and_no_self_links(wiki):
    from services.wiki import graph

    data = graph(_graph_wiki(wiki))
    edges = [(e["source"], e["target"], e["type"]) for e in data["edges"]]
    assert sorted(edges) == sorted([
        ("concepts/alpha", "concepts/beta", "links_to"),
        ("concepts/alpha", "summaries/doc", "links_to"),
        ("concepts/alpha", "source:report.pdf", "cites"),
    ])


def test_graph_degree_counts_edges_and_a_page_without_links_still_appears(wiki):
    from services.wiki import graph

    nodes = {n["id"]: n for n in graph(_graph_wiki(wiki))["nodes"]}
    assert nodes["concepts/alpha"]["degree"] == 3
    assert nodes["concepts/beta"]["degree"] == 1
    assert nodes["concepts/lonely"]["degree"] == 0
