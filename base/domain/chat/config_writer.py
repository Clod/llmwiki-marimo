"""Edits of the vocabulary lists of `wiki_config.toml`, keeping the file as written.

The three lists the pre-retrieval gate reads by hand (`load_config`):

  - `[fuera_de_alcance] terminos` — the blacklist;
  - `[alias_datos]` — the hand-written aliases, `canonical = [alias, …]`;
  - `[falsos_sinonimos]` — the rejected aliases, `canonical = [alias, …]`: the
    aliases a canonical must never have. `merge_aliases` removes them from the
    effective map, and ingestion does not write them into the generated artifact.

`tomlkit` rewrites only what changes: the comments, the order of the sections and
the spelling of the other entries stay byte-identical. Terms are compared with
`vocabulary.normalize` (case, accents, `_`), so "Dólar" and "dolar" are one term;
an existing canonical keeps its spelling. A missing file or section is created.

Pure file I/O, no git: the caller commits the change.
"""

from __future__ import annotations

from pathlib import Path

import tomlkit
from tomlkit.items import Array, Table

from domain.chat.vocabulary import normalize

CONFIG_FILE = "wiki_config.toml"
BLACKLIST_SECTION, BLACKLIST_KEY = "fuera_de_alcance", "terminos"
ALIASES_SECTION = "alias_datos"
REJECTED_SECTION = "falsos_sinonimos"


class VocabularyEditError(Exception):
    """A change refused before the file is written.

    `code` is one of "empty", "duplicate", "missing"; `term` is the term refused.
    The web layer turns the code into a message in the interface language.
    """

    def __init__(self, code: str, term: str) -> None:
        super().__init__(f"{code}: {term!r}")
        self.code = code
        self.term = term


def _load(wiki_path: Path) -> tomlkit.TOMLDocument:
    path = Path(wiki_path) / CONFIG_FILE
    return tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()


def _save(wiki_path: Path, doc: tomlkit.TOMLDocument) -> None:
    path = Path(wiki_path) / CONFIG_FILE
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(doc.as_string(), encoding="utf-8")
    tmp.replace(path)


def _clean(term: str) -> str:
    text = " ".join((term or "").split())
    if not normalize(text):
        raise VocabularyEditError("empty", term or "")
    return text


def _table(doc: tomlkit.TOMLDocument, name: str) -> Table:
    if name not in doc:
        doc.add(tomlkit.nl())
        doc.add(name, tomlkit.table())
    return doc[name]


def _key_of(table: Table, canonical: str) -> str | None:
    """The key of `table` that is the same canonical as `canonical`, in its own spelling."""
    wanted = normalize(canonical)
    return next((key for key in table if normalize(key) == wanted), None)


def _index_of(array: Array, term: str) -> int | None:
    wanted = normalize(term)
    return next((i for i, item in enumerate(array) if normalize(str(item)) == wanted), None)


def _add_to_list(table: Table, key: str, term: str) -> None:
    array = table.get(key)
    if array is None:
        table[key] = tomlkit.array([term])
        return
    if _index_of(array, term) is not None:
        raise VocabularyEditError("duplicate", term)
    array.append(term)


def _remove_from_list(table: Table, key: str, term: str, *, drop_empty: bool) -> None:
    array = table.get(key)
    index = None if array is None else _index_of(array, term)
    if index is None:
        raise VocabularyEditError("missing", term)
    del array[index]
    if drop_empty and len(array) == 0:
        del table[key]


def _add_pair(wiki_path: Path, section: str, canonical: str, alias: str) -> None:
    canonical, alias = _clean(canonical), _clean(alias)
    doc = _load(wiki_path)
    table = _table(doc, section)
    _add_to_list(table, _key_of(table, canonical) or canonical, alias)
    _save(wiki_path, doc)


def _remove_pair(wiki_path: Path, section: str, canonical: str, alias: str) -> None:
    canonical, alias = _clean(canonical), _clean(alias)
    doc = _load(wiki_path)
    table = doc.get(section)
    key = None if table is None else _key_of(table, canonical)
    if key is None:
        raise VocabularyEditError("missing", alias)
    _remove_from_list(table, key, alias, drop_empty=True)
    _save(wiki_path, doc)


# ── public operations ─────────────────────────────────────────────────────────

def add_blacklist_term(wiki_path: Path, term: str) -> str:
    """Add `term` to `[fuera_de_alcance] terminos`; return the term as written."""
    term = _clean(term)
    doc = _load(wiki_path)
    _add_to_list(_table(doc, BLACKLIST_SECTION), BLACKLIST_KEY, term)
    _save(wiki_path, doc)
    return term


def remove_blacklist_term(wiki_path: Path, term: str) -> None:
    """Remove `term` from the blacklist; the empty list stays."""
    term = _clean(term)
    doc = _load(wiki_path)
    table = doc.get(BLACKLIST_SECTION)
    if table is None:
        raise VocabularyEditError("missing", term)
    _remove_from_list(table, BLACKLIST_KEY, term, drop_empty=False)
    _save(wiki_path, doc)


def add_alias(wiki_path: Path, canonical: str, alias: str) -> None:
    """Add a hand-written alias of `canonical` to `[alias_datos]`."""
    _add_pair(wiki_path, ALIASES_SECTION, canonical, alias)


def remove_alias(wiki_path: Path, canonical: str, alias: str) -> None:
    """Remove a hand-written alias; a canonical left with no alias is removed."""
    _remove_pair(wiki_path, ALIASES_SECTION, canonical, alias)


def reject_alias(wiki_path: Path, canonical: str, alias: str) -> None:
    """Record that `alias` is not an alias of `canonical` (`[falsos_sinonimos]`)."""
    _add_pair(wiki_path, REJECTED_SECTION, canonical, alias)


def unreject_alias(wiki_path: Path, canonical: str, alias: str) -> None:
    """Remove a rejected alias: the next ingestion may propose it again."""
    _remove_pair(wiki_path, REJECTED_SECTION, canonical, alias)
