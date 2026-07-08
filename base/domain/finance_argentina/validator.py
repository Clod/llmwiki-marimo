"""Finance validator — domain lint check (design_finance_argentina.md §3).

PURPOSE FOR BEGINNERS:
Before the advisory tool (`estimate_alternatives`) trusts a category, this
validator checks two things against the requirements manifest, both via the
backend-agnostic `DatasetSource` (never reading disk directly):
1. the dataset has every required `metricas` present (`source.query`);
2. the category declares every required `attributes`, well-typed
   (`source.attributes` — the dataset file's front-matter).

Validates **structured md only** — never PDFs or concept prose — per
docs/design_datasets.md §4.3. A category that fails is reported with a
specific reason; the advisory excludes it rather than guessing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from domain.datasets.models import DatasetSource
from domain.finance_argentina.instrument_attrs import InstrumentAttributes, attributes_from_meta
from domain.finance_argentina.requirements import FinanceRequirements

logger = logging.getLogger("wiki.domain.finance_argentina.validator")


@dataclass(frozen=True)
class CategoryValidation:
    """Validation outcome for one advisory category."""

    categoria: str
    ok: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ValidationReport:
    """Validation outcome for every category in the manifest."""

    results: tuple[CategoryValidation, ...] = field(default_factory=tuple)

    @property
    def passing(self) -> tuple[str, ...]:
        return tuple(r.categoria for r in self.results if r.ok)

    @property
    def failing(self) -> tuple[CategoryValidation, ...]:
        return tuple(r for r in self.results if not r.ok)

    def reason_for(self, categoria: str) -> str:
        for r in self.results:
            if r.categoria == categoria and not r.ok:
                return "; ".join(r.reasons)
        return ""


def validate_workspace(
    requirements: FinanceRequirements,
    dataset_source: DatasetSource,
) -> ValidationReport:
    """Validate every category in `requirements` against the dataset source.

    Args:
        requirements: The parsed finance requirements manifest.
        dataset_source: Engine `DatasetSource` (e.g. `LocalMarkdownSource`),
            queried for both dataset rows (`query`) and each category's
            advisory attributes (`attributes`) — never read from disk directly
            here, keeping the validator backend-agnostic.

    Returns:
        A `ValidationReport` with one `CategoryValidation` per manifest
        category, in manifest order.
    """
    results = []
    for categoria, spec in requirements.categorias.items():
        reasons = []
        reasons.extend(_check_dataset(categoria, spec.metricas, dataset_source))
        reasons.extend(_check_attributes(categoria, spec.attributes, dataset_source))

        ok = not reasons
        if not ok:
            logger.warning("Finance validator: categoria %r failed: %s", categoria, "; ".join(reasons))
        results.append(CategoryValidation(categoria=categoria, ok=ok, reasons=tuple(reasons)))

    return ValidationReport(results=tuple(results))


def _check_dataset(categoria: str, required_metricas: tuple[str, ...], source: DatasetSource) -> list[str]:
    rows = source.query(categoria)
    if not rows:
        return [f"datos incompletos para '{categoria}': no se encontró el dataset"]

    present_metricas = {row.metrica for row in rows}
    missing = [m for m in required_metricas if m not in present_metricas]
    if missing:
        return [f"datos incompletos para '{categoria}': falta métrica {', '.join(missing)}"]
    return []


def _check_attributes(
    categoria: str, required_attributes: tuple[str, ...], source: DatasetSource
) -> list[str]:
    attrs = attributes_from_meta(source.attributes(categoria), categoria=categoria)
    return _missing_or_invalid_attributes(categoria, required_attributes, attrs)


def _missing_or_invalid_attributes(
    categoria: str, required_attributes: tuple[str, ...], attrs: InstrumentAttributes
) -> list[str]:
    reasons = []
    for attribute in required_attributes:
        if not attrs.has(attribute):
            reasons.append(f"datos incompletos para '{categoria}': falta atributo '{attribute}'")
        elif not attrs.is_well_typed(attribute):
            reasons.append(f"datos incompletos para '{categoria}': atributo '{attribute}' mal tipado")
    return reasons
