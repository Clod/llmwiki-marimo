"""Tests for domain/finance_argentina/formulae.py — TEA + projected gain.

Spec: docs/design_finance_module.md §5, §8 `test_tea_simple` / `test_tea_daily`.
Deterministic pure-function math — no LLM, no network, no filesystem.
"""

import pytest

from domain.finance_argentina.formulae import projected_gain, tea


# ── §8 test_tea_simple ──────────────────────────────────────────────────────


def test_tea_simple() -> None:
    """interes_simple_vencimiento TEA & gain match hand calc (+-1e-4)."""
    r = 0.35  # TNA 35%
    term_days = 30

    expected_tea = (1 + r * term_days / 365) ** (365 / term_days) - 1
    actual_tea = tea("interes_simple_vencimiento", r, term_days=term_days)
    assert actual_tea == pytest.approx(expected_tea, abs=1e-4)

    principal = 1_000_000
    horizon_days = 90
    expected_gain = principal * ((1 + expected_tea) ** (horizon_days / 365) - 1)
    actual_gain = projected_gain(principal, actual_tea, horizon_days)
    assert actual_gain == pytest.approx(expected_gain, abs=1e-4)


def test_tea_simple_requires_term_days() -> None:
    with pytest.raises(ValueError):
        tea("interes_simple_vencimiento", 0.35)
    with pytest.raises(ValueError):
        tea("interes_simple_vencimiento", 0.35, term_days=0)


# ── §8 test_tea_daily ────────────────────────────────────────────────────────


def test_tea_daily() -> None:
    """capitalizacion_diaria TEA & gain match hand calc."""
    r = 0.41  # nominal annual rendimiento 41%

    expected_tea = (1 + r / 365) ** 365 - 1
    actual_tea = tea("capitalizacion_diaria", r)
    assert actual_tea == pytest.approx(expected_tea, abs=1e-4)

    principal = 500_000
    horizon_days = 60
    expected_gain = principal * ((1 + expected_tea) ** (horizon_days / 365) - 1)
    actual_gain = projected_gain(principal, actual_tea, horizon_days)
    assert actual_gain == pytest.approx(expected_gain, abs=1e-4)


def test_tea_daily_ignores_term_days() -> None:
    """capitalizacion_diaria does not need a term — always 365-day base."""
    assert tea("capitalizacion_diaria", 0.41) == tea("capitalizacion_diaria", 0.41, term_days=30)


# ── Non-deterministic / unknown ─────────────────────────────────────────────


def test_tea_no_deterministico_raises() -> None:
    with pytest.raises(ValueError):
        tea("no_deterministico", 0.10)


def test_tea_unknown_metodo_raises() -> None:
    with pytest.raises(ValueError):
        tea("metodo_inventado", 0.10)
