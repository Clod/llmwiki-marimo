# `finance_argentina` Module — Concrete Design (working doc)

> **Status:** DRAFT — collaborative. The concrete spec of the **`finance_argentina`**
> domain module (`base/domain/finance_argentina/`). Architecture/seams live in
> `docs/design_datasets.md`; the generic dataset format in
> `.trellis/spec/backend/datasets-format.md`.
>
> **Domain-specific by design.** This module is **Argentine** finance — plazo
> fijo, FCI, MEP, CER/UVA, caución. It is **not** general, and we don't pretend
> otherwise: it is named `finance_argentina`, and **all LLM/user-facing text
> (advisory output, validator messages, the agent tool description + prompt
> addendum) is in Spanish.** (Dev-facing logs/docstrings stay English.)
>
> **Principle (inherited):** generic about data shapes (engine), concrete about
> domain logic (this module). Formulae are **code**, not a rules language.
>
> **Separable:** this module reads the engine's `DatasetSource` (rows +
> attributes) and registers one agent tool. It can live private/optional; the
> engine never learns the word "plazo fijo".

---

## 1. What the module contains

1. **Requirements manifest** (§2) — markdown, what each category needs.
2. **Validator** (§3) — checks the workspace vs. the manifest (a domain lint check).
3. **Instrument‑attribute vocabulary** (§4) — the documented dataset front‑matter keys it reads.
4. **Gain formulae** (§5) — deterministic, keyed by `metodo_calculo`.
5. **`estimate_alternatives`** (§6) — the advisory tool (the UC‑A north star).

---

## 2. Requirements manifest (`finance_argentina/requirements.md`)

Front‑matter = machine‑readable requirements; body = human documentation. One
file, three readers (author / validator / future producer).

```
---
categorias:
  plazo_fijo:
    metricas:   [TNA]
    attributes: [disponibilidad, plazos_dias, moneda, metodo_calculo, metrica_tasa]
  fci_money_market:
    metricas:   [rendimiento]
    attributes: [disponibilidad, moneda, metodo_calculo, metrica_tasa]
  dolar:
    metricas:   [compra, venta]
    attributes: [disponibilidad, moneda]
  acciones:                       # non-deterministic
    metricas:   [precio]
    attributes: [disponibilidad, moneda, metodo_calculo, depende_de]
---
# Finance requirements
(Per-category prose: what each metric/attribute means and where to source it.)
```

Both `metricas` (table columns) and `attributes` (front-matter contract) are
supplied by the same human-owned `datasets/<categoria>.md` file — the manifest
just lists what each must contain. The LLM-generated concept prose is never
read for these values.

---

## 3. Validator (domain lint check)

Validates **structured md only** — never PDFs/prose. Both checks go through the
backend-agnostic `DatasetSource` (never reading disk directly). For each
`categoria` in the manifest:
- **rows**: `source.query(categoria)` returns rows, and each required `metricas`
  is present as a numeric column/metric?
- **attributes**: `source.attributes(categoria)` (the dataset file's
  front‑matter) declares each required `attributes`, well‑typed (enum/list/number
  per §4)?

**Report**: list of `(categoria, missing|invalid, detail)`. **Advisory gate**: a
category that fails is **excluded** from `estimate_alternatives` with an honest
note ("datos incompletos para `plazo_fijo`: falta métrica `TNA`") — never a guess.

---

## 4. Instrument‑attribute vocabulary (documented)

Read from the **dataset file's** front‑matter (`datasets/<categoria>.md`), via
`DatasetSource.attributes(categoria)` — the same human‑owned file that carries
the rows. These are a machine‑readable contract (instrument → math), so they
live in the structured data layer, never in the LLM‑generated concept prose.
The finance overlay (`instrument_attrs.py`) is the only reader that interprets
these keys; the engine returns the front‑matter generically.

| Attribute | Type | Meaning | Allowed / unit | Used by | Req |
|-----------|------|---------|----------------|---------|-----|
| `disponibilidad` | enum | when funds are accessible | `inmediata` (T+0) \| `a_plazo` (locked) | eligibility | ✓ |
| `plazos_dias` | list[int] | available fixed terms | days | eligibility + gain | ✓ if `a_plazo` |
| `monto_minimo` | number | minimum to invest | in `moneda`; default 0 | eligibility | – |
| `moneda` | enum | currency | ARS \| USD \| … | eligibility + display | ✓ |
| `metodo_calculo` | enum | gain formula, or `no_deterministico` | `interes_simple_vencimiento` \| `capitalizacion_diaria` \| `no_deterministico` | gain | ✓ |
| `metrica_tasa` | str | which dataset metric is the rate | e.g. `TNA`, `rendimiento` | gain (links to dataset) | ✓ unless `no_deterministico` |
| `depende_de` | list[enum] | for `no_deterministico`: what drives the variability (factual, not risk) | `inflacion` \| `tipo_cambio` \| `precio_mercado` \| … | advisory display | ✓ if `no_deterministico` |

Attribute→row link: both live in the same `datasets/<categoria>.md` file;
`metrica_tasa` names which of that file's metrics is the rate.

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
2. **Eligibility** per category (read instrument attributes):
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
   separately, flagged *"rendimiento variable — no estimable; depende de:
   &lt;factores&gt;"* from `depende_de` (inflación / tipo de cambio / precio de
   mercado). Eligibility by liquidity still applies.
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
| opción       | depende de        | riesgo (fuente)        |
|--------------|-------------------|------------------------|
| Acciones     | precio de mercado | alta volatilidad [doc] |
| Bono CER     | inflación         | riesgo soberano [doc]  |
| Dólar Linked | tipo de cambio    | riesgo de TC [doc]     |
```

**Nominal‑vs‑real disclaimer (required).** The ranked gains are **nominal** —
the TEA model holds the current rate constant and does **not** deflate by
inflation. When the ranked section is non‑empty, `render_markdown` appends an
explicit caveat: the *real* (purchasing‑power) gain depends on period inflation,
and a nominal gain can be a real loss if inflation exceeds the TEA. This keeps
the deterministic numbers honest without estimating inflation (which stays
`no_deterministico`): we state the limitation rather than guess a real return.
Inflation‑linked comparison lives in the CER/UVA instruments already flagged in
the variable section.

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

---

## 10. Corpus validation — `tests/fixtures/mercado_argentino/`

12 conceptual docs (one per instrument family) used to stress‑test the design.
**Verdict: the architecture closes up** — generic datasets + instrument attributes +
deterministic overlay + honest non‑deterministic handling map cleanly. Three
findings refine scope:

**F1 — The deterministic set is narrow (and that's honest).**
v1 estimates a gain only for **nominal peso‑rate** instruments via the TEA model:
**plazo fijo tradicional, FCI money market, caución** (peso). *Plazo fijo en
dólares* is also deterministic but compared **within USD** (cross‑currency
deferred). Everything else is `no_deterministico` in v1, tagged with its
`depende_de` driver:
- inflation‑linked (bonos CER/UVA, plazo fijo UVA, LECER) → `inflacion`;
- FX‑linked (dólar linked, duales, hard dollar) → `tipo_cambio`;
- market‑priced **incl. LECAP, bonos, ON, letras, acciones, CEDEARs, FCI renta
  fija/mixtos** → `precio_mercado`.
Refusing to estimate these is the honesty guarantee, not a gap.

> **Future development — scenario / assumption‑based estimation.** Several
> currently‑`no_deterministico` instruments *could* be estimated **under an
> explicit, stated assumption** (never a hidden forecast). All deferred:
> - **"held to maturity"** → `tir_al_vencimiento` for fixed income (LECAP, bonos,
>   ON) when maturity ≤ horizon (needs TIR + maturity date in the dataset);
> - **"if inflation stays at X%"** → projected gain for CER/UVA/LECER;
> - **"if the exchange rate moves to Y"** → projected gain for dólar‑linked/duales.
> Each would surface its assumption explicitly in the answer.

**F2 — Concept granularity ≠ advisory‑category granularity.** Some concept pages
bundle variants with different method/currency: "Plazos Fijos" = tradicional /
UVA / dólares; "Letras" = LECAP / LECER / LELINK. One educational concept page
may map to **several advisory categories** (each homogeneous: one
`metodo_calculo` + `moneda`). Decision (GRAN, lean): **keep the educational
concept doc whole**; the **manifest** declares the per‑variant advisory
categories (`plazo_fijo_tradicional`, `plazo_fijo_uva`, …), each mapping to its
own dataset + method/currency and pointing back to the concept doc for caveats.
Avoids fragmenting good docs.

**F3 — Multi‑currency is real (confirmed deferred).** Hard‑dollar bonos, USD ONs,
USD plazo fijo, CEDEARs (ARS/MEP). v1 compares **within a currency**; cross‑currency
via FX stays deferred.

**Regression use:** these 12 → ~12 concept pages = a finance E2E/golden fixture
(parallels the fairy‑tale corpus). Confirm DOCX ingestion before wiring an E2E.
Content is educational (non‑sensitive); committing publishes it (public repo).

| # | Decision | Lean | Status |
|---|----------|------|--------|
| DET‑scope | Deterministic = nominal‑peso‑rate only (plazo fijo tradicional, money market, caución; USD plazo fijo within USD); rest `no_deterministico` + `depende_de` | yes | **CONFIRMED** |
| NOM‑disclaimer | Ranked gains are nominal; `render_markdown` states real‑return depends on inflation rather than estimating it (§6) | yes | **CONFIRMED** |
| DET‑scenario | Assumption‑based estimation (held‑to‑maturity TIR; "if inflation stays at X"; "if FX = Y") | future | **deferred** |
| GRAN | 1 concept doc : N advisory categories — manifest declares variants; docs stay whole | yes | proposed |
| FX | Cross‑currency comparison | later | deferred |
