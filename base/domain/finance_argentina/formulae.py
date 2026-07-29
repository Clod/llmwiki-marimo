"""Gain formulae — deterministic, pure functions (design_finance_module.md §5).

PURPOSE FOR BEGINNERS:
"TEA" (Tasa Efectiva Anual / effective annual rate) is the comparable-basis
model: convert each instrument's quoted nominal rate to an effective annual
rate per its `metodo_calculo`, then project a gain over the requested horizon
assuming the **current rate holds constant**. This is the only arithmetic in
the finance module that ever touches a real number — never delegated to an
LLM (design_datasets.md §2.1: "gain math is deterministic code... never LLM
arithmetic").

All rates are decimals here (35% = 0.35) — callers convert percent values
from the dataset before calling these functions.
"""

from __future__ import annotations

_INTERES_SIMPLE_VENCIMIENTO = "interes_simple_vencimiento"
_CAPITALIZACION_DIARIA = "capitalizacion_diaria"
_NO_DETERMINISTICO = "no_deterministico"

_DAYS_PER_YEAR = 365


def tea(metodo_calculo: str, r: float, term_days: int | None = None) -> float:
    """Convert a quoted nominal rate `r` (decimal) to an effective annual rate.

    Args:
        metodo_calculo: One of `interes_simple_vencimiento`,
            `capitalizacion_diaria`. `no_deterministico` has no TEA — callers
            must check `metodo_calculo` themselves before computing (no gain
            is ever computed for non-deterministic instruments, per §5).
        r: Quoted nominal rate as a decimal (35% -> 0.35).
        term_days: Required for `interes_simple_vencimiento` — the fixed
            term (`t` in the formula) over which `r` is quoted/paid at
            maturity. Ignored for `capitalizacion_diaria` (always a 365-day
            compounding base).

    Returns:
        The effective annual rate as a decimal.

    Raises:
        ValueError: `metodo_calculo` is `no_deterministico` or unknown, or
            `term_days` is missing/non-positive for `interes_simple_vencimiento`.
    """
    if metodo_calculo == _INTERES_SIMPLE_VENCIMIENTO:
        if term_days is None or term_days <= 0:
            raise ValueError("interes_simple_vencimiento requires a positive term_days")
        return (1 + r * term_days / _DAYS_PER_YEAR) ** (_DAYS_PER_YEAR / term_days) - 1

    if metodo_calculo == _CAPITALIZACION_DIARIA:
        return (1 + r / _DAYS_PER_YEAR) ** _DAYS_PER_YEAR - 1

    if metodo_calculo == _NO_DETERMINISTICO:
        raise ValueError("no_deterministico has no TEA — gain is never computed for it")

    raise ValueError(f"unknown metodo_calculo: {metodo_calculo!r}")


def projected_gain(principal: float, tea_value: float, horizon_days: int) -> float:
    """Project a gain over `horizon_days`, assuming `tea_value` holds constant.

    `gain = P * ((1 + TEA) ** (horizon_days/365) - 1)` — the one stated
    assumption ("si la tasa actual se mantiene") that callers must surface
    alongside the number (design_finance_module.md §5).

    Args:
        principal: Amount invested (`P`).
        tea_value: Effective annual rate as a decimal (from `tea()`).
        horizon_days: Projection horizon in days.

    Returns:
        Projected gain in the same currency unit as `principal`.
    """
    return principal * ((1 + tea_value) ** (horizon_days / _DAYS_PER_YEAR) - 1)
