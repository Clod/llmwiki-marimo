"""Hybrid pre-retrieval orchestration (WIP).

The code retrieves before the model answers, instead of leaving retrieval to the
model. This module assembles the deterministic pieces (`scope`, `overlap`) with
the dataset vocabulary and the per-wiki lists.

First piece: `build_vocabulary` — the closed "lista de datos" (dataset
categories + their keys) that `scope.mentions_known_data` checks a question
against. Everything here is derived from a `DatasetSource`; no LLM.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from domain.chat.scope import is_off_limits
from domain.datasets.models import DatasetSource


def build_vocabulary(source: DatasetSource) -> set[str]:
    """The set of known data terms: every dataset category plus every key in it.

    Keys can repeat across rows (one per metrica); the set dedupes them.
    """
    vocabulary: set[str] = set()
    for categoria in source.categories():
        vocabulary.add(categoria)
        for row in source.query(categoria):
            vocabulary.add(row.clave)
    return vocabulary


@dataclass(frozen=True)
class RetrievalPlan:
    """What to do with a question after the deterministic gate + retrieval.

    action: "invoke" (call the model) | "refuse" (answer "no lo tengo", no LLM).
    tier:   "curado" (Tier 1 wiki page) | "crudo" (Tier 2 raw doc) | None (data
            question — tools only, no injected context).
    context: the retrieved text to inject, or None.
    verify:  run answer-vs-source overlap after the answer (Tier 2 only).
    """

    action: str
    tier: str | None
    context: str | None
    verify: bool


_REFUSE = RetrievalPlan(action="refuse", tier=None, context=None, verify=False)


def plan_retrieval(
    question: str,
    *,
    off_limits: Iterable[str],
    wiki_hits: list[str],
    doc_hits: list[str],
    has_data: bool,
) -> RetrievalPlan:
    """Decide the plan. Order: blacklist first (refuse), then curated wiki
    (Tier 1), then raw docs (Tier 2, verify), then a data/advisory question
    (tools only), else refuse without invoking the model.

    `has_data` is computed by the caller (`scope.mentions_known_data`, possibly
    after a synonym-rescue step). `wiki_hits`/`doc_hits` are the retrieved page
    and chunk texts.
    """
    if is_off_limits(question, off_limits):
        return _REFUSE
    if wiki_hits:
        return RetrievalPlan("invoke", "curado", "\n\n".join(wiki_hits), False)
    if doc_hits:
        return RetrievalPlan("invoke", "crudo", "\n\n".join(doc_hits), True)
    if has_data:
        return RetrievalPlan("invoke", None, None, False)
    return _REFUSE
