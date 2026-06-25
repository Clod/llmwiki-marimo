"""Concept-page attribute reading — finance vocabulary (design_finance_module.md §4).

PURPOSE FOR BEGINNERS:
Concept pages already carry free-form YAML front-matter (e.g. `tags:`,
`sources:`). The finance module documents and reads its own keys from that
same front-matter block — `disponibilidad`, `plazos_dias`, `monto_minimo`,
`moneda`, `metodo_calculo`, `metrica_tasa`, `depende_de` — without adding any
generic "attributes mechanism" to the engine. This module reuses
`domain/datasets/frontmatter.py` (PyYAML-based) for the actual YAML parsing;
it only adds the finance-specific typing/validation on top.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from domain.datasets.frontmatter import parse_frontmatter, split_frontmatter

logger = logging.getLogger("wiki.domain.finance_argentina.concept_attrs")

_DISPONIBILIDAD_VALUES = {"inmediata", "a_plazo"}
_METODO_CALCULO_VALUES = {
    "interes_simple_vencimiento",
    "capitalizacion_diaria",
    "no_deterministico",
}


@dataclass(frozen=True)
class ConceptAttributes:
    """Typed view over the finance attributes block of one concept page.

    All fields default to "absent" sentinels (`None` / empty tuple) — a
    concept page that does not declare a given key simply leaves that field
    unset; the validator (§3) is what decides whether that is acceptable for
    a given category's required attribute list.
    """

    disponibilidad: str | None = None
    plazos_dias: tuple[int, ...] = field(default_factory=tuple)
    monto_minimo: float = 0.0
    moneda: str | None = None
    metodo_calculo: str | None = None
    metrica_tasa: str | None = None
    depende_de: tuple[str, ...] = field(default_factory=tuple)

    def has(self, attribute: str) -> bool:
        """True when `attribute` is present (declared, non-empty) on this page."""
        value = getattr(self, attribute, None)
        if value is None:
            return False
        if isinstance(value, tuple):
            return len(value) > 0
        return True

    def is_well_typed(self, attribute: str) -> bool:
        """True when a *present* `attribute` holds a valid value for its type/enum.

        Absence is not a typing failure (the validator checks presence
        separately) — this only flags a present-but-invalid value.
        """
        if attribute == "disponibilidad":
            return self.disponibilidad is None or self.disponibilidad in _DISPONIBILIDAD_VALUES
        if attribute == "metodo_calculo":
            return self.metodo_calculo is None or self.metodo_calculo in _METODO_CALCULO_VALUES
        if attribute == "plazos_dias":
            return all(isinstance(d, int) for d in self.plazos_dias)
        if attribute == "monto_minimo":
            return isinstance(self.monto_minimo, (int, float))
        return True


def parse_concept_attributes(text: str, *, page_slug: str = "") -> ConceptAttributes:
    """Read the finance attribute vocabulary from a concept page's front-matter.

    Args:
        text: Full concept page markdown (front-matter + body).
        page_slug: Used only for log context.

    Returns:
        A `ConceptAttributes` with whatever finance keys are present. A page
        with no front-matter, or front-matter that fails to parse, returns an
        all-absent `ConceptAttributes()` (never raises) — the validator turns
        that into an honest "missing attribute" report rather than a crash.
    """
    frontmatter_block, _body = split_frontmatter(text)
    if frontmatter_block is None:
        return ConceptAttributes()

    try:
        meta = parse_frontmatter(frontmatter_block)
    except ValueError as exc:
        logger.warning("Concept page %s: unparseable front-matter: %s", page_slug, exc)
        return ConceptAttributes()

    return ConceptAttributes(
        disponibilidad=_as_optional_str(meta.get("disponibilidad")),
        plazos_dias=_as_int_tuple(meta.get("plazos_dias"), page_slug),
        monto_minimo=_as_float(meta.get("monto_minimo"), page_slug),
        moneda=_as_optional_str(meta.get("moneda")),
        metodo_calculo=_as_optional_str(meta.get("metodo_calculo")),
        metrica_tasa=_as_optional_str(meta.get("metrica_tasa")),
        depende_de=_as_str_tuple(meta.get("depende_de")),
    )


def _as_optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _as_int_tuple(value: object, page_slug: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        logger.warning("Concept page %s: plazos_dias is not a list — ignored", page_slug)
        return ()
    result = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            logger.warning("Concept page %s: non-integer plazos_dias entry %r — skipped", page_slug, item)
    return tuple(result)


def _as_str_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        return (str(value),)
    return tuple(str(item) for item in value)


def _as_float(value: object, page_slug: str) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("Concept page %s: monto_minimo %r is not numeric — defaulting to 0", page_slug, value)
        return 0.0
