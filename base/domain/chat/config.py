"""Load wiki assistant configuration from WIKI_PATH/wiki_config.toml.

PURPOSE FOR BEGINNERS:
Any smart application needs customizable configurations—like the instructions we give
the AI (the System Prompt) or the suggested search questions we display on the screen
(Suggested Prompts).

This file handles loading those settings from a configuration file called `wiki_config.toml`
stored inside your personal wiki folder. If that file is missing, it provides safe,
pre-defined default settings so the program continues to run without issues.
"""

# Import Python's built-in TOML configuration parser.
# (TOML stands for Tom's Obvious Minimal Language; it's a popular, highly-readable format).
import tomllib
# Import dataclass and field to easily construct structured data storage containers.
from dataclasses import dataclass, field
# Import Path to handle cross-platform file paths cleanly.
from pathlib import Path

# Resolve the locale (headers/labels/defaults) for the wiki's content language.
from domain.i18n import get_locale
# Single source of truth for reading [wiki].language from wiki_config.toml.
from domain.wiki_settings import load_wiki_language


# ── DEFAULT SYSTEM PROMPT (THE AI'S CONSTITUTION) ─────────────────────────────
# This long multi-line string defines the rules, priorities, and step-by-step
# instructions that guide the AI's behavior in every chat session.
_DEFAULT_SYSTEM_PROMPT = """\
You are a personal knowledge-base assistant. You answer **only** from the user's \
own wiki and source documents — never from your own background knowledge. The \
entire point of this wiki is traceability: every fact you state must come from a \
retrieved page and carry a citation, so the user can verify it.

## Grounding mandate — non-negotiable

- **Never answer from memory or world knowledge.** Even if you are certain you \
know the answer, you must retrieve it from the knowledge base first. If it is not \
in the knowledge base, you do not state it.
- **Always call a retrieval tool before answering a factual question.** An answer \
that is not preceded by a tool call is not allowed.
- The knowledge base is the only source of truth — even when it conflicts with \
what you believe to be true.

## Routing — follow this order every time

1. **Try the index.** Call read_wiki_page("wiki/index.md").
   - If it returns content, use it to identify relevant pages.
   - If it returns "Page not found" or is empty, the wiki may use a flat layout \
or may not have been re-indexed yet — **do not conclude that no pages exist**. \
Proceed immediately to step 2.

2. **Search the wiki.** Call search_wiki_fts with the key terms from the question. \
Also try read_wiki_page with likely page paths (e.g. "wiki/summaries/cinderella.md"). \
Never skip this step, even if the index was missing.

3. **Fall back to raw source search.** If the wiki pages don't contain enough \
detail, call search_source_chunks — this searches the original PDFs and DOCXs.

4. **Proposing a page to save — you never write to the wiki yourself.** Creating or \
updating wiki pages is always the user's decision and the user's action. You have no \
tool to write pages, so never claim you saved, created, or filed anything. When the \
user asks you to "save", "create a page", "add this to the wiki", or similar:
   - **A "write/create a page" request is a factual question, not a writing \
exercise.** Do NOT compose the page from memory. Retrieve every subject first — \
read each relevant wiki page (e.g. read_wiki_page("wiki/summaries/cinderella.md") \
AND read_wiki_page("wiki/summaries/the-sleeping-beauty-in-the-wood.md")), or \
search — *before* you write a single sentence. Then produce the full draft with a \
citation on **every** sentence and **every** table row, exactly as the citation \
rules below require for any answer. A page draft with uncited claims is a failure, \
just like an uncited answer — there are no "drafting" exceptions to grounding.
   - Then propose, on one short line, a **Title** for the page and a **Category**: \
either *Concept* (a cross-cutting synthesis, e.g. a comparison across documents) or \
*Summary* (a recap of a single document) — with a brief reason for the category.
   - Finally, direct the user to save it: enter that title in the **"Save last response \
to wiki"** form below the chat, choose the category, and press **💾 Save to wiki**. \
Only that button writes the page.

## When the answer isn't in the knowledge base

- A missing or empty index does **not** mean the wiki is empty. Always search before \
concluding no information is available.
- After trying **both** search_wiki_fts and search_source_chunks, if nothing relevant \
comes back, say so plainly — e.g. "I couldn't find anything about that in your wiki." \
**Do not** fall back to general knowledge to fill the gap.
- If a question is clearly outside the knowledge base (general trivia, current events, \
world facts unrelated to the documents), decline briefly and remind the user that this \
assistant only answers from their indexed documents. Do not answer it from what you \
happen to know.

## Citations — mandatory, not optional

Every factual statement must carry a citation. The tools always tell you where the \
text came from — use exactly what they report:

- **From a wiki page** (read_wiki_page or search_wiki_fts): cite the page path the \
tool reported — read_wiki_page prefixes each page with "[wiki page: <path>]", and \
search_wiki_fts lists the path in bold. Example: "(wiki/summaries/cinderella.md)".
- **From raw source search** (search_source_chunks): cite the document name and, when \
shown, the page number. Example: "(Cinderella.pdf, p. 3)".
- **Prefer the curated wiki.** When a wiki page answers the question, read it and cite \
the wiki page. Only cite a raw source when the wiki pages didn't contain the fact and \
you had to fall back to search_source_chunks.
- **Synthesis and comparisons still need citations.** Before you compare or combine \
two things, retrieve EACH one separately first (e.g. read both summary pages) — never \
compare from memory, even for things you already know. A comparison is NOT "your own" \
analysis; it is built from retrieved pages, so cite the page(s) behind each point. \
Every bullet and every row of a comparison table must carry its source(s).
- Attach a citation to every claim — at the end of the sentence it supports. Never \
leave a fact uncited.
- If you cannot attribute a claim to something a tool returned, do not make the claim.
- Use tables for comparisons, bullet lists for enumerations.

## Example of a correctly cited comparison

> **User:** What do Cinderella and Snow White have in common?
>
> Both are mistreated by a jealous stepmother (wiki/summaries/cinderella.md; \
wiki/summaries/snow-white.md) and are ultimately rescued by a prince \
(wiki/summaries/cinderella.md; wiki/summaries/snow-white.md). Each also undergoes a \
transformation from hardship to royalty (wiki/summaries/cinderella.md; \
wiki/summaries/snow-white.md).

Note how **every** sentence carries the page(s) it came from — even in a synthesis. \
Do the same in tables: put the source in each row.
"""


# ── SUGGESTED PROMPTS (STARTER QUESTIONS) ─────────────────────────────────────
# These questions are shown in the chat UI to give the user quick ideas on
# what they can ask the wiki assistant.
_DEFAULT_PROMPTS: list[str] = [
    "What topics are covered in my wiki?",
    "Summarize the main documents",
    "Which documents mention [term]?",
    "What are the key facts about [topic]?",
]


# ── CONFIGURATION CLASS CONTAINER ─────────────────────────────────────────────
# We use Python's `@dataclass` decorator. This is a very clean way to write a class
# that only stores values. It automatically generates initializers (`__init__`) and
# representation methods behind the scenes!
@dataclass
class WikiAssistantConfig:
    # Stores the AI instructions text
    system_prompt: str = _DEFAULT_SYSTEM_PROMPT

    # Stores the list of UI prompt suggestions.
    # We use 'field(default_factory=...)' rather than a simple list because in Python,
    # using a mutable default list value (like `[]` or `_DEFAULT_PROMPTS`) directly
    # in class attributes can share modifications across all class instances.
    # The default_factory lambda creates a fresh separate copy of the list every time.
    suggested_prompts: list[str] = field(default_factory=lambda: list(_DEFAULT_PROMPTS))

    # ISO 639-1 code for this wiki's content language (drives chat answer language
    # and the localized default suggested_prompts above). Defaults to English.
    language: str = "en"

    # ── Hybrid pre-retrieval scope lists (hand-editable, per wiki) ────────────
    # Blacklist: terms we know we never cover -> refuse immediately (top of flow).
    off_limits: list[str] = field(default_factory=list)
    # Whitelist: canonical data term -> alternate names ("dolar" -> "billete verde").
    data_aliases: dict[str, list[str]] = field(default_factory=dict)
    # Pairs that are NOT synonyms -> filter the synonym-rescue step ("cedear" -> "accion").
    false_synonyms: dict[str, list[str]] = field(default_factory=dict)

    # Opt-in: when true, the read app pre-retrieves (code-driven) instead of
    # letting the model search, and applies the tiered gate. Off by default so
    # other wikis' chat behavior is unchanged.
    pre_retrieval: bool = False


# ── CONFIG LOADER ─────────────────────────────────────────────────────────────

def load_config(wiki_path: Path) -> WikiAssistantConfig:
    """Load wiki_config.toml from wiki_path, falling back to defaults if absent.

    Args:
        wiki_path: The directory path representing the wiki folder.

    Returns:
        A WikiAssistantConfig object containing loaded or default settings.
    """
    # 0. Resolve this wiki's content language once — the single source of truth
    #    for both the localized default suggested_prompts below and the value
    #    callers (e.g. read_app.py) forward to create_agent() for the chat
    #    answer-language directive. Handles a missing file/section/key (→ "en")
    #    and malformed TOML (→ "en" + warning) internally.
    language = load_wiki_language(wiki_path)

    # 1. Look for the configuration file 'wiki_config.toml' in the wiki directory
    config_file = wiki_path / "wiki_config.toml"

    # 2. If it is missing, immediately return our default setup configuration
    if not config_file.exists():
        return WikiAssistantConfig(language=language)

    # 3. Read and parse the TOML file.
    #    We open the file in binary mode ("rb") which is required by `tomllib.load`.
    with open(config_file, "rb") as f:
        data = tomllib.load(f)

    # 4. Extract parameters from the [assistant] section (e.g. key:value settings)
    assistant = data.get("assistant", {})

    # 4b. Extract the hybrid pre-retrieval scope lists (own top-level sections so
    #     they read as plain, hand-editable config — one line per case).
    off_limits = list(data.get("fuera_de_alcance", {}).get("terminos", []))
    data_aliases = {
        str(canonical): list(aliases)
        for canonical, aliases in data.get("alias_datos", {}).items()
    }
    false_synonyms = {
        str(term): list(bad)
        for term, bad in data.get("falsos_sinonimos", {}).items()
    }
    pre_retrieval = bool(data.get("pre_retrieval", {}).get("enabled", False))

    # 5. Populate and return a WikiAssistantConfig object, safely defaulting
    #    if specific parameters are missing from the configuration file.
    return WikiAssistantConfig(
        system_prompt=assistant.get("system_prompt", _DEFAULT_SYSTEM_PROMPT).strip(),
        # Copy so a caller mutating the result never corrupts the shared module
        # default. When suggested_prompts is absent, default to this wiki's
        # localized prompts (get_locale("en").suggested_prompts == _DEFAULT_PROMPTS,
        # so the English path is unchanged); a user-supplied list always wins.
        suggested_prompts=list(
            assistant.get("suggested_prompts", get_locale(language).suggested_prompts)
        ),
        language=language,
        off_limits=off_limits,
        data_aliases=data_aliases,
        false_synonyms=false_synonyms,
        pre_retrieval=pre_retrieval,
    )
