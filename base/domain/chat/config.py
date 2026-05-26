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


# ── DEFAULT SYSTEM PROMPT (THE AI'S CONSTITUTION) ─────────────────────────────
# This long multi-line string defines the rules, priorities, and step-by-step
# instructions that guide the AI's behavior in every chat session.
_DEFAULT_SYSTEM_PROMPT = """\
You are a wiki assistant with access to a structured knowledge base.

## Routing — follow this order every time

1. **Try the index.** Call read_wiki_page("wiki/index.md").
   - If it returns content, use it to identify relevant pages.
   - If it returns "Page not found" or is empty, the wiki may use a flat layout \
or may not have been re-indexed yet — **do not conclude that no pages exist**. \
Proceed immediately to step 2.

2. **Search the wiki.** Call search_wiki_fts with the key terms from the question. \
Also try read_wiki_page with likely page paths (e.g. "wiki/blancanieves.md", \
"wiki/summaries/blancanieves.md"). Never skip this step, even if the index was missing.

3. **Fall back to raw source search.** Only call search_source_chunks if the wiki pages \
don't contain enough detail — this searches the original PDFs and DOCXs.

4. **Save useful syntheses.** If you produce a comparison, analysis, or summary worth \
keeping, call file_to_wiki to save it as a concept page.

## Critical rules

- A missing or empty index does **not** mean the wiki is empty. Always search before \
concluding no information is available.
- Only say "I could not find information" after you have tried both search_wiki_fts \
and search_source_chunks.

## Output guidelines

- Cite sources: document name and page number for specific facts.
- Use tables for comparisons, bullet lists for enumerations.
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


# ── CONFIG LOADER ─────────────────────────────────────────────────────────────

def load_config(wiki_path: Path) -> WikiAssistantConfig:
    """Load wiki_config.toml from wiki_path, falling back to defaults if absent.

    Args:
        wiki_path: The directory path representing the wiki folder.

    Returns:
        A WikiAssistantConfig object containing loaded or default settings.
    """
    # 1. Look for the configuration file 'wiki_config.toml' in the wiki directory
    config_file = wiki_path / "wiki_config.toml"

    # 2. If it is missing, immediately return our default setup configuration
    if not config_file.exists():
        return WikiAssistantConfig()

    # 3. Read and parse the TOML file.
    #    We open the file in binary mode ("rb") which is required by `tomllib.load`.
    with open(config_file, "rb") as f:
        data = tomllib.load(f)

    # 4. Extract parameters from the [assistant] section (e.g. key:value settings)
    assistant = data.get("assistant", {})

    # 5. Populate and return a WikiAssistantConfig object, safely defaulting
    #    if specific parameters are missing from the configuration file.
    return WikiAssistantConfig(
        system_prompt=assistant.get("system_prompt", _DEFAULT_SYSTEM_PROMPT).strip(),
        suggested_prompts=assistant.get("suggested_prompts", _DEFAULT_PROMPTS),
    )
