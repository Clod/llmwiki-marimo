# Dataset Source Format

> Executable contract for **structured / transient** sources — domain‑neutral.
> Defines the on‑disk markdown format and the normalized rows a parser must emit
> for tabular data that changes over time (rates, prices, stats, readings, …).
> The engine knows nothing about any specific domain; **personal finance is one
> example** (see [Domain example](#domain-example-personal-finance)).
> The **producer** (how files are generated/refreshed), **storage**, and
> **agent** layers are deferred — see [Deferred — the "how"](#deferred--the-how).

---

## 1. Scope / Trigger

A wiki has **two kinds of sources**, treated differently:

| Kind | Nature | Cadence | Pipeline |
|------|--------|---------|----------|
| **Conceptual** (durable) | distilled prose knowledge | seldom | Existing concept‑page pipeline (LLM distillation, merge, FTS) — unchanged |
| **Dataset** (transient) | structured tabular values | periodic (e.g. daily) | **This spec** — parsed structurally, replace‑on‑refresh, **never LLM‑distilled** |

This is a **cross‑layer contract** (file → parser → normalized rows → storage →
chat agent tool), captured with code‑spec depth. Datasets live in their **own
directory** (`datasets/`, one file per category) and must **not** flow through
the concept‑page pipeline (see [§8 Wrong vs Correct](#8-wrong-vs-correct)).

The capability is **opt‑in per workspace and domain‑neutral** — see
[§4 Activation / Optionality](#4-activation--optionality). A plain document wiki
is unaffected.

---

## 2. Signatures (the file contract)

### 2.1 File layout

```
datasets/<categoria>.md       # one file per category; filename slug == categoria
```

Each file = **YAML front‑matter** (metadata + self‑describing mapping) followed
by **exactly one markdown table**.

### 2.2 Two table shapes (`formato`)

- `formato: matriz` — pivot grid. First column = row key; remaining column
  headers are values of one dimension; each cell is a single metric value.
- `formato: largo` — tidy/long. One row per key; each declared column is a
  metric (or a dimension).

### 2.3 Normalized row (the uniform queryable target)

Regardless of `formato`, a parser MUST flatten every file into rows of this
logical shape (physical storage is deferred):

```
(categoria, clave, metrica, valor, unidad, dims, as_of, fuente)
```

- `categoria: str`   — category slug (== filename, == concept‑page join key)
- `clave: str`       — item key (e.g. "Banco Nación", "MEP", a ticker)
- `metrica: str`     — name of the measured quantity (domain‑defined)
- `valor: float`     — numeric only (no unit baked into the value)
- `unidad: str`      — "%", "ARS", "USD", "ms", "kg", …
- `dims: dict[str,str]` — qualifying dimensions, e.g. `{"plazo": "30d"}`; `{}` if none
- `as_of: date`      — freshness
- `fuente: str`      — citation source

---

## 3. Contracts (front‑matter fields)

### 3.1 Required for every dataset file

| Key | Type | Notes |
|-----|------|-------|
| `type` | `"dataset"` | Discriminator. A file without `type: dataset` is **not** a dataset (concept pages never match). |
| `categoria` | str (slug) | Must equal the filename slug; used to join to a concept page. |
| `formato` | `"matriz"` \| `"largo"` | Selects the parse path. |
| `as_of` | `YYYY-MM-DD` | File‑level default freshness. |
| `fuente` | str | File‑level default citation. |

### 3.2 `formato: matriz` mapping keys

| Key | Type | Notes |
|-----|------|-------|
| `clave` | str | Name of the first column (the row key). |
| `columnas_dim` | str | Dimension that the remaining column **headers** are values of (e.g. `plazo`). |
| `metrica` | str | What every cell measures. |
| `unidad` | str | Unit of all cells. |

### 3.3 `formato: largo` mapping keys

| Key | Type | Notes |
|-----|------|-------|
| `clave` | str | Row‑key column name. |
| `metricas` | map `{col: unidad}` | Each named column is a metric with its unit. |
| `dimensiones` | list[str] (optional) | Columns that qualify the row but are not metrics. |

### 3.4 Per‑row freshness override (largo only)

If a `largo` table includes `as_of` / `fuente` columns, they override the
file‑level defaults for that row. Matrix files use file‑level values only.

### 3.5 Values are raw; derivations are out of core

The engine stores and serves **raw** numeric values only. Any **derived**
metric (computed from stored values, e.g. an effective rate from a nominal one)
is a **domain‑overlay concern**, out of core scope — see
[Domain example](#domain-example-personal-finance). This keeps the core engine
domain‑neutral and matrix cells single‑valued and numeric.

### 3.6 Domain‑overlay front‑matter keys (surfaced via `attributes()`)

A file's front‑matter MAY carry **extra keys beyond the structural ones** above
(e.g. a finance overlay's `disponibilidad`, `metodo_calculo`). The generic
parser **ignores** them for row parsing. A `DatasetSource` exposes the whole
front‑matter mapping via `attributes(categoria) -> Mapping[str, object]`
(unknown categoria → `{}`, never raises), so a domain overlay can read its own
keys **without the engine knowing what they mean**. This is what lets a domain
keep a machine‑readable per‑instrument contract next to its rows, in the same
human‑owned file — never in the LLM‑generated concept prose.

---

## 4. Activation / Optionality

The feature ships in the engine but is **dormant per workspace**, mirroring how
content language is in the engine yet the English path is byte‑identical when
unused.

- **Trigger**: presence of a `datasets/` directory with valid files. No
  `datasets/` → the capability is fully inert.
- **Default path is byte‑identical** when no datasets are present:
  - ingestion/scan does not touch a non‑existent `datasets/`
  - the chat agent registers the **same three tools**
    (`read_wiki_page`, `search_wiki_fts`, `search_source_chunks`)
  - the system prompt is unchanged
  - the lint pass is unchanged
- **When active**, and only then:
  - datasets are parsed → normalized rows
  - the agent **conditionally** registers `query_dataset` and the prompt gains
    dataset‑routing lines
  - the staleness lint special‑cases `datasets/` (transient = expected, not a defect)

This invariant is **enforced by a guard test** (see §7): a no‑dataset workspace
must yield exactly the three default tools and an unchanged prompt.

---

## 5. Validation & Error Matrix

| Condition | Behavior |
|-----------|----------|
| Front‑matter missing `type: dataset` | File ignored by the dataset parser (not an error — not a dataset). |
| Missing any required key (`categoria`, `formato`, `as_of`, `fuente`) | **Reject file**, log error. |
| `formato` not in {`matriz`, `largo`} | **Reject file**, log error. |
| `matriz` missing `clave`/`columnas_dim`/`metrica`/`unidad` | **Reject file**, log error. |
| `largo` missing `clave` or `metricas` | **Reject file**, log error. |
| `clave` / declared metric column absent from table header | **Reject file**, log error. |
| Non‑numeric cell where `valor` expected | **Skip that cell/row**, log warning; continue. |
| Duplicate `(clave, dims, metrica)` within a file | Last wins, log warning. |
| `categoria` ≠ filename slug | Log warning (filename is authoritative for the join). |

Logging follows the project convention: never silently swallow — log via the
`wiki` logger (see `logging-guidelines.md`).

---

## 6. Good / Base / Bad Cases

> Examples use a personal‑finance domain; the engine treats `plazo_fijo`/`dolar`
> as opaque category strings.

### Good — `datasets/plazo_fijo.md` (matriz)

```
---
type: dataset
categoria: plazo_fijo
formato: matriz
clave: entidad
columnas_dim: plazo
metrica: TNA
unidad: "%"
as_of: 2026-06-25
fuente: bna.com.ar, galicia.com.ar
---

| entidad        | 30d   | 60d   | 90d   |
|----------------|-------|-------|-------|
| Banco Nación   | 35.00 | 36.00 | 37.00 |
| Banco Galicia  | 34.50 | 35.50 | 36.50 |
```

→ 6 normalized rows, e.g.
`(plazo_fijo, "Banco Nación", TNA, 35.00, "%", {"plazo":"30d"}, 2026-06-25, "bna.com.ar, galicia.com.ar")`.

### Base — `datasets/dolar.md` (largo, two metrics)

```
---
type: dataset
categoria: dolar
formato: largo
clave: tipo
metricas: { compra: "ARS", venta: "ARS" }
as_of: 2026-06-25
fuente: ámbito
---

| tipo    | compra | venta |
|---------|--------|-------|
| Oficial | 1000   | 1050  |
| MEP     | 1180   | 1185  |
```

→ 4 normalized rows, e.g. `(dolar, "MEP", compra, 1180, "ARS", {}, 2026-06-25, "ámbito")`.

### Bad — rejected / repaired

```
| entidad      | 30d  |
|--------------|------|
| Banco Nación | 35%  |    # "35%" non‑numeric → row skipped + logged
```

```
---
type: dataset
categoria: plazo_fijo
formato: matriz          # missing clave/columnas_dim/metrica/unidad → file rejected
---
```

---

## 7. Tests Required

| Test | Assertion points |
|------|------------------|
| `test_parse_matriz` | key×dim grid → N×M rows; each row's `metrica`, `unidad`, `dims`, `as_of`, `fuente` correct. |
| `test_parse_largo` | each `metricas` column → one row per key; `dimensiones` captured into `dims`; units correct. |
| `test_type_discriminator` | a concept page (no `type: dataset`) yields **zero** dataset rows. |
| `test_missing_frontmatter_key` | missing `formato` (or a matrix key) → file rejected, error logged, no rows. |
| `test_non_numeric_cell` | a non‑numeric cell → that row skipped, warning logged, other rows still parsed. |
| `test_per_row_override` | `largo` row with `as_of`/`fuente` columns overrides file defaults. |
| `test_optionality_guard` | no `datasets/` workspace → agent registers exactly the 3 default tools and the prompt is unchanged. |

Unit tests use a deterministic in‑memory markdown string (no LLM, no network),
consistent with the project's fake‑LLM unit layer.

---

## 8. Wrong vs Correct

### Wrong — route a dataset through the concept pipeline

```
sources/tasas_hoy.pdf  →  LLM distills  →  concept page:
  "El plazo fijo paga aproximadamente 35%."
```

Paraphrased ("aproximadamente"), goes stale, the daily re‑ingest fights the
concept‑merge logic, and the staleness lint flags it as a defect every day.

### Correct — structured dataset, queried verbatim

```
datasets/plazo_fijo.md  →  parser  →  normalized rows  →  agent reads exact value
  "Banco Nación 30d: 35,00% TNA, al 25/06/2026 — fuente bna.com.ar"
```

Exact numeric value, replace‑on‑refresh, citation **+ date** stated.

### Wrong vs Correct — units in the value

```
# WRONG: unit baked into the cell (non‑numeric, unparseable)
| 30d  |
| 35%  |

# CORRECT: numeric valor + unidad declared in front‑matter
metrica: TNA
unidad: "%"
| 30d   |
| 35.00 |
```

---

## Design Decisions

### Decision: decouple "what" (format) from "how" (producer)

Freeze the file format + normalized rows as the stable interface; the producer
(manual, scrape, API) is swappable without touching parser, storage, or agent.

### Decision: domain‑neutral engine, domain lives in workspace content

The engine knows only generic *datasets*; specific categories (finance:
`plazo_fijo`, `dolar`; or any other domain) are **user content**, never engine
vocabulary. This is what lets the capability stay in the public general tool.

### Decision: one file per category (not one global table)

Within a category cardinality explodes (a rate grid = key × dimension; a price
list = hundreds of items) and metrics differ across categories. `datasets/<cat>.md`.
Bonus: filename == `categoria` == **join key to the concept page** (1:1:1).

### Decision: self‑describing front‑matter → generic parser

Each file declares its own shape + mapping. Adding a category = writing a file,
**zero parser code** (mirrors the multilingual "add a `Locale` entry"
extensibility — see `docs/design_multilingual_content.md`).

### Decision: dormant by default (opt‑in per workspace)

The capability is additive; a no‑dataset wiki is byte‑identical to today,
enforced by a guard test (§4, §7).

### Decision: freshness + citation are first‑class

Every normalized row carries `as_of` + `fuente`; the agent must state the
**date** of any value. Extends "citations as obligation" to "cite source **and**
date". (Implies the staleness lint treats `datasets/` as expected‑stale.)

---

## Domain example: personal finance

The first consumer of this engine is a personal‑finance specialization (a
private workspace/overlay, not part of the public engine):

- **Categories**: `plazo_fijo`, `fci`, `dolar`, `caucion`, `billetera`, `bono`, …
- **Derived metric (overlay, not core §3.5)**: TEA from TNA for a term of `n`
  days, interest paid at maturity:
  `TEA = (1 + TNA * n/365) ** (365/n) - 1` (rates as decimals). Stored cells are
  TNA; TEA is computed by the finance overlay on demand, never stored.

None of this appears in the engine — it is example/overlay content only.

---

## Deferred — the "how"

Not yet decided (future task workflow); intentionally out of scope here:

- **Producer / refresh**: manual edit first; scheduled `marimo-batch` notebook
  later. Source form (API / CSV / scrape / PDF) TBD.
- **Storage**: physical SQLite layout for the normalized rows; **replace‑latest**
  (markdown holds the current snapshot) vs. optional **time‑series** history.
- **Agent tool**: `query_dataset(categoria, filtro?)` signature + routing rules
  in the system prompt ("for current values, use the dataset tool + state the date").
- **Concept ↔ dataset linkage**: how a concept page surfaces its live rows at
  answer time (join on `categoria`).
- **Directory placement**: `datasets/` under `WIKI_PATH` vs. inside `wiki/`.
