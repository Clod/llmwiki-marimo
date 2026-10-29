"""The expression lists of the two intent predicates of the pre-retrieval.

`scope.collection_intent` (node Q9) and `scope.advisory_intent` (node Q5) look for
expressions in the question. The lists live in the `[intent]` section of
`wiki_config.toml`, keys `collection` and `advisory`. A key that the section does
not have takes the base list:

  - `collection`: `intent.<language>.toml`, next to this module;
  - `advisory`: the `intent.<language>.toml` of each active domain
    (`domain/extensions/<domain>/`).

A key the section has replaces its base list completely. The Vocabulary tab writes
the whole base list into the section on the first edit of that list
(`config_writer.add_intent_cue`). The advisory shapes (money amount, time horizon)
always come from the active domains: a wiki without an advisory domain has none,
and `advisory_intent` is then always False.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

SECTION = "intent"
KEYS = ("collection", "advisory")
_BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class IntentLists:
    language: str = "en"            # the language of the wiki: it selects the patterns
    collection: tuple[str, ...] = ()
    advisory: tuple[str, ...] = ()
    advisory_shapes: tuple = ()
    # True when the `[intent]` section of the wiki has the key; False when the list
    # is the base list.
    written: dict[str, bool] = field(default_factory=dict)


def base_collection(language: str) -> tuple[str, ...]:
    """The base collection list of `language`; empty when the language has no file."""
    path = _BASE_DIR / f"intent.{language}.toml"
    if not path.is_file():
        return ()
    return tuple(str(c) for c in tomllib.loads(path.read_text(encoding="utf-8")).get("collection", []))


def base_lists(workspace: Path, language: str) -> dict[str, tuple[str, ...]]:
    """The base list of each key, for `workspace` in `language`."""
    from domain.extensions import advisory_base

    return {"collection": base_collection(language), "advisory": advisory_base(workspace, language)[0]}


def load_intent(workspace: Path, language: str, section: dict) -> IntentLists:
    """The lists the predicates use: the keys of `section` (the `[intent]` table of
    `wiki_config.toml`, or {}), the base lists for the keys it lacks."""
    from domain.extensions import advisory_base

    advisory_cues, shapes = advisory_base(workspace, language)
    base = {"collection": base_collection(language), "advisory": advisory_cues}
    lists = {key: tuple(str(c) for c in section[key]) if key in section else base[key] for key in KEYS}
    return IntentLists(
        language=language, collection=lists["collection"], advisory=lists["advisory"], advisory_shapes=shapes,
        written={key: key in section for key in KEYS},
    )
