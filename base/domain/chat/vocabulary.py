"""Shared vocabulary primitives for the ingest-time alias generation (Piece 2).

One place for the logic that three callers must agree on:
  - the ingest-time generator (builds the alias map, drops bad proposals),
  - the linter (flags collisions / drift in the committed map),
  - the pre-retrieval gate (matches a question against known coverage).

The core is pure — no LLM. The only I/O is `read_generated_aliases` /
`write_generated_aliases`, which own the per-wiki generated alias artifact
(`.llmwiki/aliases.generated.toml`) that ingestion writes and `load_config`
merges under the hand-written overrides. Normalization is reused from `scope`
so the generator, the linter, and the gate compare terms identically.

  - `build_roster` — the "padrón": the normalized set of canonical names the
    wiki actually covers (dataset categories/keys + concept page names). Only
    canonical names belong here, never aliases.
  - `validate_aliases` — normalize, dedupe, and drop collisions from proposed
    aliases. A proposal that is really the canonical name of a DIFFERENT covered
    thing (CEDEAR ← "acciones") is dropped and reported as a `Collision`, so the
    generator can seed a false-synonym and the linter can flag it.
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
class Collision:
    """An alias proposal that is really the canonical name of something else."""

    canonical: str       # the concept the alias was proposed for
    alias: str           # the offending alias, as proposed
    collides_with: str   # the other covered canonical it normalizes to


@dataclass(frozen=True)
class ValidatedVocabulary:
    """Result of validating proposed aliases against the roster."""

    aliases: dict[str, list[str]] = field(default_factory=dict)
    collisions: tuple[Collision, ...] = ()


def validate_aliases(
    proposals: Mapping[str, Iterable[str]],
    roster: Iterable[str],
) -> ValidatedVocabulary:
    """Clean proposed aliases and separate out the collisions.

    For each canonical → its proposed aliases, an alias is KEPT (as proposed,
    stripped) unless it: is blank or not a string; repeats its own canonical or
    an earlier alias of the same canonical (deduped by normalized form); or is
    the canonical name of a DIFFERENT covered thing — that last case is dropped
    and recorded as a `Collision` (the CEDEAR ← "acciones" bug), never silently
    kept. A canonical whose aliases are all removed drops out of the result.
    """
    roster_by_norm = {n: t for t in roster if (n := normalize(t))}
    clean: dict[str, list[str]] = {}
    collisions: list[Collision] = []

    for canonical, proposed in proposals.items():
        own = normalize(canonical)
        kept: list[str] = []
        seen: set[str] = set()
        for alias in proposed:
            if not isinstance(alias, str):
                continue
            na = normalize(alias)
            if not na or na == own or na in seen:
                continue
            if na in roster_by_norm:
                collisions.append(
                    Collision(canonical=canonical, alias=alias, collides_with=roster_by_norm[na])
                )
                continue
            seen.add(na)
            kept.append(alias.strip())
        if kept:
            clean[canonical] = kept

    return ValidatedVocabulary(aliases=clean, collisions=tuple(collisions))


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
