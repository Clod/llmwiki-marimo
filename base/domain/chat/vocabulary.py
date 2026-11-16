"""Shared vocabulary primitives for the ingest-time alias generation (Piece 2).

One place for the logic that three callers must agree on:
  - the ingest-time generator (builds the alias map, cleans the proposals),
  - the linter (flags drift in the committed map),
  - the pre-retrieval gate (matches a question against known coverage).

The core is pure — no LLM. The only I/O is `read_generated_aliases` /
`write_generated_aliases`, which own the per-wiki generated alias artifact
(`.llmwiki/aliases.generated.toml`) that ingestion writes and `load_config`
merges under the hand-written overrides. Normalization is reused from `scope`
so the generator, the linter, and the gate compare terms identically.

  - `build_roster` — the "padrón": the normalized set of canonical names the
    wiki actually covers (dataset categories/keys + concept page names). Only
    canonical names belong here, never aliases.
  - `validate_aliases` — normalize and dedupe proposed aliases. It never judges
    whether an alias is false: an alias that is also the name of another covered
    thing may be right, as two pages can treat the same thing ("Riesgo de
    inflación" and "Riesgo inflacionario"). A curator reviews the new aliases the
    ingestion reports, and lists a false one in `[falsos_sinonimos]`.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from domain.chat.scope import _normalize

# Per-wiki artifact the ingest-time generator writes and load_config merges.
GENERATED_ALIASES_REL = ".llmwiki/aliases.generated.toml"

# Re-export the single normalization used across scope, the gate, and here, so
# every layer folds case, accents, and `_` the same way.
normalize = _normalize


def _dedupe_normalized(items: Iterable[str]) -> list[str]:
    """Keep the first spelling of each distinct (normalized) non-blank string."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        n = normalize(item)
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(item.strip())
    return out


def build_roster(*sources: Iterable[str]) -> frozenset[str]:
    """The padrón: the normalized canonical names the wiki covers.

    Pass any number of term sources (dataset vocabulary, concept page names…);
    each term is normalized and blanks are dropped.
    """
    roster: set[str] = set()
    for source in sources:
        for term in source:
            n = normalize(term)
            if n:
                roster.add(n)
    return frozenset(roster)


@dataclass(frozen=True)
class ValidatedVocabulary:
    """Result of validating proposed aliases: the clean map, and the (canonical,
    alias) pairs the generated artifact did not hold before, for a curator to review."""

    aliases: dict[str, list[str]] = field(default_factory=dict)
    new: tuple[tuple[str, str], ...] = ()


class RosterKey:
    """How a term is compared against the roster: the gate's comparison.

    Without a language, every term compares by its normalized form. With a
    language, categories, concept-page titles and aliases compare by Snowball
    stem, and the terms in `exact` (the dataset keys) by normalized form
    (design_stemming.md, decisions 7 and 15).
    """

    def __init__(self, language: str | None = None, exact: Iterable[str] = ()) -> None:
        self.language = language
        self.exact = {n for t in exact if (n := normalize(t))}

    def of_roster_term(self, term: str) -> str:
        n = normalize(term)
        if self.language is None or n in self.exact:
            return n
        return self._stem(term)

    def of_alias(self, alias: str) -> set[str]:
        """The keys an alias can collide on: its normalized form and its stem."""
        n = normalize(alias)
        if self.language is None:
            return {n} if n else set()
        return {k for k in (n if n in self.exact else "", self._stem(alias)) if k}

    def _stem(self, term: str) -> str:
        from domain.text.stemming import stem_text

        return stem_text(term, self.language)


def validate_aliases(
    proposals: Mapping[str, Iterable[str]],
    key: RosterKey | None = None,
) -> ValidatedVocabulary:
    """Clean proposed aliases.

    For each canonical → its proposed aliases, an alias is KEPT (as proposed,
    stripped) unless it is blank or not a string, or repeats its own canonical or
    an earlier alias of the same canonical (deduped by normalized form). A
    canonical whose aliases are all removed drops out of the result.
    """
    key = key or RosterKey()
    clean: dict[str, list[str]] = {}

    for canonical, proposed in proposals.items():
        own = key.of_alias(canonical) | {key.of_roster_term(canonical)}
        kept: list[str] = []
        seen: set[str] = set()
        for alias in proposed:
            if not isinstance(alias, str):
                continue
            na = normalize(alias)
            keys = key.of_alias(alias)
            if not na or keys & own or na in seen:
                continue
            seen.add(na)
            kept.append(alias.strip())
        if kept:
            clean[canonical] = kept

    return ValidatedVocabulary(aliases=clean)


def merge_aliases(
    generated: Mapping[str, Iterable[str]],
    overrides: Mapping[str, Iterable[str]],
    false_synonyms: Mapping[str, Iterable[str]],
) -> dict[str, list[str]]:
    """Compose the effective alias map: generated ⊕ overrides − false_synonyms.

    Canonicals are matched by normalized form, so a generated "Dólar" and a
    hand-written "dolar" are the same entry; the override's spelling of the key
    wins (a human pinned it), and its aliases are unioned onto the generated
    ones. Finally the false-synonym lists act as a delete filter: any alias a
    canonical must never have (`cedear` ↛ "acciones") is removed. A canonical
    left with no aliases drops out.
    """
    by_norm: dict[str, dict] = {}
    for source, is_override in ((generated, False), (overrides, True)):
        for canonical, aliases in source.items():
            key = normalize(canonical)
            if not key:
                continue
            slot = by_norm.setdefault(key, {"label": canonical, "aliases": []})
            if is_override:
                slot["label"] = canonical  # human-pinned spelling wins
            slot["aliases"].extend(a for a in aliases if isinstance(a, str))

    forbidden = {
        key: {normalize(bad) for bad in targets if isinstance(bad, str)}
        for term, targets in false_synonyms.items()
        if (key := normalize(term))
    }

    result: dict[str, list[str]] = {}
    for key, slot in by_norm.items():
        block = forbidden.get(key, set())
        merged = [a for a in _dedupe_normalized(slot["aliases"]) if normalize(a) not in block]
        if merged:
            result[slot["label"]] = merged
    return result


# ── generated alias artifact (the only I/O) ───────────────────────────────────

def _toml_basic_string(s: str) -> str:
    """Serialize a string as a TOML basic string (escape backslash, quote, control)."""
    escaped = (
        s.replace("\\", "\\\\").replace('"', '\\"')
        .replace("\n", " ").replace("\r", " ").replace("\t", " ")
    )
    return f'"{escaped}"'


def read_generated_aliases(workspace: Path | str) -> dict[str, list[str]]:
    """Read the generated alias map from a wiki's artifact; {} if absent/malformed."""
    path = Path(workspace) / GENERATED_ALIASES_REL
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        return {}
    return {
        str(canonical): [str(a) for a in aliases]
        for canonical, aliases in data.get("alias_datos", {}).items()
    }


def write_generated_aliases(workspace: Path | str, alias_map: Mapping[str, Iterable[str]]) -> None:
    """Write the alias map to the wiki's generated artifact (creating `.llmwiki/`)."""
    path = Path(workspace) / GENERATED_ALIASES_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Generated at ingest — do not edit by hand.",
        "# Hand overrides live in wiki_config.toml [alias_datos]; this file is merged UNDER them.",
        "",
        "[alias_datos]",
    ]
    for canonical in sorted(alias_map):
        vals = ", ".join(_toml_basic_string(a) for a in alias_map[canonical])
        lines.append(f"{_toml_basic_string(canonical)} = [{vals}]")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
