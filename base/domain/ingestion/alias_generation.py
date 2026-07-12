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

import hashlib
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from domain.chat.vocabulary import (
    ValidatedVocabulary,
    build_roster,
    normalize,
    read_generated_aliases,
    validate_aliases,
    write_generated_aliases,
)
from domain.datasets.source import LocalMarkdownSource

# Sidecar next to the alias artifact, holding a fingerprint of the dataset
# vocabulary the aliases were last generated for. Kept SEPARATE from the alias
# artifact so the concept path's rewrite of the artifact never disturbs it.
DATASET_FINGERPRINT_REL = ".llmwiki/dataset_aliases.fingerprint"


def dataset_vocabulary(workspace: Path) -> set[str]:
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

    roster = build_roster(dataset_vocabulary(workspace), concept_names, new_names)
    validated = validate_aliases(proposals, roster)
    write_generated_aliases(workspace, validated.aliases)
    return validated


def _vocab_fingerprint(vocab: Iterable[str]) -> str:
    """A stable hash of the normalized dataset vocabulary (order-independent)."""
    canon = "\n".join(sorted({n for t in vocab if (n := normalize(t))}))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _read_fingerprint(workspace: Path) -> str:
    try:
        return (workspace / DATASET_FINGERPRINT_REL).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_fingerprint(workspace: Path, fingerprint: str) -> None:
    path = workspace / DATASET_FINGERPRINT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fingerprint + "\n", encoding="utf-8")


def regenerate_dataset_aliases(
    workspace: Path | str,
    concept_names: Iterable[str],
    propose: Callable[[list[str]], Mapping[str, Iterable[str]]],
    *,
    force: bool = False,
) -> ValidatedVocabulary | None:
    """Generate aliases for the dataset vocabulary (Piece 4), once per vocab change.

    The dataset terms don't change when a PDF is ingested — only when the
    ``datasets/`` files do — so this is gated on a fingerprint of the normalized
    dataset vocabulary (a sidecar in ``.llmwiki/``). Unless ``force``, an unchanged
    vocabulary is a no-op (returns ``None``) and ``propose`` (the LLM) is never
    called. Re-validating existing dataset aliases as the concept roster grows is
    already handled, for free, by the concept path (`update_generated_aliases`),
    which rereads and revalidates the whole artifact on every doc.

    ``propose`` is injected (so this is testable without an LLM); it maps the sorted
    dataset terms to proposed aliases. Proposals are accepted only for real dataset
    terms, merged UNDER the existing concept aliases (which are preserved), and
    validated against the coverage padrón — a proposal that is really another
    covered thing's name is dropped as a collision, exactly like the concept path.
    Returns the validated result, or ``None`` when nothing was regenerated.
    """
    workspace = Path(workspace)
    vocab = dataset_vocabulary(workspace)
    if not vocab:
        return None

    fingerprint = _vocab_fingerprint(vocab)
    if not force and _read_fingerprint(workspace) == fingerprint:
        return None

    vocab_norm = {normalize(t) for t in vocab}
    # Preserve concept entries (keys not in the dataset vocab); the dataset entries
    # are replaced wholesale by this fresh pass.
    proposals: dict[str, list[str]] = {
        canonical: list(aliases)
        for canonical, aliases in read_generated_aliases(workspace).items()
        if normalize(canonical) not in vocab_norm
    }
    for term, aliases in (propose(sorted(vocab)) or {}).items():
        if normalize(term) in vocab_norm:  # ignore invented / off-vocab proposals
            proposals.setdefault(term, []).extend(a for a in aliases if isinstance(a, str))

    roster = build_roster(vocab, concept_names)
    validated = validate_aliases(proposals, roster)
    write_generated_aliases(workspace, validated.aliases)
    _write_fingerprint(workspace, fingerprint)
    return validated
