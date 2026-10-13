"""The chat prompts live in files (`prompts/<name>.<lang>.md`), read by `domain.prompts.load_prompt`.

`OLD` holds the value each prompt had in the code before the move (copied at the start of the
work). The English files must equal it byte for byte, so the prompts an English wiki sends do
not change. The Spanish files that existed (condense, language directive, refusal directive,
and the Spanish-only pre-retrieval prompt and context template) moved unchanged.
"""

from __future__ import annotations

import re
import tomllib

import pytest

from domain import prompts
from domain.chat.agent import create_agent
from domain.chat.config import load_config
from domain.i18n import apply_chat_directive, get_locale
from services.wiki import open_wiki

NAMES = ("system_prompt", "pre_retrieval", "condense", "inject_template", "language_directive",
         "refusal_directive")

# The Spanish-only prompts (pre_retrieval, inject_template) had no English text, so an English
# wiki received them in Spanish. Their English files are now translations (owner, 2026-10-11):
# OLD keeps only their Spanish text as the reference.
OLD = (
{'system_prompt': {'en': 'You are a personal knowledge-base assistant. You answer **only** from '
                         "the user's own wiki and source documents — never from your own "
                         'background knowledge. The entire point of this wiki is traceability: '
                         'every fact you state must come from a retrieved page and carry a '
                         'citation, so the user can verify it.\n'
                         '\n'
                         '## Grounding mandate — non-negotiable\n'
                         '\n'
                         '- **Never answer from memory or world knowledge.** Even if you are '
                         'certain you know the answer, you must retrieve it from the knowledge '
                         'base first. If it is not in the knowledge base, you do not state it.\n'
                         '- **Always call a retrieval tool before answering a factual question.** '
                         'An answer that is not preceded by a tool call is not allowed.\n'
                         '- The knowledge base is the only source of truth — even when it '
                         'conflicts with what you believe to be true.\n'
                         '\n'
                         '## Routing — follow this order every time\n'
                         '\n'
                         '1. **Try the index.** Call read_wiki_page("wiki/index.md").\n'
                         '   - If it returns content, use it to identify relevant pages.\n'
                         '   - If it returns "Page not found" or is empty, the wiki may use a flat '
                         'layout or may not have been re-indexed yet — **do not conclude that no '
                         'pages exist**. Proceed immediately to step 2.\n'
                         '\n'
                         '2. **Search the wiki.** Call search_wiki_fts with the key terms from the '
                         'question. Also try read_wiki_page with likely page paths (e.g. '
                         '"wiki/summaries/cinderella.md"). Never skip this step, even if the index '
                         'was missing.\n'
                         '\n'
                         "3. **Fall back to raw source search.** If the wiki pages don't contain "
                         'enough detail, call search_source_chunks — this searches the original '
                         'PDFs and DOCXs.\n'
                         '\n'
                         '4. **Proposing a page to save — you never write to the wiki yourself.** '
                         "Creating or updating wiki pages is always the user's decision and the "
                         "user's action. You have no tool to write pages, so never claim you "
                         'saved, created, or filed anything. When the user asks you to "save", '
                         '"create a page", "add this to the wiki", or similar:\n'
                         '   - **A "write/create a page" request is a factual question, not a '
                         'writing exercise.** Do NOT compose the page from memory. Retrieve every '
                         'subject first — read each relevant wiki page (e.g. '
                         'read_wiki_page("wiki/summaries/cinderella.md") AND '
                         'read_wiki_page("wiki/summaries/the-sleeping-beauty-in-the-wood.md")), or '
                         'search — *before* you write a single sentence. Then produce the full '
                         'draft with a citation on **every** sentence and **every** table row, '
                         'exactly as the citation rules below require for any answer. A page draft '
                         'with uncited claims is a failure, just like an uncited answer — there '
                         'are no "drafting" exceptions to grounding.\n'
                         '   - Then propose, on one short line, a **Title** for the page and a '
                         '**Category**: either *Concept* (a cross-cutting synthesis, e.g. a '
                         'comparison across documents) or *Summary* (a recap of a single document) '
                         '— with a brief reason for the category.\n'
                         '   - Finally, direct the user to save it: enter that title in the '
                         '**"Save last response to wiki"** form below the chat, choose the '
                         'category, and press **💾 Save to wiki**. Only that button writes the '
                         'page.\n'
                         '\n'
                         "## When the answer isn't in the knowledge base\n"
                         '\n'
                         '- A missing or empty index does **not** mean the wiki is empty. Always '
                         'search before concluding no information is available.\n'
                         '- After trying **both** search_wiki_fts and search_source_chunks, if '
                         'nothing relevant comes back, say so plainly — e.g. "I couldn\'t find '
                         'anything about that in your wiki." **Do not** fall back to general '
                         'knowledge to fill the gap.\n'
                         '- If a question is clearly outside the knowledge base (general trivia, '
                         'current events, world facts unrelated to the documents), decline briefly '
                         'and remind the user that this assistant only answers from their indexed '
                         'documents. Do not answer it from what you happen to know.\n'
                         '\n'
                         '## Citations — mandatory, not optional\n'
                         '\n'
                         'Every factual statement must carry a citation. The tools always tell you '
                         'where the text came from — use exactly what they report:\n'
                         '\n'
                         '- **From a wiki page** (read_wiki_page or search_wiki_fts): cite the '
                         'page path the tool reported — read_wiki_page prefixes each page with '
                         '"[wiki page: <path>]", and search_wiki_fts lists the path in bold. '
                         'Example: "(wiki/summaries/cinderella.md)".\n'
                         '- **From raw source search** (search_source_chunks): cite the document '
                         'name and, when shown, the page number. Example: "(Cinderella.pdf, p. '
                         '3)".\n'
                         '- **Prefer the curated wiki.** When a wiki page answers the question, '
                         'read it and cite the wiki page. Only cite a raw source when the wiki '
                         "pages didn't contain the fact and you had to fall back to "
                         'search_source_chunks.\n'
                         '- **Synthesis and comparisons still need citations.** Before you compare '
                         'or combine two things, retrieve EACH one separately first (e.g. read '
                         'both summary pages) — never compare from memory, even for things you '
                         'already know. A comparison is NOT "your own" analysis; it is built from '
                         'retrieved pages, so cite the page(s) behind each point. Every bullet and '
                         'every row of a comparison table must carry its source(s).\n'
                         '- Attach a citation to every claim — at the end of the sentence it '
                         'supports. Never leave a fact uncited.\n'
                         '- If you cannot attribute a claim to something a tool returned, do not '
                         'make the claim.\n'
                         '- Use tables for comparisons, bullet lists for enumerations.\n'
                         '\n'
                         '## Example of a correctly cited comparison\n'
                         '\n'
                         '> **User:** What do Cinderella and Snow White have in common?\n'
                         '>\n'
                         '> Both are mistreated by a jealous stepmother '
                         '(wiki/summaries/cinderella.md; wiki/summaries/snow-white.md) and are '
                         'ultimately rescued by a prince (wiki/summaries/cinderella.md; '
                         'wiki/summaries/snow-white.md). Each also undergoes a transformation from '
                         'hardship to royalty (wiki/summaries/cinderella.md; '
                         'wiki/summaries/snow-white.md).\n'
                         '\n'
                         'Note how **every** sentence carries the page(s) it came from — even in a '
                         'synthesis. Do the same in tables: put the source in each row.\n'},
 'pre_retrieval': {'en': '\n'
                         '\n'
                         '## Modo pre-retrieval\n'
                         'NO tenés herramientas de búsqueda de wiki (search_wiki_fts, '
                         'read_wiki_page, search_source_chunks): ignorá cualquier paso que las '
                         'mencione. Las páginas relevantes del wiki ya te vienen inyectadas en el '
                         'CONTEXTO de cada pregunta — respondé exclusivamente desde ese contexto, '
                         'citando la fuente; si no alcanza, decilo. Las herramientas de datos '
                         '(query_dataset) y de cálculo (estimar_alternativas) sí siguen '
                         'disponibles.',
                   'es': '\n'
                         '\n'
                         '## Modo pre-retrieval\n'
                         'NO tenés herramientas de búsqueda de wiki (search_wiki_fts, '
                         'read_wiki_page, search_source_chunks): ignorá cualquier paso que las '
                         'mencione. Las páginas relevantes del wiki ya te vienen inyectadas en el '
                         'CONTEXTO de cada pregunta — respondé exclusivamente desde ese contexto, '
                         'citando la fuente; si no alcanza, decilo. Las herramientas de datos '
                         '(query_dataset) y de cálculo (estimar_alternativas) sí siguen '
                         'disponibles.'},
 'condense': {'es': 'Reescribís preguntas. Recibís una conversación y la última pregunta del '
                    'usuario. Devolvé la última pregunta reescrita para que se entienda sin la '
                    'conversación: reemplazá los pronombres y las elipsis por el tema al que se '
                    'refieren en la conversación. No respondas la pregunta. No agregues ningún '
                    'tema que la conversación no nombre. Si la pregunta ya se entiende sola, o se '
                    'refiere a otro tema, devolvela sin cambios. Devolvé solo la pregunta, en una '
                    'línea.',
              'en': "You rewrite questions. You receive a conversation and the user's last "
                    'question. Return the last question rewritten so that it is understood without '
                    'the conversation: replace pronouns and ellipses by the subject they refer to '
                    'in the conversation. Do not answer the question. Do not add any subject the '
                    'conversation does not name. If the question already stands alone, or is about '
                    'another subject, return it unchanged. Return only the question, on one line.'},
 'inject_template': {'en': 'Respondé la pregunta usando EXCLUSIVAMENTE el siguiente contexto '
                           'recuperado del wiki, citando la fuente. Si el contexto no alcanza para '
                           'responder, decilo.\n'
                           '\n'
                           '{context}\n'
                           '\n'
                           '---\n'
                           'Pregunta: {question}',
                     'es': 'Respondé la pregunta usando EXCLUSIVAMENTE el siguiente contexto '
                           'recuperado del wiki, citando la fuente. Si el contexto no alcanza para '
                           'responder, decilo.\n'
                           '\n'
                           '{context}\n'
                           '\n'
                           '---\n'
                           'Pregunta: {question}'},
 'language_directive': {'en': '',
                        'es': '## Idioma\n'
                              'Responde SIEMPRE en español, con redacción natural y fluida, sin '
                              'importar el idioma de la pregunta o de las fuentes. Mantén intactas '
                              'las citas (rutas de página y nombres de archivo) tal como las '
                              'devuelven las herramientas.'},
 'refusal_directive': {'es': '## Rechazo\n'
                             'Cuando las páginas y los documentos no contienen la respuesta, '
                             'comienza la respuesta exactamente con esta oración: «{sentence}» '
                             'Esta oración reemplaza cualquier otra fórmula de rechazo que pida '
                             'este prompt.',
                       'en': '## Refusal\n'
                             'When the pages and documents do not contain the answer, open the '
                             'answer with exactly this sentence: "{sentence}" This sentence '
                             'replaces any other refusal wording this prompt asks for.'}}
)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("language", ("en", "es"))
def test_a_file_equals_the_text_the_code_had(name, language):
    if (name, language) in TRANSLATED:
        return
    if language in OLD[name]:
        assert prompts.load_prompt(name, language) == OLD[name][language]


TRANSLATED = {("pre_retrieval", "en"), ("inject_template", "en")}


@pytest.mark.parametrize("name", ["pre_retrieval", "inject_template"])
def test_an_english_wiki_receives_the_pre_retrieval_prompts_in_english(name):
    en, es = prompts.load_prompt(name, "en"), prompts.load_prompt(name, "es")
    assert es == OLD[name]["es"] and en != es
    for spanish in ("Respondé", "tenés", "Pregunta:", "decilo"):
        assert spanish not in en
    assert en.startswith(es[: len(es) - len(es.lstrip("\n"))])    # the same leading blank lines


@pytest.mark.parametrize("name", NAMES)
def test_every_prompt_has_a_file_in_both_languages(name):
    for language in ("en", "es"):
        assert (prompts.PROMPTS_DIR / f"{name}.{language}.md").is_file()


def test_the_spanish_system_prompt_translates_the_english_one_with_the_same_structure():
    en, es = prompts.load_prompt("system_prompt", "en"), prompts.load_prompt("system_prompt", "es")
    assert es != en
    def headings(text):
        return [line.split()[0] for line in text.splitlines() if line.startswith("#")]

    assert headings(es) == headings(en)
    assert len(es.splitlines()) == len(en.splitlines())
    for fixed in ("read_wiki_page", "search_wiki_fts", "search_source_chunks", "wiki/index.md",
                  "wiki/summaries/cinderella.md", "(Cinderella.pdf, p. 3)", "💾 Save to wiki"):
        assert es.count(fixed) == en.count(fixed), fixed
    assert re.search(r"\b(respondé|citá|buscá|llamá)\b", es, re.IGNORECASE)


@pytest.mark.parametrize("name,placeholders", [("inject_template", {"context", "question"}),
                                               ("refusal_directive", {"sentence"})])
@pytest.mark.parametrize("language", ("en", "es"))
def test_the_templates_keep_their_placeholders(name, placeholders, language):
    assert set(re.findall(r"{(\w+)}", prompts.load_prompt(name, language))) == placeholders


# ── load_prompt ──────────────────────────────────────────────────────────────

@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(prompts, "PROMPTS_DIR", tmp_path)
    return tmp_path


def test_load_prompt_reads_the_file_of_the_language_without_changing_it(folder):
    (folder / "x.en.md").write_text("English\n", encoding="utf-8")
    (folder / "x.es.md").write_text("Español\n\n", encoding="utf-8")
    assert prompts.load_prompt("x", "es") == "Español\n\n"
    assert prompts.load_prompt("x", "en") == "English\n"


def test_a_language_without_the_file_falls_back_to_english(folder):
    (folder / "x.en.md").write_text("English", encoding="utf-8")
    assert prompts.load_prompt("x", "es") == "English"
    assert prompts.load_prompt("x", "fr") == "English"
    assert prompts.load_prompt("x", None) == "English"


def test_an_empty_english_file_is_an_empty_prompt(folder):
    (folder / "x.en.md").write_text("", encoding="utf-8")
    assert prompts.load_prompt("x", "en") == ""


def test_a_missing_english_file_is_an_error(folder):
    (folder / "x.es.md").write_text("Español", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="x.en.md"):
        prompts.load_prompt("x", "es")


def test_the_folder_is_found_from_the_module():
    assert prompts.PROMPTS_DIR.is_dir() and prompts.PROMPTS_DIR.name == "prompts"


# ── a Spanish wiki ───────────────────────────────────────────────────────────

def _wiki(tmp_path, config=None):
    wiki = open_wiki(tmp_path / "w", language="es")
    if config is not None:
        (wiki.path / "wiki_config.toml").write_text(config, encoding="utf-8")
    return wiki


def test_a_spanish_wiki_without_a_prompt_gets_the_spanish_default_and_the_directives(tmp_path):
    config = load_config(_wiki(tmp_path).path)
    assert config.system_prompt == prompts.load_prompt("system_prompt", "es").strip()
    agent = create_agent("http://localhost:1/v1", "k", "m", system_prompt=config.system_prompt,
                         language=config.language)
    (effective,) = agent._system_prompts
    assert effective == apply_chat_directive(config.system_prompt, "es")
    assert get_locale("es").chat_directive == prompts.load_prompt("language_directive", "es")
    assert prompts.load_prompt("language_directive", "es") in effective
    assert "No encontré la respuesta en el texto que recibí de la wiki." in effective


def test_an_english_wiki_without_a_prompt_gets_the_english_default(tmp_path):
    config = load_config(open_wiki(tmp_path / "e").path)
    assert config.system_prompt == OLD["system_prompt"]["en"]


def test_the_prompt_of_the_wiki_still_wins(tmp_path):
    wiki = _wiki(tmp_path, '[wiki]\nlanguage = "es"\n\n[assistant]\nsystem_prompt = "Mi prompt."\n')
    assert load_config(wiki.path).system_prompt == "Mi prompt."


def test_create_agent_without_a_prompt_reads_the_default_of_the_language():
    agent = create_agent("http://localhost:1/v1", "k", "m", language="es")
    (effective,) = agent._system_prompts
    assert effective == apply_chat_directive(prompts.load_prompt("system_prompt", "es"), "es")


# ── the language at creation ─────────────────────────────────────────────────

def test_creating_a_wiki_with_a_language_writes_only_that_language(tmp_path):
    wiki = open_wiki(tmp_path / "nueva", language="es")
    text = (wiki.path / "wiki_config.toml").read_text(encoding="utf-8")
    assert tomllib.loads(text) == {"wiki": {"language": "es"}}
    assert wiki.language == "es"


def test_creating_a_wiki_without_a_language_writes_no_file(tmp_path):
    wiki = open_wiki(tmp_path / "nueva")
    assert not (wiki.path / "wiki_config.toml").exists() and wiki.language == "en"


def test_opening_an_existing_wiki_writes_nothing(tmp_path):
    root = tmp_path / "vieja"
    open_wiki(root)
    wiki = open_wiki(root, language="es")
    assert not (root / "wiki_config.toml").exists() and wiki.language == "en"


def test_opening_an_existing_wiki_leaves_its_file_untouched(tmp_path):
    root = tmp_path / "vieja"
    open_wiki(root, language="en")
    (root / "wiki_config.toml").write_text('# mine\n[wiki]\nlanguage = "en"\n', encoding="utf-8")
    open_wiki(root, language="es")
    assert (root / "wiki_config.toml").read_text(encoding="utf-8") == '# mine\n[wiki]\nlanguage = "en"\n'
