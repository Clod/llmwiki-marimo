---
categorias:
  plazo_fijo:
    metricas: [TNA]
    attributes: [disponibilidad, plazos_dias, moneda, metodo_calculo, metrica_tasa]
  fci_money_market:
    metricas: [rendimiento]
    attributes: [disponibilidad, moneda, metodo_calculo, metrica_tasa]
  caucion:
    metricas: [TNA]
    attributes: [disponibilidad, plazos_dias, moneda, metodo_calculo, metrica_tasa]
  acciones:
    metricas: [precio]
    attributes: [disponibilidad, moneda, metodo_calculo, depende_de]
  dolar:
    metricas: [compra, venta]
    attributes: [disponibilidad, moneda]
---

# Finance requirements

Single source of truth for what each advisory category needs from the
workspace's `datasets/` directory. Read by the validator
(`domain/finance_argentina/validator.py`) before `estimate_alternatives` runs — a
category that fails any check below is excluded from the advisory with an
honest note (design_finance_argentina.md §3).

Each category's `datasets/<categoria>.md` file supplies **both** parts: its
`metricas` come from the table columns, and its `attributes` (the advisory
contract — instrument → math) come from the file's own front-matter, alongside
the rows. The LLM-generated concept prose is never read for these values.

Per-category notes:

## plazo_fijo

Traditional ARS fixed-term deposit. Deterministic via `interes_simple_vencimiento`
(simple interest at maturity). The dataset (`datasets/plazo_fijo.md`) carries the
nominal annual rate `TNA` (unit `%`) per entity per term, and its front-matter
declares `disponibilidad: a_plazo`, the available `plazos_dias`, `moneda: ARS`,
`metodo_calculo: interes_simple_vencimiento`, and `metrica_tasa: TNA` (the dataset
metric that supplies the rate).

## fci_money_market

Money-market mutual fund / digital wallet yield. Deterministic via
`capitalizacion_diaria` (daily compounding of the quoted nominal annual rate).
The dataset carries `rendimiento` (unit `%`); its front-matter declares
`disponibilidad: inmediata` (T+0 liquidity), `moneda`, `metodo_calculo:
capitalizacion_diaria`, and `metrica_tasa: rendimiento`.

## caucion

Repo-style short-term peso lending (bolsa caución). Deterministic via
`interes_simple_vencimiento`, same shape as `plazo_fijo`. The dataset carries
`TNA`; its front-matter declares `disponibilidad: a_plazo`, `plazos_dias`,
`moneda: ARS`, `metodo_calculo: interes_simple_vencimiento`, `metrica_tasa: TNA`.

## acciones

Equities — non-deterministic by design (estimating a future stock return would
be inventing a number). The dataset carries the current `precio` only (for
liquidity/eligibility display, not gain); its front-matter declares
`disponibilidad: inmediata`, `moneda`, `metodo_calculo: no_deterministico`, and
`depende_de: [precio_mercado]` — the factual driver of variability, never a
risk forecast.

## dolar

FX quote (compra/venta) — not itself an investable instrument with a gain;
kept in the manifest as a reference category (two metrics, no `metodo_calculo`
required) per design_finance_argentina.md §2.
