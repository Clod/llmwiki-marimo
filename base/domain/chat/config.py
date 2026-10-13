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
# The default prompts are files, one per language.
from domain.prompts import load_prompt
# Single source of truth for reading [wiki].language from wiki_config.toml.
from domain.wiki_settings import load_wiki_language
# Merge the ingest-generated alias artifact under the hand-written overrides.
from domain.chat.vocabulary import merge_aliases, read_generated_aliases


# Entailment probability a sentence needs to count as supported. Defined here,
# not in domain.verify, so that loading the config imports nothing of the verifier.
# See privado/encargos/2026-10-05_sentence_verification_informe.md.
DEFAULT_VERIFY_THRESHOLD = 0.5
# Passages kept for each sentence of a wiki page before the verifier runs
# (`[verify] candidates`). See domain/verify/preselect.py.
DEFAULT_VERIFY_CANDIDATES = 5


# ── DEFAULT SYSTEM PROMPT (THE AI'S CONSTITUTION) ─────────────────────────────
# The rules, priorities and step-by-step instructions that guide the AI's behavior in
# every chat session are a file per language: `prompts/system_prompt.<lang>.md`
# (read by `domain.prompts.load_prompt`).


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
    system_prompt: str = field(default_factory=lambda: load_prompt("system_prompt", "en"))

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
    # Tier-2 threshold: the fraction of an answer's content words that must
    # appear in the raw fragment it came from (`[pre_retrieval] min_coverage`).
    min_coverage: float = 0.2
    # Query condensation: a question that names no topic of the wiki is rewritten
    # by the model, from the conversation, into a question that stands alone, and
    # the coverage gate and the search run on the rewrite
    # (`[pre_retrieval] condense_question`). On by default.
    condense_question: bool = True
    # Sentence verification: the entailment probability a sentence needs to
    # count as supported (`[verify] threshold`). The owner calibrates it.
    verify_threshold: float = DEFAULT_VERIFY_THRESHOLD
    # Page verification: passages of the context kept for each sentence by
    # full-text ranking before the verifier runs (`[verify] candidates`).
    verify_candidates: int = DEFAULT_VERIFY_CANDIDATES


# ── CONFIG LOADER ─────────────────────────────────────────────────────────────

def _threshold(value) -> float:
    """`[verify] threshold` as a float in [0, 1]; the default when absent or invalid."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_VERIFY_THRESHOLD
    return number if 0.0 <= number <= 1.0 else DEFAULT_VERIFY_THRESHOLD


def _candidates(value) -> int:
    """`[verify] candidates` as a positive integer; the default when absent or invalid."""
    if isinstance(value, bool):
        return DEFAULT_VERIFY_CANDIDATES
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_VERIFY_CANDIDATES
    return number if number >= 1 else DEFAULT_VERIFY_CANDIDATES


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
        return WikiAssistantConfig(language=language, system_prompt=load_prompt("system_prompt", language))

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
    # Fold the ingest-generated aliases (base) under the hand-written overrides
    # (win), minus the false-synonym delete filter. Absent artifact → {} → the
    # hand config behaves exactly as before.
    data_aliases = merge_aliases(read_generated_aliases(wiki_path), data_aliases, false_synonyms)
    pre_retrieval = bool(data.get("pre_retrieval", {}).get("enabled", False))
    min_coverage = float(data.get("pre_retrieval", {}).get("min_coverage", 0.2))
    condense_question = bool(data.get("pre_retrieval", {}).get("condense_question", True))

    verify_threshold = _threshold(data.get("verify", {}).get("threshold"))
    verify_candidates = _candidates(data.get("verify", {}).get("candidates"))

    # 5. Populate and return a WikiAssistantConfig object, safely defaulting
    #    if specific parameters are missing from the configuration file.
    return WikiAssistantConfig(
        system_prompt=assistant.get("system_prompt", load_prompt("system_prompt", language)).strip(),
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
        min_coverage=min_coverage,
        condense_question=condense_question,
        verify_threshold=verify_threshold,
        verify_candidates=verify_candidates,
    )
