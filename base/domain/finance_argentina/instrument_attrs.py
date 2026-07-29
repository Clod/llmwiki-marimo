"""Instrument attribute reading — finance vocabulary (design_finance_argentina.md §4).

PURPOSE FOR BEGINNERS:
Each advisory category's dataset file (`datasets/<categoria>.md`) carries
self-describing YAML front-matter. Beyond the engine's structural keys
(`type`, `formato`, `metrica`, …) the finance overlay reads its own keys from
that same block — `disponibilidad`, `plazos_dias`, `monto_minimo`, `moneda`,
`metodo_calculo`, `metrica_tasa`, `depende_de`. The engine stays domain-neutral:
a `DatasetSource` returns the raw front-matter mapping via `attributes()`; this
module is the *only* place that knows what those finance keys mean.

The advisory attributes are a machine-readable contract (instrument → math),
so they live in the human-owned dataset layer next to the rows — never in the
LLM-generated concept prose (which stays untouched).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

logger = logging.getLogger("wiki.domain.finance_argentina.instrument_attrs")

_DISPONIBILIDAD_VALUES = {"inmediata", "a_plazo"}
_METODO_CALCULO_VALUES = {
    "interes_simple_vencimiento",
    "capitalizacion_diaria",
    "no_deterministico",
}


@dataclass(frozen=True)
class InstrumentAttributes:
    """Typed view over the finance attributes of one advisory category.

    All fields default to "absent" sentinels (`None` / empty tuple) — a
    dataset that does not declare a given key simply leaves that field
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
        """True when `attribute` is present (declared, non-empty)."""
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


def attributes_from_meta(meta: Mapping[str, object], *, categoria: str = "") -> InstrumentAttributes:
    """Interpret a dataset's raw front-matter mapping as finance attributes.

    Args:
        meta: The category's self-describing front-matter (as returned by
            `DatasetSource.attributes(categoria)`) — an already-parsed mapping,
            not raw text. YAML parsing is the datasets layer's job.
        categoria: Used only for log context.

    Returns:
        An `InstrumentAttributes` with whatever finance keys are present. An
        empty mapping (no dataset, or none of the finance keys declared)
        yields an all-absent `InstrumentAttributes()` — the validator turns
        that into an honest "missing attribute" report rather than a crash.
    """
    return InstrumentAttributes(
        disponibilidad=_as_optional_str(meta.get("disponibilidad")),
        plazos_dias=_as_int_tuple(meta.get("plazos_dias"), categoria),
        monto_minimo=_as_float(meta.get("monto_minimo"), categoria),
        moneda=_as_optional_str(meta.get("moneda")),
        metodo_calculo=_as_optional_str(meta.get("metodo_calculo")),
        metrica_tasa=_as_optional_str(meta.get("metrica_tasa")),
        depende_de=_as_str_tuple(meta.get("depende_de")),
    )


def _as_optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _as_int_tuple(value: object, categoria: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        logger.warning("Dataset %s: plazos_dias is not a list — ignored", categoria)
        return ()
    result = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            logger.warning("Dataset %s: non-integer plazos_dias entry %r — skipped", categoria, item)
    return tuple(result)


def _as_str_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        return (str(value),)
    return tuple(str(item) for item in value)


def _as_float(value: object, categoria: str) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logger.warning("Dataset %s: monto_minimo %r is not numeric — defaulting to 0", categoria, value)
        return 0.0
