# Finance Module — Concrete Design (working doc)

> **Status:** DRAFT — collaborative. The concrete spec of the finance domain
> module. Architecture/seams live in `docs/design_datasets.md`; the generic
> dataset format in `.trellis/spec/backend/datasets-format.md`.
>
> **Principle (inherited):** generic about data shapes (engine), concrete about
> domain logic (this module). Formulae are **code**, not a rules language.
>
> **Separable:** this module reads the engine's `DatasetSource` + concept
> front‑matter and registers one agent tool. It can live private/optional; the
> engine never learns the word "plazo fijo".

---

## 1. What the module contains

1. **Requirements manifest** (§2) — markdown, what each category needs.
2. **Validator** (§3) — checks the workspace vs. the manifest (a domain lint check).
3. **Concept‑attribute vocabulary** (§4) — the documented front‑matter keys it reads.
4. **Gain formulae** (§5) — deterministic, keyed by `metodo_calculo`.
5. **`estimate_alternatives`** (§6) — the advisory tool (the UC‑A north star).

---

## 2. Requirements manifest (`finance/requirements.md`)

Front‑matter = machine‑readable requirements; body = human documentation. One
file, three readers (author / validator / future producer).

```
---
categorias:
  plazo_fijo:
    dataset:  { metricas: [TNA] }
    concept:  { attributes: [disponibilidad, plazos_dias, moneda, metodo_calculo, metrica_tasa] }
  fci_money_market:
    dataset:  { metricas: [rendimiento] }
    concept:  { attributes: [disponibilidad, moneda, metodo_calculo, metrica_tasa] }
  dolar:
    dataset:  { metricas: [compra, venta] }
    concept:  { attributes: [disponibilidad, moneda] }
  acciones:                       # non-deterministic
    dataset:  { metricas: [precio] }
    concept:  { attributes: [disponibilidad, moneda, metodo_calculo] }
---
# Finance requirements
(Per-category prose: what each metric/attribute means and where to source it.)
```

---

## 3. Validator (domain lint check)

Validates **structured md only** — never PDFs/prose. For each `categoria` in the
manifest:
- **dataset**: `datasets/<categoria>.md` exists? each required `metricas`
  present as a numeric column/metric?
- **concept**: concept page exists? each required `attributes` present in
  front‑matter and well‑typed (enum/list/number per §4)?

**Report**: list of `(categoria, missing|invalid, detail)`. **Advisory gate**: a
category that fails is **excluded** from `estimate_alternatives` with an honest
note ("datos incompletos para `plazo_fijo`: falta métrica `TNA`") — never a guess.

---

## 4. Concept‑attribute vocabulary (documented)

Read from concept‑page front‑matter (`attributes:` block).

| Attribute | Type | Meaning | Allowed / unit | Used by | Req |
|-----------|------|---------|----------------|---------|-----|
| `disponibilidad` | enum | when funds are accessible | `inmediata` (T+0) \| `a_plazo` (locked) | eligibility | ✓ |
| `plazos_dias` | list[int] | available fixed terms | days | eligibility + gain | ✓ if `a_plazo` |
| `monto_minimo` | number | minimum to invest | in `moneda`; default 0 | eligibility | – |
| `moneda` | enum | currency | ARS \| USD \| … | eligibility + display | ✓ |
| `metodo_calculo` | enum | gain formula, or `no_deterministico` | `interes_simple_vencimiento` \| `capitalizacion_diaria` \| `no_deterministico` | gain | ✓ |
| `metrica_tasa` | str | which dataset metric is the rate | e.g. `TNA`, `rendimiento` | gain (links to dataset) | ✓ unless `no_deterministico` |

Concept→dataset link: implicit by slug (`categoria` == concept slug);
`metrica_tasa` names the rate metric.

---

## 5. Gain formulae (code) — comparable‑basis model

> **Model:** convert each instrument's quoted rate to an **effective annual rate
> (TEA)** per its `metodo_calculo`, then project over the horizon assuming the
> **current rate holds constant**. This makes heterogeneous instruments
> comparable and states one honest assumption. All rates as decimals (35% = 0.35).

| `metodo_calculo` | TEA from quoted rate `r` | Notes |
|------------------|--------------------------|-------|
| `interes_simple_vencimiento` | `TEA = (1 + r * t/365) ** (365/t) − 1` | plazo fijo; `t` = best term ≤ horizon from `plazos_dias`; simple interest per term, reinvested |
| `capitalizacion_diaria` | `TEA = (1 + r/365) ** 365 − 1` | money‑market FCI / billetera; `r` = quoted nominal annual |
| `no_deterministico` | — | **no TEA, no gain** (§6) |

**Projected gain** over horizon of `Y` days, principal `P`:
```
gain = P * ((1 + TEA) ** (Y/365) − 1)
```
Reported with: the `TEA` used, the gain amount, the rate's `as_of` + `fuente`,
and the assumption ("si la tasa actual se mantiene").

---

## 6. `estimate_alternatives(amount, horizon, moneda="ARS")`

The advisory tool (conditionally registered when the module is active).

**Algorithm:**
1. Load active categories (manifest ∩ workspace); run the validator — exclude
   failing categories with a noted reason.
2. **Eligibility** per category (read concept attributes):
   - `moneda` matches the requested currency,
   - `monto_minimo` ≤ `amount`,
   - horizon fit: `inmediata` → always; `a_plazo` → eligible iff
     `min(plazos_dias) ≤ horizon_days` (a term completes within the horizon).
3. **Deterministic** options (`metodo_calculo` ≠ `no_deterministico`): fetch
   **all eligible** rows via `DatasetSource(categoria, metrica=metrica_tasa)`. For
   `a_plazo`, use each entity's **best‑fit term** (largest `plazos_dias` ≤ horizon).
   Convert each to TEA (§5), compute projected gain, and **list every option,
   ranked by gain across categories** — the user picks. Keep `clave`/entity, term,
   `as_of`, `fuente` per row.
4. **Non‑deterministic** options (`no_deterministico`): **no gain**; collect
   separately, flagged *"rendimiento variable — no estimable"*, eligibility by
   liquidity still applies.
5. Attach **cited risk caveats** pulled from each concept's prose.
6. Return a two‑section, cited comparison (below).

**Output shape (the tool returns this; agent narrates):**
```
Para $1.000.000 a ~3 meses (ARS), si la tasa actual se mantiene:

Alternativas con ganancia estimada (ordenadas por ganancia)
| opción         | entidad/clave  | plazo | TEA   | ganancia est. | al fecha   | fuente |
|----------------|----------------|-------|-------|---------------|------------|--------|
| FCI Money Mkt  | Mercado Fondos | T+0   | 41.0% | $98.500       | 2026-06-25 | mp.com |
| Plazo Fijo     | Banco Galicia  | 90d   | 39.8% | $95.800       | 2026-06-25 | galicia|
| Plazo Fijo     | Banco Nación   | 90d   | 39.5% | $95.100       | 2026-06-25 | bna    |
| Billetera Ualá | Ualá           | T+0   | 38.0% | $91.700       | 2026-06-25 | uala   |
| …              |                |       |       |               |            |        |

Renta variable (no estimable)
| opción  | nota                                   | riesgo (fuente)        |
|---------|----------------------------------------|------------------------|
| Acciones| rendimiento variable — no estimable    | alta volatilidad [doc] |
```

---

## 7. Edge cases

| Case | Behavior |
|------|----------|
| No eligible deterministic options | say so; show variable‑return options if any |
| `amount` < all `monto_minimo` | "el monto no alcanza el mínimo de ninguna alternativa" |
| horizon < all `a_plazo` terms, but `inmediata` exists | only liquid options |
| category fails validation | exclude + note "datos incompletos para X" |
| dataset stale | state `as_of`; reader judges (no threshold) |

---

## 8. Tests required

| Test | Assertion |
|------|-----------|
| `test_tea_simple` | `interes_simple_vencimiento` TEA & gain match hand calc (±1e‑4) |
| `test_tea_daily` | `capitalizacion_diaria` TEA & gain match |
| `test_eligibility_currency_min_horizon` | filters by `moneda`, `monto_minimo`, term‑vs‑horizon |
| `test_non_deterministic_excluded_from_ranking` | `no_deterministico` → no gain, appears only in variable section |
| `test_validator_gate` | missing `TNA` → `plazo_fijo` excluded + reason; advisory still runs for the rest |
| `test_empty_no_eligible` | no eligible options → honest message, no fabricated numbers |
| `test_lists_all_eligible_ranked` | every eligible option listed, ranked by gain; entity/term/`as_of`/`fuente` carried |

Deterministic unit tests (in‑memory manifest + concept + dataset; no LLM/network).

---

## 9. Deferred / out of scope (v1)

- **Producer / extraction** (manifest is its spec), **data‑as‑a‑service**.
- **Personal holdings** (UC10) — sensitive; separate/private layer or later.
- **Multi‑currency conversion** (e.g. compare ARS vs USD instruments via FX) —
  v1 filters by `moneda`; cross‑currency comparison later.
- **Tax treatment**, fees, rollover modeling beyond constant‑rate projection.
- **List capping/grouping** for very long lists — v1 lists **all** eligible
  options ranked; an optional top‑N cap ("y N más") can come later.
