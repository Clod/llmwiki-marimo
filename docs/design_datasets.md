# Dataset Sources — Interface Design (working doc)

> **Status:** DRAFT — collaborative working document. We iterate here; once a
> decision firms up it gets distilled into the executable contract at
> `.trellis/spec/backend/datasets-format.md`.
>
> **Scope:** the **interface / consumer** side of dataset sources — how the rest
> of the system reads and uses datasets. The **producer** (how `datasets/` gets
> filled) is **deferred on purpose** (strategic — possibly data-as-a-service).
>
> **Already decided** (see the spec): the on‑disk markdown format, the
> self‑describing front‑matter, the normalized row, per‑category files, opt‑in
> activation, finance as an example domain only.

---

## 1. Goal & non‑goals

**Goal:** define the stable interfaces between dataset data and its consumers
(the chat agent, the concept pages), such that the *source* of the data is
swappable.

**In scope (this doc):**
- The dataset **access interface** (how rows are read)
- The **agent tool** interface (`query_dataset`) + prompt routing
- The **concept ↔ dataset linkage**
- Activation/optionality wiring (mostly settled in the spec)
- Freshness/citation surfacing + empty/stale handling

**Out of scope (deferred — "the how"):**
- Producer / generation / refresh
- Physical storage layout (SQLite vs. parse‑on‑read)
- Data‑as‑a‑service provider contract (auth, endpoint, caching)

---

## 2. Use cases (ground the design)

> **These drive everything below.** Concrete chat questions for the
> personal‑finance example. Most of UC1–UC8 turn out to be **components of the
> north‑star use case (UC‑A)**.

### 2.1 North‑star — the advisory query (UC‑A)

> *"Tengo $X que no voy a necesitar por Y meses. ¿Qué alternativas tengo y
> cuánto ganaría estimado con cada una?"*

This is the real goal of the finance specialization. It decomposes into:

1. **Enumerate** candidate categories (plazo fijo, FCI, billetera, caución…) → generic `DatasetSource`.
2. **Filter by fit**: the horizon Y rules instruments in/out — needs each
   instrument's **term/liquidity profile** (durable, not a rate).
3. **Compute estimated gain** over Y months (amount × rate × time, correct
   compounding) — **deterministically**, never LLM arithmetic.
4. **Rank** by gain; **annotate** risk/liquidity/tax caveats from concept prose.
5. Present a **cited comparison** (option · estimated gain · `as_of` · fuente).

**Two design consequences:**
- **(a) Deterministic gain, not LLM math.** A finance tool must not let the model
  do money arithmetic (violates "don't invent numbers", unsafe). Gain is a
  **finance‑overlay computation**, exposed as a domain tool
  `estimate_alternatives(amount, horizon)` returning a computed, ranked, cited
  table the agent narrates. Confirms the engine (serves rows) / overlay
  (advisory intelligence) split.
- **(b) A three‑layer data model** the advisory tool joins:

  | Layer | Nature | Example | Lives in |
  |-------|--------|---------|----------|
  | Concept **prose** | durable narrative | what it is; **risk (qualitative, cited)** | concept page body |
  | Concept **attributes** | durable *structured* (generic typed map) | `disponibilidad`, `plazos_dias`, `monto_minimo`, `moneda` (vocab → §2.3) | concept front‑matter ← **NEW** |
  | **Datasets** | transient structured | today's TNA | `datasets/<cat>.md` |

  Attributes decide *eligibility*, datasets give *the number*, prose gives *the
  caveats* (risk included — cited, not a filter).

### 2.2 Component use cases

| # | User asks (es) | Touches | What it forces |
|---|----------------|---------|----------------|
| **UC1** | "¿TNA del plazo fijo a 30 días en Banco Nación?" | `plazo_fijo` (Nación, 30d, TNA) | single exact lookup + `as_of`/`fuente` |
| **UC2** | "¿Qué banco paga mejor a 30 días?" | `plazo_fijo`, all entidades @30d | filter + **rank** within a category |
| **UC3** | "¿Qué es un plazo fijo y cuánto rinde hoy?" | concept **+** `plazo_fijo` | **compose** prose + numbers — Seam #3 |
| **UC4** | "¿Plazo fijo o FCI money market para 30 días?" | `plazo_fijo` + `fci` + concept | **cross‑category** data + concept reasoning |
| **UC5** | "¿Cómo varía en Galicia según el plazo?" | `plazo_fijo` (Galicia, all `plazo`) | enumerate a **dimension** for one key |
| **UC6** | "$1.000.000 a 30 días en Nación, ¿cuánto cobro?" | `plazo_fijo` TNA + derivation | **deterministic gain** (subset of UC‑A) |
| **UC7** | "¿A cuánto está el dólar MEP?" | `dolar` (MEP, compra/venta) | **two metrics per key** (largo) |
| **UC8** | "¿Cuánto rinde el bono AL30?" *(no dataset)* | — (absent) | **empty** → honest, never invent |
| ~~UC9~~ | ~~"¿Subió o bajó esta semana?"~~ | — | **DROPPED — no history; current‑only** |
| **UC10** `[?]` | "Tengo plata en Ualá, ¿la muevo?" | `billetera` + `plazo_fijo` + **holdings** | where do **personal holdings** live? (strategic/privacy) |

**Still open:** UC10 (personal holdings — separate private layer, or out of v1?).

### 2.3 Concept attributes — generic mechanism + finance vocabulary

Two levels, to keep the engine domain‑neutral:

**Engine (generic — to be documented in the spec):** a concept page may carry a
typed `attributes` map in its front‑matter. The engine parses and exposes it but
assigns **no meaning** to any key (same stance as `categoria` for datasets).

```
---
tags: [entity]
sources: [...]
attributes:
  disponibilidad: a_plazo
  plazos_dias: [30, 60, 90, 180, 365]
  monto_minimo: 1000
  moneda: ARS
  metodo_calculo: interes_simple_vencimiento
  metrica_tasa: TNA
---
# Plazo Fijo
... prose, including risk discussion with citations ...
```

**Finance overlay vocabulary (the page‑authoring contract — to be documented thoroughly):**

| Attribute | Type | Meaning | Allowed / unit | Used by | Req |
|-----------|------|---------|----------------|---------|-----|
| `disponibilidad` | enum | when funds are accessible | `inmediata` (T+0) \| `a_plazo` (locked) | eligibility (horizon) | ✓ |
| `plazos_dias` | list[int] | available fixed terms | days | eligibility + gain | ✓ if `a_plazo` |
| `monto_minimo` | number | minimum to invest | in `moneda`; default 0 | eligibility (amount) | – |
| `moneda` | enum | instrument currency | ARS \| USD \| … | eligibility + display | ✓ |
| `metodo_calculo` | enum | deterministic gain formula | `interes_simple_vencimiento` \| `capitalizacion_diaria` \| … | gain | ✓ |
| `metrica_tasa` | str | which dataset metric is the applicable rate | e.g. `TNA`, `rendimiento` | gain (links to dataset) | ✓ |

- **Dataset link:** implicit by slug (concept slug == `categoria`); `metrica_tasa`
  names which metric is the rate. (An explicit `dataset:` key overrides only if
  the slug differs.)
- **Risk is NOT an attribute** — qualitative prose in the body, grounded in
  sources; the advisory surfaces it as a **cited caveat**, never a number/filter.
- **Why this split:** the engine stays domain‑neutral (any overlay can define its
  own attributes); the *finance* vocabulary is thoroughly documented so a human
  (or a generator) knows exactly what to fill for the advisory to work.

---

## 3. Architecture & the seams

```
   [ PRODUCER ]            ← DEFERRED (local files | scheduled job | remote service)
        │  fills
        ▼
  ┌─────────────────┐
  │  DatasetSource  │      ← SEAM #1 (this doc) — backend-agnostic access interface
  │  (Protocol)     │
  └─────────────────┘
        │  normalized rows
        ▼
  ┌─────────────────┐      ┌──────────────────────────┐
  │  query_dataset  │◄─────│  chat agent + system      │   ← SEAM #2 (this doc)
  │  (agent tool)   │      │  prompt routing           │
  └─────────────────┘      └──────────────────────────┘
        ▲
        │ joins on `categoria`
  ┌─────────────────┐
  │  concept page   │      ← SEAM #3 (this doc) — concept ↔ dataset linkage
  └─────────────────┘
```

**Key principle — backend‑agnostic source.** Everything above `DatasetSource`
depends only on the Protocol, never on where the rows come from. That is what
keeps "supply data as a service later" a drop‑in, not a rewrite.

---

## 4. Seam #1 — Dataset access interface (`DatasetSource`)

The repository‑style boundary. A consumer asks for rows by category/filter and
gets back normalized rows; it never knows the backend.

**Proposed (for discussion):**

```python
from typing import Protocol
from dataclasses import dataclass
from datetime import date

@dataclass(frozen=True)
class DatasetRow:
    categoria: str
    clave: str
    metrica: str
    valor: float
    unidad: str
    dims: dict[str, str]
    as_of: date
    fuente: str

class DatasetSource(Protocol):
    def categories(self) -> list[str]: ...
    def query(
        self,
        categoria: str,
        *,
        clave: str | None = None,
        metrica: str | None = None,
        dims: dict[str, str] | None = None,
    ) -> list[DatasetRow]: ...
```

Backends (all implement the Protocol):
- `LocalMarkdownSource` — parses `datasets/*.md` (today).
- `RemoteServiceSource` — calls an external API (data‑as‑a‑service, later).

**OPEN questions:**
- **Q1.1** Return bare `list[DatasetRow]`, or a small result object with helpers
  (e.g. `.latest()`, `.by_metric()`)? *(lean: bare rows now, keep it simple)*
- **Q1.2** Category‑scoped only, or allow cross‑category queries (UC4)? *(lean:
  category‑scoped; aggregation is a higher layer)*
- **Q1.3** Does the Protocol hide *all* storage (parse‑on‑read vs. SQLite‑backed
  vs. remote), so storage stays a pure "how"? *(lean: yes — that's the point)*
- ~~Q1.4 date/range param~~ — **RESOLVED: current‑only, no time dimension** (no history).

---

## 5. Seam #2 — Agent tool (`query_dataset`) + prompt routing

A new PydanticAI tool, **conditionally registered** only when the workspace has
datasets (spec §4), so the default 3‑tool agent is unchanged.

**Proposed tool surface the LLM sees:**

```python
def query_dataset(categoria: str, clave: str | None = None,
                  metrica: str | None = None) -> str:
    """Look up current structured values for a category (e.g. a rate or price).
    Returns exact values with their unit, as_of date, and source."""
```

Return: a compact, citation‑bearing rendering (clave · metrica · valor+unidad ·
`as_of` · fuente) the model can quote verbatim.

**Prompt routing addition (only when active):**
> For current quantitative values, call `query_dataset` and **always state the
> `as_of` date**. Never infer a number from concept‑page prose.

**OPEN questions:**
- **Q2.1** How does the agent discover which categories exist + their shape?
  (a) inject the active categories + a one‑line schema into the system prompt at
  startup; (b) a `list_datasets()` tool. *(lean: (a) — cheaper, deterministic)*
- **Q2.2** Tool return format: compact markdown table vs. structured text lines?
  *(lean: small markdown table — readable + quotable)*
- **Q2.3** Ranking (UC2) / cross‑category (UC4): does the tool do it, or does the
  agent fetch rows and reason? *(lean: tool returns rows, agent reasons/ranks)*

---

## 6. Seam #3 — Concept ↔ dataset linkage

How the "Plazo Fijo" concept page connects to `datasets/plazo_fijo.md` (UC3, UC4).

**Proposed:** **implicit by slug** — `categoria == concept slug` is the join.
Composition happens **at answer time**: when a question touches a concept that
has a matching dataset category, the agent reads the concept page (what/why) and
calls `query_dataset` (current numbers) and composes a cited answer.

We explicitly **do not** inject live values into concept‑page prose (that
reintroduces staleness and fights the merge logic).

**OPEN questions:**
- **Q3.1** Implicit slug match vs. explicit `datasets: [plazo_fijo]` front‑matter
  on the concept page? *(lean: implicit; explicit only if slugs ever diverge)*
- **Q3.2** Should the viewer show a "live values" panel next to a concept page,
  or keep it chat‑only for now? *(lean: chat‑only first; viewer later)*

---

## 7. Seam #4 — Activation / optionality (settled; here for completeness)

Per spec §4: dormant unless a `datasets/` directory is present. Detection drives
(a) conditional `query_dataset` registration, (b) prompt routing lines, (c) lint
treating `datasets/` as expected‑stale. Guarded by `test_optionality_guard`.

**OPEN questions:**
- **Q4.1** Trigger = presence of `datasets/` (lean) vs. explicit
  `[datasets] enabled` in `wiki_config.toml`?

---

## 8. Cross‑cutting — freshness, citation, empty/stale

- **Always surface `as_of` + `fuente`** with any value; the agent states the date.
- **Empty (UC8):** no rows for a category → honest "no dataset for X" (no guessing).
- **Stale:** if `as_of` is old, optionally flag it. *(OPEN Q5.1: staleness
  threshold, or just always state the date and let the reader judge? lean:
  always state the date.)*

---

## 9. Deferred — the "how" (producer / storage / service)

Designed so these are swappable behind `DatasetSource`:
- **Producer**: manual markdown → scheduled `marimo-batch` → remote service.
- **Data‑as‑a‑service**: `RemoteServiceSource` implementing the Protocol; auth,
  endpoint, caching, rate limits — all TBD, none visible to consumers.
- **Storage**: parse‑on‑read vs. SQLite‑backed. **Replace‑latest (DECIDED — no
  history; no time‑series).**
- **Advisory overlay** (finance): `estimate_alternatives(amount, horizon)` —
  deterministic eligibility + gain math + ranking, on top of `DatasetSource` +
  concept attributes. Domain code, not engine.

---

## 10. Open decisions log

| # | Decision | Lean | Status |
|---|----------|------|--------|
| UC9 | History vs. current‑only | current‑only | **RESOLVED — no history** |
| UC‑A | Advisory query is the v1 north star | yes | **CONFIRMED** |
| C1a | Constraint mechanism | generic typed `attributes` map (engine) | **RESOLVED** |
| C1b | Finance attribute vocabulary | drafted in §2.3 | proposed — confirm fields |
| C1c | Risk handling | cited prose caveat, not an attribute | **RESOLVED** |
| C2 | Gain math: deterministic vs LLM | deterministic overlay tool | **CONFIRMED** |
| C3 | Concept→dataset rate link | implicit slug + `metrica_tasa` | open |
| UC10 | Personal holdings in scope? where? | separate/later | open — strategic |
| Q1.1 | Access return type | bare rows | open |
| Q1.2 | Cross‑category queries | category‑scoped | open |
| Q1.3 | Protocol hides all storage | yes | open |
| Q2.1 | Category discovery for the LLM | prompt injection | open |
| Q2.2 | Tool return format | markdown table | open |
| Q2.3 | Ranking/cross‑category: tool vs agent | agent reasons | open |
| Q3.1 | Concept↔dataset link | implicit slug | open |
| Q3.2 | Viewer "live values" panel | chat‑only first | open |
| Q4.1 | Activation trigger | `datasets/` presence | open |
| Q5.1 | Staleness handling | always state date | open |
