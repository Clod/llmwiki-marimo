"""Normalized dataset row + backend-agnostic DatasetSource Protocol.

PURPOSE FOR BEGINNERS:
A "dataset" here means structured, periodically-refreshed tabular data (rates,
prices, stats) as opposed to the durable prose knowledge in concept pages. This
module defines the shared shape every dataset backend produces (DatasetRow) and
the interface consumers depend on (DatasetSource) instead of any specific
storage mechanism — local markdown today, a remote service later.

Engine is domain-neutral: `categoria`, `clave`, `metrica` are opaque strings.
Specific category vocabulary (e.g. "plazo_fijo") is workspace content, never
engine vocabulary. See .trellis/spec/backend/datasets-format.md §2.3 and
docs/design_datasets.md §3.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class DatasetRow:
    """One normalized observation: a single (categoria, clave, metrica) value.

    Regardless of the on-disk table shape (matriz or largo), every parser
    flattens its input into rows of this shape.
    """

    categoria: str
    clave: str
    metrica: str
    valor: float
    unidad: str
    as_of: date
    fuente: str
    dims: dict[str, str] = field(default_factory=dict)


class DatasetSource(Protocol):
    """Backend-agnostic read interface for normalized dataset rows.

    Consumers (e.g. the query_dataset agent tool) depend only on this
    Protocol, never on where rows come from. `LocalMarkdownSource` is the
    only implementation today (parse-on-read from a workspace's datasets/
    directory); a `RemoteServiceSource` is a future, swappable backend.

    Current-only: there is no date parameter — no history, replace-latest.
    """

    def categories(self) -> list[str]:
        """Return the list of known category slugs."""
        ...

    def query(
        self,
        categoria: str,
        *,
        clave: str | None = None,
        metrica: str | None = None,
        dims: dict[str, str] | None = None,
    ) -> list[DatasetRow]:
        """Return rows for `categoria`, optionally filtered by clave/metrica/dims.

        An unknown categoria returns an empty list (never raises) — the
        engine stays honest about absent data rather than erroring.
        """
        ...
