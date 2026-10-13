"""The prompts of the ingestion and of the lint live in files (`prompts/<name>.<lang>.md`).

`OLD` holds the value each constant had in the code before the move (copied at the start of the
work, from `wiki_generator.py`, `lint/checks.py` and `Locale.content_directive`). The English
files must equal it byte for byte, so the prompts an English wiki sends do not change. The
Spanish `content_directive` moved unchanged.
"""

from __future__ import annotations

import json
import re

import pytest

from domain import prompts
from domain.i18n import get_locale, with_content_directive
from domain.ingestion.wiki_generator import (
    ExtractedConcept,
    build_concept_page,
    extract_dataset_aliases,
    extract_structured,
    structure_chat_content,
    update_overview,
)
from domain.lint.checks import contradiction_check, data_gap_check
from tests.helpers.fake_llm import FakeLLMClient
from tests.helpers.workspace import WorkspaceFixture

OLD = {'ingest_system': {'en': 'You are a knowledge-base assistant. Your task is to write a '
                         'structured wiki page\n'
                         'that summarises the provided document. Be factual, concise, and '
                         'well-organised.\n'
                         'Use markdown formatting. Do not invent information that is not in '
                         'the document.'},
 'ingest_user_template': {'en': 'Write a wiki page for the following document.\n'
                                '\n'
                                'Document metadata:\n'
                                '- Filename: {filename}\n'
                                '- File type: {file_type}\n'
                                '- Pages: {page_count}\n'
                                '- Parser: {parser}\n'
                                '- Ingested: {ingested_at}\n'
                                '\n'
                                'Document content:\n'
                                '---\n'
                                '{content}\n'
                                '---\n'
                                '\n'
                                'Produce a wiki page with exactly these sections in this '
                                'order:\n'
                                '\n'
                                '# {title}\n'
                                '\n'
                                '**Source:** {filename} | **Type:** {file_type} | **Pages:** '
                                '{page_count} | **Ingested:** {ingested_at}\n'
                                '\n'
                                '## Summary\n'
                                '(2–4 paragraphs: what the document is about, its purpose, '
                                'main conclusions)\n'
                                '\n'
                                '## Key Topics\n'
                                '(bullet list of the main themes and subjects covered)\n'
                                '\n'
                                '## Key Entities\n'
                                '(bullet list of notable people, organisations, places, and '
                                'dates mentioned)\n'
                                '\n'
                                '## Important Data & Figures\n'
                                '(bullet list of notable numbers, statistics, metrics, or '
                                'conclusions)\n'
                                '\n'
                                '## Source Information\n'
                                '- **File:** {filename}\n'
                                '- **Type:** {file_type}\n'
                                '- **Pages:** {page_count}\n'
                                '- **Ingested:** {ingested_at}\n'
                                '- **Parser:** {parser}\n'
                                '\n'
                                'Output only the markdown. Do not add any preamble or '
                                'explanation outside the page.'},
 'extract_system': {'en': 'You are a knowledge extraction assistant. Given a document, '
                          'extract:\n'
                          '1. A concise summary (2-3 sentences)\n'
                          '2. Key concepts: entities, instruments, or themes worth a '
                          'standalone wiki page.\n'
                          '   For each concept give a short, clean canonical name (a noun '
                          'phrase — no\n'
                          '   parenthetical glosses, no "X vs Y") plus any aliases: other '
                          'names for exactly\n'
                          "   the SAME thing (abbreviations, synonyms, an acronym's "
                          'expansion). Never list a\n'
                          '   related-but-different concept as an alias.\n'
                          '\n'
                          'Respond with valid JSON only — no markdown fences, no commentary.'},
 'extract_user_template': {'en': 'Extract knowledge from this document.\n'
                                 '\n'
                                 'Document: {filename} ({file_type}, {page_count} pages)\n'
                                 '\n'
                                 'Content:\n'
                                 '---\n'
                                 '{content}\n'
                                 '---\n'
                                 '\n'
                                 'Return JSON with this exact structure:\n'
                                 '{{\n'
                                 '  "document_summary": "2-3 sentence summary of the '
                                 'document",\n'
                                 '  "concepts": [\n'
                                 '    {{\n'
                                 '      "name": "Short canonical name — a noun phrase, no '
                                 'parentheses or vs-comparisons",\n'
                                 '      "category": "entity|instrument|theme",\n'
                                 '      "insight": "The specific new insight this document '
                                 'adds about this concept",\n'
                                 '      "aliases": ["other names for the exact same thing", '
                                 '"abbreviations, synonyms"]\n'
                                 '    }}\n'
                                 '  ]\n'
                                 '}}\n'
                                 '\n'
                                 'Extract 2-5 concepts. Only include concepts worth a '
                                 'dedicated wiki page. Use an\n'
                                 'empty list for "aliases" when the concept has no common '
                                 'alternate names.'},
 'concept_system': {'en': 'You are a wiki page author. Write clear, factual concept pages in '
                          'markdown.\n'
                          'Each page should have a brief definition, key characteristics, and '
                          'context.\n'
                          'Never invent facts not supported by the provided content.\n'
                          '\n'
                          'Preserve traceability above all. If the provided content carries '
                          'inline citations — e.g. (wiki/summaries/cinderella.md) or '
                          '(Cinderella.pdf, p. 3) — keep EVERY one exactly as written, '
                          'attached to the same claim. Never drop, merge, summarise away, or '
                          'reword a citation while reshaping the text. A claim you cannot keep '
                          'its citation for must be removed, not left uncited.'},
 'concept_new_template': {'en': 'Write a wiki concept page for: {name}\n'
                                '\n'
                                'Category: {category}\n'
                                'New insight from document "{filename}": {insight}\n'
                                '\n'
                                'Produce a markdown page with this structure:\n'
                                '\n'
                                '# {name}\n'
                                '\n'
                                '## {h_definition}\n'
                                '(1-2 sentences defining the concept)\n'
                                '\n'
                                '## {h_key_characteristics}\n'
                                '(bullet list)\n'
                                '\n'
                                '## {h_context}\n'
                                '(how this concept relates to the broader domain)\n'
                                '\n'
                                '## {h_sources}\n'
                                '- {filename}\n'
                                '\n'
                                'Output only the markdown.'},
 'concept_update_template': {'en': 'Update this existing wiki concept page for: {name}\n'
                                   '\n'
                                   'New insight from document "{filename}": {insight}\n'
                                   '\n'
                                   'Existing page:\n'
                                   '---\n'
                                   '{existing}\n'
                                   '---\n'
                                   '\n'
                                   'Integrate the new insight. Add the source to the Sources '
                                   'section as a new list item: - {filename}.\n'
                                   'Keep all existing content. Output only the updated '
                                   'markdown page.'},
 'chat_concept_new_template': {'en': 'Write a wiki concept page for: {name}\n'
                                     '\n'
                                     'Category: {category}\n'
                                     'Content synthesised from a chat conversation:\n'
                                     '---\n'
                                     '{content}\n'
                                     '---\n'
                                     '\n'
                                     'Produce a markdown page with this structure:\n'
                                     '\n'
                                     '# {name}\n'
                                     '\n'
                                     '## {h_definition}\n'
                                     '(1-2 sentences defining the concept)\n'
                                     '\n'
                                     '## {h_key_characteristics}\n'
                                     '(bullet list)\n'
                                     '\n'
                                     '## {h_context}\n'
                                     '(how this concept relates to the broader domain)\n'
                                     '\n'
                                     '## {h_sources}\n'
                                     '(list each distinct source cited in the content above, '
                                     'one per line — e.g. "- wiki/summaries/cinderella.md" or '
                                     '"- Cinderella.pdf, p. 3")\n'
                                     '\n'
                                     'Keep every inline citation from the content above '
                                     'attached to the exact claim it supports — carry them '
                                     'verbatim into the Definition, Key Characteristics, and '
                                     'Context sections. Do NOT strip citations when reshaping '
                                     'into this structure, and do NOT replace them with a '
                                     'generic "Chat synthesis" line. Output only the '
                                     'markdown.'},
 'chat_concept_update_template': {'en': 'Update this existing wiki concept page for: {name}\n'
                                        '\n'
                                        'New content from a chat synthesis to integrate:\n'
                                        '---\n'
                                        '{content}\n'
                                        '---\n'
                                        '\n'
                                        'Existing page:\n'
                                        '---\n'
                                        '{existing}\n'
                                        '---\n'
                                        '\n'
                                        'Merge the new content into the existing page. Add '
                                        'insights, avoid duplication. Keep every inline '
                                        'citation from BOTH the existing page and the new '
                                        'content exactly as written, attached to its claim — '
                                        'never drop or reword one. In the Sources section, '
                                        'keep the existing entries and add any new distinct '
                                        'source cited in the new content (one per line, e.g. '
                                        '"- wiki/summaries/cinderella.md").\n'
                                        'Keep all existing content intact. Output only the '
                                        'updated markdown page.'},
 'overview_system': {'en': 'You are a knowledge base curator. Rewrite the overview page to '
                           'reflect the current\n'
                           'state of the knowledge base. Be narrative and synthetic — do not '
                           'just list pages.'},
 'overview_template': {'en': 'Update the knowledge base overview incorporating the new '
                             'document.\n'
                             '\n'
                             'Current overview:\n'
                             '---\n'
                             '{current_overview}\n'
                             '---\n'
                             '\n'
                             'New document summary: {new_summary}\n'
                             '\n'
                             'Known concepts: {concept_list}\n'
                             '\n'
                             'Write an updated overview (3-5 paragraphs) that synthesises all '
                             'the knowledge.\n'
                             'Output only the markdown (no frontmatter).'},
 'dataset_alias_system': {'en': 'You map each KNOWN DATA TERM to the common alternative names '
                                'or nicknames a user would type for the SAME thing. Return '
                                'ONLY JSON, no prose:\n'
                                '\n'
                                '{"aliases": {"<term>": ["<other name>", ...], ...}}\n'
                                '\n'
                                'Rules:\n'
                                '- An alias must mean EXACTLY the same thing as the term — a '
                                'nickname, an abbreviation, or a true synonym. NEVER list a '
                                'related-but-different instrument as an alias (for example, '
                                '"acciones" is NOT an alias of "CEDEAR").\n'
                                '- Only use the terms you are given as keys. Omit a term '
                                'entirely if it has no common alias — do not invent one.\n'
                                '- Keep it short: a handful of real aliases per term at most.'},
 'dataset_alias_user_template': {'en': 'Known data terms:\n'
                                       '{terms}\n'
                                       '\n'
                                       'Return the JSON aliases map.'},
 'lint_contradiction_system': {'en': 'You are a fact-checker reviewing a knowledge base for '
                                     'internal consistency.'},
 'lint_contradiction_template': {'en': 'Compare these two wiki pages and identify any factual '
                                       'contradictions.\n'
                                       '\n'
                                       'Page 1 ({path_a}):\n'
                                       '{content_a}\n'
                                       '\n'
                                       'Page 2 ({path_b}):\n'
                                       '{content_b}\n'
                                       '\n'
                                       'Reply with "CONTRADICTION: <description>" if you find '
                                       'contradictions, or "NO CONTRADICTION" if none.'},
 'lint_gap_template': {'en': 'Review these wiki concept pages and identify important topics '
                             'that are missing or underdeveloped.\n'
                             '\n'
                             'Concepts covered:\n'
                             '{concept_list}\n'
                             '\n'
                             'Reply with one "GAP: <topic> — <suggestion>" per line for gaps '
                             'you find, or "NO GAPS" if coverage looks comprehensive.'},
 'content_directive': {'en': '',
                       'es': 'Redacta TODA la salida en español, con un español natural y '
                             'fluido, independientemente del idioma del documento de origen. '
                             'Traduce al español los títulos de sección y las etiquetas que se '
                             'te indiquen.'}}

NAMES = tuple(OLD)

# Keys that the parsers read (`_parse_extraction`, `_parse_dataset_aliases`) and the markers that
# the lint checks read from the answer (`contradiction_check`, `data_gap_check`). They are code:
# both languages must carry them.
KEYS = {
    "extract_user_template": ['"document_summary"', '"concepts"', '"name"', '"category"',
                              '"insight"', '"aliases"', "entity|instrument|theme"],
    "extract_system": ["JSON"],
    "dataset_alias_system": ['{"aliases": {"<'],
    "lint_contradiction_template": ['"CONTRADICTION: <', '"NO CONTRADICTION"'],
    "lint_gap_template": ['"GAP: <', "—", '"NO GAPS"'],
}


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"(?<!{){(\w+)}(?!})", text))


def _doubled(text: str) -> list[str]:
    return re.findall(r"{{|}}", text)


@pytest.mark.parametrize("name", NAMES)
def test_the_english_file_equals_the_text_the_code_had(name):
    assert prompts.load_prompt(name, "en") == OLD[name]["en"]


def test_the_spanish_content_directive_moved_unchanged():
    assert prompts.load_prompt("content_directive", "es") == OLD["content_directive"]["es"]
    assert get_locale("es").content_directive == OLD["content_directive"]["es"]
    assert get_locale("en").content_directive == ""


@pytest.mark.parametrize("name", NAMES)
def test_every_prompt_has_a_file_in_both_languages(name):
    for language in ("en", "es"):
        assert (prompts.PROMPTS_DIR / f"{name}.{language}.md").is_file()


@pytest.mark.parametrize("name", [n for n in NAMES if n != "content_directive"])
def test_the_spanish_file_translates_the_english_one(name):
    en, es = prompts.load_prompt(name, "en"), prompts.load_prompt(name, "es")
    assert es != en and es.strip()
    assert _placeholders(es) == _placeholders(en)
    assert sorted(_doubled(es)) == sorted(_doubled(en))
    assert [line.split()[0] for line in es.splitlines() if line.startswith("#")] == \
           [line.split()[0] for line in en.splitlines() if line.startswith("#")]
    assert len(es.splitlines()) == len(en.splitlines())


@pytest.mark.parametrize("name", [n for n in NAMES if n.endswith("_template")])
def test_the_spanish_file_can_be_formatted_like_the_english_one(name):
    en = prompts.load_prompt(name, "en")
    values = {p: f"<{p}>" for p in _placeholders(en)}
    values.update({"h_definition": "H1", "h_key_characteristics": "H2", "h_context": "H3",
                   "h_sources": "H4"} if "h_definition" in values else {})
    assert prompts.load_prompt(name, "es").format(**values)


@pytest.mark.parametrize("name,keys", KEYS.items())
def test_the_keys_that_the_parsers_read_appear_in_both_languages(name, keys):
    for language in ("en", "es"):
        text = prompts.load_prompt(name, language)
        for key in keys:
            assert key in text, (name, language, key)


def test_the_fixed_identifiers_survive_the_translation():
    for name in ("concept_system", "chat_concept_new_template", "chat_concept_update_template"):
        en, es = prompts.load_prompt(name, "en"), prompts.load_prompt(name, "es")
        for fixed in ("wiki/summaries/cinderella.md", "Cinderella.pdf"):
            assert es.count(fixed) == en.count(fixed), (name, fixed)


# ── a Spanish wiki sends the Spanish prompts ─────────────────────────────────

_DOC = {"filename": "doc.pdf", "file_type": "pdf", "page_count": 1}
_PAGES = [(1, "Contenido del documento.")]
_CONCEPT = ExtractedConcept("Banco Central", "entity", "Controla la política monetaria")
_ES_DIRECTIVE = OLD["content_directive"]["es"]


def _messages(llm):
    (call,) = llm.calls
    return call["messages"][0]["content"], call["messages"][1]["content"]


def _spanish(text, name):
    return text.startswith(prompts.load_prompt(name, "es").split("{", 1)[0][:60])


def test_extraction_in_a_spanish_wiki_sends_the_spanish_prompts():
    llm = FakeLLMClient(response_content=json.dumps({"document_summary": "R", "concepts": []}))
    extract_structured(_DOC, _PAGES, llm, "fake", language="es")
    system, user = _messages(llm)
    assert system == f"{prompts.load_prompt('extract_system', 'es')}\n\n{_ES_DIRECTIVE}"
    assert user.startswith(prompts.load_prompt("extract_user_template", "es").split("{", 1)[0])
    assert user != prompts.load_prompt("extract_user_template", "en").format(
        filename="doc.pdf", file_type="pdf", page_count=1, content="[Page 1]\nContenido del documento.")


def test_extraction_in_an_english_wiki_sends_the_english_prompts():
    llm = FakeLLMClient(response_content=json.dumps({"document_summary": "R", "concepts": []}))
    extract_structured(_DOC, _PAGES, llm, "fake", language="en")
    system, user = _messages(llm)
    assert system == OLD["extract_system"]["en"]
    assert user == OLD["extract_user_template"]["en"].format(
        filename="doc.pdf", file_type="pdf", page_count=1, content="[Page 1]\nContenido del documento.")


@pytest.mark.parametrize("existing,new_name,update_name", [(None, "concept_new_template", None),
                                                          ("# X\n\nTexto.\n", None, "concept_update_template")])
def test_a_concept_page_in_a_spanish_wiki_sends_the_spanish_prompts(existing, new_name, update_name):
    llm = FakeLLMClient(response_content="# Banco Central\n\nContenido.\n")
    build_concept_page(_CONCEPT, "doc.pdf", existing, llm, "fake", language="es")
    system, user = _messages(llm)
    assert system == f"{prompts.load_prompt('concept_system', 'es')}\n\n{_ES_DIRECTIVE}"
    template = prompts.load_prompt(new_name or update_name, "es")
    assert user.startswith(template.split("{", 1)[0])
    assert user != OLD[new_name or update_name]["en"].split("{", 1)[0]


@pytest.mark.parametrize("existing,name", [(None, "chat_concept_new_template"),
                                           ("# X\n\nTexto.\n", "chat_concept_update_template")])
def test_a_chat_page_in_a_spanish_wiki_sends_the_spanish_prompts(existing, name):
    llm = FakeLLMClient(response_content="# T\n\nContenido.\n")
    structure_chat_content("T", "concept", "Texto (a.md).", existing, llm, "fake", language="es")
    system, user = _messages(llm)
    assert system == f"{prompts.load_prompt('concept_system', 'es')}\n\n{_ES_DIRECTIVE}"
    assert user.startswith(prompts.load_prompt(name, "es").split("{", 1)[0])


def test_the_overview_in_a_spanish_wiki_sends_the_spanish_prompts():
    llm = FakeLLMClient(response_content="Panorama.")
    update_overview("# O\n\nViejo.\n", "Nuevo.", ["A", "B"], llm, "fake", language="es")
    system, user = _messages(llm)
    assert system == f"{prompts.load_prompt('overview_system', 'es')}\n\n{_ES_DIRECTIVE}"
    assert user.startswith(prompts.load_prompt("overview_template", "es").split("{", 1)[0])


def test_the_dataset_aliases_in_a_spanish_wiki_send_the_spanish_prompts():
    llm = FakeLLMClient(response_content=json.dumps({"aliases": {"CEDEAR": ["cedears"]}}))
    assert extract_dataset_aliases(["CEDEAR"], llm, "fake", language="es") == {"CEDEAR": ["cedears"]}
    system, user = _messages(llm)
    assert system == f"{prompts.load_prompt('dataset_alias_system', 'es')}\n\n{_ES_DIRECTIVE}"
    assert user.startswith(prompts.load_prompt("dataset_alias_user_template", "es").split("{", 1)[0])


def test_with_content_directive_keeps_its_behaviour():
    assert with_content_directive("base", "en") == "base"
    assert with_content_directive("base", "es") == f"base\n\n{_ES_DIRECTIVE}"


# ── the lint in a Spanish wiki ───────────────────────────────────────────────

def _spanish_workspace(ws: WorkspaceFixture) -> None:
    (ws.workspace / "wiki_config.toml").write_text('[wiki]\nlanguage = "es"\n', encoding="utf-8")


def test_the_contradiction_check_in_a_spanish_wiki_sends_the_spanish_prompts(
        tmp_workspace: WorkspaceFixture) -> None:
    from tests.unit.test_lint_contradictions import _setup_two_related_concepts
    _setup_two_related_concepts(tmp_workspace)
    _spanish_workspace(tmp_workspace)
    llm = FakeLLMClient(response_content="CONTRADICTION: A sube, B baja")
    issues = contradiction_check(tmp_workspace.db_path, tmp_workspace.workspace, llm, "fake")
    assert any(i.check == "contradiction" for i in issues)
    system, user = _messages(llm)
    assert system == prompts.load_prompt("lint_contradiction_system", "es")
    assert user.startswith(prompts.load_prompt("lint_contradiction_template", "es").split("{", 1)[0])


def test_the_contradiction_check_in_an_english_wiki_sends_the_english_prompts(
        tmp_workspace: WorkspaceFixture) -> None:
    from tests.unit.test_lint_contradictions import _setup_two_related_concepts
    _setup_two_related_concepts(tmp_workspace)
    llm = FakeLLMClient(response_content="NO CONTRADICTION")
    contradiction_check(tmp_workspace.db_path, tmp_workspace.workspace, llm, "fake")
    system, user = _messages(llm)
    assert system == OLD["lint_contradiction_system"]["en"]
    assert user.startswith(OLD["lint_contradiction_template"]["en"].split("{", 1)[0])


def test_the_gap_check_in_a_spanish_wiki_sends_the_spanish_prompt(
        tmp_workspace: WorkspaceFixture) -> None:
    from domain.tools.wiki_fs import create_page
    from tests.unit.test_lint_contradictions import _CONTENT_A
    create_page(tmp_workspace.db_path, tmp_workspace.workspace, "/wiki/concepts/",
                "federal-reserve", "Federal Reserve", _CONTENT_A, [])
    _spanish_workspace(tmp_workspace)
    llm = FakeLLMClient(response_content="NO GAPS")
    assert data_gap_check(tmp_workspace.db_path, tmp_workspace.workspace, llm, "fake") == []
    (call,) = llm.calls
    (message,) = call["messages"]
    assert message["content"].startswith(
        prompts.load_prompt("lint_gap_template", "es").split("{", 1)[0])
