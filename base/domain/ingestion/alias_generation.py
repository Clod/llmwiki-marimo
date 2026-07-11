"""Ingest-time generation of the alias artifact (`.llmwiki/aliases.generated.toml`).

Each ingested document's concepts contribute their canonical name + aliases. The
aliases are validated against the wiki's coverage — the "padrón" = dataset
vocabulary + every concept name — so a proposal that is really another covered
thing's name (CEDEAR ← "acciones") is dropped as a collision. The accumulated map
is rewritten to the per-wiki artifact, which `load_config` merges under the
hand-written overrides.

Concept pages store only names, so the artifact is the store of aliases: this is
an incremental accumulation (read existing → add the new concepts → re-validate →
rewrite), which keeps a re-ingest consistent instead of double-adding.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from domain.chat.vocabulary import (
    ValidatedVocabulary,
    build_roster,
    read_generated_aliases,
    validate_aliases,
    write_generated_aliases,
)
from domain.datasets.source import LocalMarkdownSource


def _dataset_vocabulary(workspace: Path) -> set[str]:
    """Dataset categories + keys for this wiki; empty if it has no `datasets/`."""
    source = LocalMarkdownSource(Path(workspace) / "datasets")
    vocab: set[str] = set()
    for categoria in source.categories():
        vocab.add(categoria)
        for row in source.query(categoria):
            vocab.add(row.clave)
    return vocab


def update_generated_aliases(
    workspace: Path | str,
    concept_names: Iterable[str],
    new_concepts: Iterable[tuple[str, Iterable[str]]],
) -> ValidatedVocabulary:
    """Merge the just-ingested concepts' aliases into the generated artifact.

    Args:
        workspace: the wiki folder (holds `datasets/` and `.llmwiki/`).
        concept_names: every concept page name currently known (roster source).
        new_concepts: (canonical_name, aliases) for the just-ingested concepts.

    Returns the validated result so the caller can surface any dropped collisions.
    """
    workspace = Path(workspace)
    proposals: dict[str, list[str]] = {
        canonical: list(aliases) for canonical, aliases in read_generated_aliases(workspace).items()
    }
    new_names: list[str] = []
    for name, aliases in new_concepts:
        new_names.append(name)
        proposals.setdefault(name, []).extend(a for a in aliases if isinstance(a, str))

    roster = build_roster(_dataset_vocabulary(workspace), concept_names, new_names)
    validated = validate_aliases(proposals, roster)
    write_generated_aliases(workspace, validated.aliases)
    return validated
