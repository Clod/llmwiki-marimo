"""estimate_alternatives — the advisory tool (design_finance_module.md §6).

PURPOSE FOR BEGINNERS:
This is the "UC-A north star": given an amount and a horizon, list every
eligible investment alternative the workspace's data supports, ranked by
estimated gain — and separately list variable-return instruments that are
honestly flagged as "not estimable" rather than guessed. All gain arithmetic
is delegated to `domain.finance_argentina.formulae` (deterministic code, never an LLM).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from domain.datasets.models import DatasetRow, DatasetSource
from domain.finance_argentina.concept_attrs import ConceptAttributes, parse_concept_attributes
from domain.finance_argentina.formulae import projected_gain, tea
from domain.finance_argentina.requirements import FinanceRequirements
from domain.finance_argentina.validator import validate_workspace

logger = logging.getLogger("wiki.domain.finance_argentina.advisory")

_NO_DETERMINISTICO = "no_deterministico"
_A_PLAZO = "a_plazo"
_INMEDIATA = "inmediata"


@dataclass(frozen=True)
class RankedOption:
    """One deterministic alternative — a row in the gain-ranked table."""

    categoria: str
    clave: str
    term_label: str
    tea: float
    gain: float
    as_of: str
    fuente: str


@dataclass(frozen=True)
class VariableOption:
    """One non-deterministic alternative — listed separately, no gain."""

    categoria: str
    clave: str
    depende_de: tuple[str, ...]
    as_of: str
    fuente: str


@dataclass(frozen=True)
class AdvisoryResult:
    """Two-section result: gain-ranked deterministic options + variable options."""

    amount: float
    horizon_days: int
    moneda: str
    ranked: tuple[RankedOption, ...] = field(default_factory=tuple)
    variable: tuple[VariableOption, ...] = field(default_factory=tuple)
    excluded_reasons: tuple[str, ...] = field(default_factory=tuple)


def estimate_alternatives(
    amount: float,
    horizon_months: float,
    requirements: FinanceRequirements,
    dataset_source: DatasetSource,
    workspace: Path,
    moneda: str = "ARS",
) -> AdvisoryResult:
    """Run the full advisory algorithm (design_finance_module.md §6).

    Args:
        amount: Principal to invest, in `moneda`.
        horizon_months: How long the funds can stay invested, in months
            (converted to days as `horizon_months * 30`).
        requirements: The parsed finance requirements manifest.
        dataset_source: Engine `DatasetSource` for current rate/price rows.
        workspace: Workspace root (concept pages live under `wiki/concepts/`).
        moneda: Requested currency; only same-currency instruments are
            eligible (cross-currency comparison is deferred, §9).

    Returns:
        An `AdvisoryResult` with deterministic options ranked by gain and
        non-deterministic options listed separately, plus the reasons any
        category was excluded by the validator gate.
    """
    horizon_days = round(horizon_months * 30)

    validation = validate_workspace(requirements, dataset_source, workspace)
    excluded_reasons = tuple(
        f"{r.categoria}: {'; '.join(r.reasons)}" for r in validation.failing
    )

    ranked: list[RankedOption] = []
    variable: list[VariableOption] = []

    for categoria in validation.passing:
        attrs = _read_concept_attrs(workspace, categoria)
        if not _is_eligible(attrs, amount, horizon_days, moneda):
            continue

        if attrs.metodo_calculo == _NO_DETERMINISTICO:
            variable.append(
                VariableOption(
                    categoria=categoria,
                    clave=categoria,
                    depende_de=attrs.depende_de,
                    as_of="",
                    fuente="",
                )
            )
            continue

        if attrs.metodo_calculo is None:
            # Reference category with no gain formula declared (e.g. "dolar")
            # — not an investable alternative, not a flagged variable either.
            continue

        ranked.extend(_ranked_options_for(categoria, attrs, dataset_source, amount, horizon_days))

    ranked.sort(key=lambda opt: opt.gain, reverse=True)

    return AdvisoryResult(
        amount=amount,
        horizon_days=horizon_days,
        moneda=moneda,
        ranked=tuple(ranked),
        variable=tuple(variable),
        excluded_reasons=excluded_reasons,
    )


def _read_concept_attrs(workspace: Path, categoria: str) -> ConceptAttributes:
    concept_path = workspace / "wiki" / "concepts" / f"{categoria}.md"
    try:
        text = concept_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("estimate_alternatives: could not read concept page for %s: %s", categoria, exc)
        return ConceptAttributes()
    return parse_concept_attributes(text, page_slug=categoria)


def _is_eligible(attrs: ConceptAttributes, amount: float, horizon_days: int, moneda: str) -> bool:
    if attrs.moneda != moneda:
        return False
    if amount < attrs.monto_minimo:
        return False
    if attrs.disponibilidad == _INMEDIATA:
        return True
    if attrs.disponibilidad == _A_PLAZO:
        if not attrs.plazos_dias:
            return False
        return min(attrs.plazos_dias) <= horizon_days
    return False  # disponibilidad missing/unrecognized -> not eligible


def _ranked_options_for(
    categoria: str,
    attrs: ConceptAttributes,
    dataset_source: DatasetSource,
    amount: float,
    horizon_days: int,
) -> list[RankedOption]:
    if not attrs.metrica_tasa:
        logger.warning("estimate_alternatives: %s has no metrica_tasa — skipped", categoria)
        return []

    rows = dataset_source.query(categoria, metrica=attrs.metrica_tasa)
    if not rows:
        return []

    options = []
    for row in rows:
        option = _option_from_row(categoria, attrs, row, amount, horizon_days)
        if option is not None:
            options.append(option)
    return options


def _option_from_row(
    categoria: str,
    attrs: ConceptAttributes,
    row: DatasetRow,
    amount: float,
    horizon_days: int,
) -> RankedOption | None:
    r = _rate_as_decimal(row.valor, row.unidad)

    if attrs.disponibilidad == _A_PLAZO:
        term_days = _best_fit_term(row, attrs.plazos_dias, horizon_days)
        if term_days is None:
            return None  # this row's term doesn't fit the horizon
        term_label = f"{term_days}d"
        rate_tea = tea(attrs.metodo_calculo, r, term_days=term_days)
    else:
        term_days = horizon_days
        term_label = "T+0"
        rate_tea = tea(attrs.metodo_calculo, r)

    gain = projected_gain(amount, rate_tea, horizon_days)
    return RankedOption(
        categoria=categoria,
        clave=row.clave,
        term_label=term_label,
        tea=rate_tea,
        gain=gain,
        as_of=row.as_of.isoformat(),
        fuente=row.fuente,
    )


def _best_fit_term(row: DatasetRow, plazos_dias: tuple[int, ...], horizon_days: int) -> int | None:
    """Largest declared term <= horizon_days, restricted to this row's own dim term.

    Each `a_plazo` dataset row already carries one specific term in its
    `dims` (matriz format: `{"plazo": "30d"}`) — we honor that row's term
    rather than re-deriving it, but only accept it as a "best fit" candidate
    if it is one of the concept's declared `plazos_dias` and fits the horizon.
    """
    row_term = _term_days_from_dims(row.dims)
    if row_term is None:
        return None
    if row_term not in plazos_dias:
        return None
    if row_term > horizon_days:
        return None
    return row_term


def _term_days_from_dims(dims: dict[str, str]) -> int | None:
    plazo_value = dims.get("plazo")
    if plazo_value is None:
        return None
    match = re.match(r"(\d+)", plazo_value)
    if match is None:
        return None
    return int(match.group(1))


def _rate_as_decimal(valor: float, unidad: str) -> float:
    """Convert a stored rate value to a decimal, per its declared unit.

    Dataset rates are stored as percent numbers (e.g. TNA 35.00, unidad
    "%") — design_finance_module.md instructs converting % -> decimal
    (0.35) before the formulae. A non-"%" unit is assumed already decimal.
    """
    if unidad.strip() == "%":
        return valor / 100.0
    return valor


def render_markdown(result: AdvisoryResult) -> str:
    """Render an `AdvisoryResult` as the two-section cited markdown comparison.

    Shape matches design_finance_module.md §6's output sketch: a ranked
    deterministic table, then a variable-return table, each citing
    clave/term/TEA/gain/as_of/fuente or depende_de as appropriate.
    """
    lines = [
        f"Para ${result.amount:,.0f} a ~{result.horizon_days} días ({result.moneda}), "
        "si la tasa actual se mantiene:",
        "",
    ]

    if result.excluded_reasons:
        lines.append("**Categorías excluidas** (datos incompletos):")
        for reason in result.excluded_reasons:
            lines.append(f"- {reason}")
        lines.append("")

    lines.append("**Alternativas con ganancia estimada** (ordenadas por ganancia)")
    if result.ranked:
        lines.append("| opción | clave | plazo | TEA | ganancia est. | al fecha | fuente |")
        lines.append("|---|---|---|---|---|---|---|")
        for opt in result.ranked:
            lines.append(
                f"| {opt.categoria} | {opt.clave} | {opt.term_label} | "
                f"{opt.tea * 100:.2f}% | ${opt.gain:,.0f} | {opt.as_of} | {opt.fuente} |"
            )
    else:
        lines.append("_No hay alternativas con ganancia estimable para este monto y plazo._")
    lines.append("")

    lines.append("**Renta variable** (no estimable)")
    if result.variable:
        lines.append("| opción | depende de |")
        lines.append("|---|---|")
        for opt in result.variable:
            depende = ", ".join(opt.depende_de) if opt.depende_de else "—"
            lines.append(f"| {opt.categoria} | {depende} |")
    else:
        lines.append("_Sin alternativas de renta variable elegibles._")

    return "\n".join(lines)
