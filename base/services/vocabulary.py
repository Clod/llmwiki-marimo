"""The vocabulary of a wiki, as the Vocabulario screen shows and edits it.

The roster ("padrón") is what the pre-retrieval gate counts as covered: the
dataset categories and keys (`datasets/`) and the titles of the ready concept
pages. It is computed, never stored, so it is read only here.

The aliases are the effective map of `load_config` (generated ⊕ hand-written −
rejected), each alias marked with its origin. The rejected aliases
(`[falsos_sinonimos]`) and the blacklist (`[fuera_de_alcance]`) are the lists of
`wiki_config.toml`. The findings are those of `vocabulary_check`.

Every change goes through `domain.chat.config_writer`, which keeps the file as
written, and is one commit of the wiki: `wiki_config.toml` is part of a point of
the history, so going back to a point restores the lists too. The caller holds
the wiki's lock.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from domain.chat import config_writer
from domain.chat.config import load_config
from domain.chat.config_writer import VocabularyEditError
from domain.chat.vocabulary import normalize
from domain.datasets.source import LocalMarkdownSource
from domain.lint.checks import vocabulary_check
from domain.tools.db import get_connection

from services.wiki import Wiki

__all__ = ["VocabularyEditError", "Vocabulary", "read_vocabulary", "add_blacklist_term", "remove_blacklist_term",
           "add_alias", "remove_alias", "reject_alias", "unreject_alias"]


@dataclass(frozen=True)
class RosterEntry:
    name: str
    origin: str          # "category" | "key" | "concept"
    page: str = ""       # with origin "concept": the page, e.g. "concepts/plazo-fijo-uva"


@dataclass(frozen=True)
class AliasEntry:
    alias: str
    origin: str          # "generated" | "hand"


@dataclass(frozen=True)
class CanonicalAliases:
    canonical: str
    aliases: tuple[AliasEntry, ...]


@dataclass(frozen=True)
class Finding:
    check: str           # "vocab_collision" | "vocab_stale" | "vocab_ambiguous" | "vocab_covered"
    severity: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class Vocabulary:
    roster: tuple[RosterEntry, ...]
    aliases: tuple[CanonicalAliases, ...]
    rejected: tuple[tuple[str, str], ...]     # (canonical, alias)
    blacklist: tuple[str, ...]
    findings: tuple[Finding, ...]


# ── reading ───────────────────────────────────────────────────────────────────

def _sections(wiki: Wiki) -> dict:
    path = wiki.path / config_writer.CONFIG_FILE
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def roster(wiki: Wiki) -> tuple[RosterEntry, ...]:
    """The covered names, one entry per normalized name, sorted without case nor accents."""
    entries: dict[str, RosterEntry] = {}
    source = LocalMarkdownSource(Path(wiki.path) / "datasets")
    for category in source.categories():
        entries.setdefault(normalize(category), RosterEntry(category, "category"))
    with get_connection(wiki.db_path) as conn:
        rows = conn.execute(
            "SELECT title, relative_path FROM documents "
            "WHERE source_kind='wiki' AND path='/wiki/concepts/' AND status='ready'").fetchall()
    for row in rows:
        if row["title"]:
            page = row["relative_path"].removeprefix("wiki/").removesuffix(".md")
            entries.setdefault(normalize(row["title"]), RosterEntry(row["title"], "concept", page))
    for category in source.categories():
        for row in source.query(category):
            entries.setdefault(normalize(row.clave), RosterEntry(row.clave, "key"))
    return tuple(sorted(entries.values(), key=lambda e: normalize(e.name)))


def _aliases(wiki: Wiki, sections: dict) -> tuple[CanonicalAliases, ...]:
    hand = {normalize(c): {normalize(a) for a in aliases if isinstance(a, str)}
            for c, aliases in sections.get(config_writer.ALIASES_SECTION, {}).items()}
    effective = load_config(wiki.path).data_aliases
    result = []
    for canonical in sorted(effective, key=normalize):
        own = hand.get(normalize(canonical), set())
        result.append(CanonicalAliases(canonical, tuple(
            AliasEntry(alias, "hand" if normalize(alias) in own else "generated")
            for alias in effective[canonical])))
    return tuple(result)


def read_vocabulary(wiki: Wiki) -> Vocabulary:
    sections = _sections(wiki)
    rejected = tuple(
        (str(canonical), alias)
        for canonical, aliases in sections.get(config_writer.REJECTED_SECTION, {}).items()
        for alias in aliases if isinstance(alias, str))
    findings = tuple(Finding(i.check, i.severity, i.terms) for i in vocabulary_check(wiki.db_path, wiki.path))
    return Vocabulary(roster=roster(wiki), aliases=_aliases(wiki, sections), rejected=rejected,
                      blacklist=tuple(load_config(wiki.path).off_limits), findings=findings)


# ── changes ───────────────────────────────────────────────────────────────────

def _covered(wiki: Wiki) -> set[str]:
    return {normalize(e.name) for e in roster(wiki)}


def _commit(wiki: Wiki, message: str) -> None:
    from domain.tools.git_ops import auto_commit, init_wiki_repo

    init_wiki_repo(wiki.path)
    auto_commit(wiki.path, message)


def add_blacklist_term(wiki: Wiki, term: str) -> str:
    """Add a term the wiki does not cover. Refused ("covered") when it is in the roster."""
    if normalize(term) in _covered(wiki):
        raise VocabularyEditError("covered", term.strip())
    written = config_writer.add_blacklist_term(wiki.path, term)
    _commit(wiki, f'vocabulary: blacklist "{written}"')
    return written


def remove_blacklist_term(wiki: Wiki, term: str) -> None:
    config_writer.remove_blacklist_term(wiki.path, term)
    _commit(wiki, f'vocabulary: remove "{term.strip()}" from the blacklist')


def add_alias(wiki: Wiki, canonical: str, alias: str) -> None:
    """Add a hand-written alias. The canonical must be in the roster ("unknown_canonical");
    the alias must not be the name of another covered thing ("collision")."""
    names = {normalize(e.name): e.name for e in roster(wiki)}
    if normalize(canonical) not in names:
        raise VocabularyEditError("unknown_canonical", canonical.strip())
    if normalize(alias) in names and normalize(alias) != normalize(canonical):
        raise VocabularyEditError("collision", alias.strip())
    canonical = names[normalize(canonical)]          # the roster's spelling, not the typed one
    config_writer.add_alias(wiki.path, canonical, alias)
    _commit(wiki, f'vocabulary: alias "{alias.strip()}" of "{canonical.strip()}"')


def remove_alias(wiki: Wiki, canonical: str, alias: str) -> None:
    config_writer.remove_alias(wiki.path, canonical, alias)
    _commit(wiki, f'vocabulary: remove alias "{alias.strip()}" of "{canonical.strip()}"')


def reject_alias(wiki: Wiki, canonical: str, alias: str) -> None:
    """Remove an alias for good: it goes to the rejected aliases, and a hand-written
    copy of it, if any, is removed too, so the alias leaves the effective map."""
    try:
        config_writer.remove_alias(wiki.path, canonical, alias)
    except VocabularyEditError:
        pass                                   # it was a generated alias only
    config_writer.reject_alias(wiki.path, canonical, alias)
    _commit(wiki, f'vocabulary: reject alias "{alias.strip()}" of "{canonical.strip()}"')


def unreject_alias(wiki: Wiki, canonical: str, alias: str) -> None:
    config_writer.unreject_alias(wiki.path, canonical, alias)
    _commit(wiki, f'vocabulary: accept alias "{alias.strip()}" of "{canonical.strip()}" again')


# ── datasets behind roster names ──────────────────────────────────────────────

@dataclass(frozen=True)
class DatasetTable:
    """One dataset as the screen shows it: one row per key, one column per
    (metric, dimensions) combination, whatever the on-disk format."""

    category: str
    path: str                                          # the file, relative to the wiki: "datasets/plazo_fijo.md"
    metrics: tuple[str, ...]
    unit: str
    as_of: str
    source: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, tuple[str, ...]], ...]    # (key, cells), cells aligned with columns
    marked: str = ""                                   # the key of the row to mark, as written


def _number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") if value == value else ""   # NaN → ""


def _table(source: LocalMarkdownSource, category: str, marked: str = "") -> DatasetTable:
    rows = source.query(category)
    metrics = tuple(dict.fromkeys(r.metrica for r in rows))
    columns: dict[tuple, str] = {}
    for r in rows:
        combo = (r.metrica, tuple(sorted(r.dims.items())))
        if combo not in columns:
            dims = " · ".join(v for _, v in combo[1])
            label = " · ".join(p for p in ((r.metrica if len(metrics) > 1 else ""), dims) if p)
            columns[combo] = label or r.metrica
    cells: dict[str, dict[tuple, str]] = {}
    for r in rows:
        cells.setdefault(r.clave, {})[(r.metrica, tuple(sorted(r.dims.items())))] = _number(r.valor)
    join = lambda values: " · ".join(dict.fromkeys(str(v) for v in values if v))  # noqa: E731
    return DatasetTable(
        category=category, path=f"datasets/{category}.md", metrics=metrics, unit=join(r.unidad for r in rows),
        as_of=join(r.as_of.isoformat() for r in rows), source=join(r.fuente for r in rows),
        columns=tuple(columns.values()),
        rows=tuple((key, tuple(by_combo.get(combo, "") for combo in columns)) for key, by_combo in cells.items()),
        marked=marked)


def dataset_tables(wiki: Wiki, *, category: str = "", key: str = "") -> tuple[DatasetTable, ...]:
    """The dataset of `category`, or every dataset that holds `key` with that key's row
    marked. Empty when the category or the key does not exist."""
    source = LocalMarkdownSource(Path(wiki.path) / "datasets")
    if category:
        return (_table(source, category),) if category in source.categories() else ()
    wanted = normalize(key)
    tables = []
    for name in source.categories():
        hit = next((r.clave for r in source.query(name) if normalize(r.clave) == wanted), None)
        if wanted and hit is not None:
            tables.append(_table(source, name, marked=hit))
    return tuple(tables)
