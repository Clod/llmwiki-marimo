# Dataset Sources + Finance Module — Design (working doc)

> **Status:** DRAFT — collaborative working document. Decisions firm up here, then
> get distilled into `.trellis/spec/backend/datasets-format.md` (the dataset
> format) and, later, a finance‑module spec.
>
> **Scope:** the **interface / consumer** side. The **producer** (how data gets
> generated/refreshed) is **deferred** (strategic — possibly data‑as‑a‑service).

---

## 0. Guiding principle (the architecture line)

> **Be generic about data shapes; be concrete about domain logic.**

- **Generic, in the engine:** the *dataset* row format (a normalized table the
  engine parses without understanding). Cheap, reusable, makes the public tool
  better at "dynamic structured data."
- **Concrete, in a domain module:** all *logic* — eligibility, gain formulae,
  the advisory, the requirements check. Formulae are **code**, not a generic
  rules language. Trying to generalize logic is the nightmare we avoid.

Concept pages already carry **free‑form YAML front‑matter**, so the finance
module reads its own documented keys directly — **no generic "attributes
mechanism" is added to the engine.**

---

## 1. Two layers

```
┌───────────────────────────── ENGINE (generic, public) ─────────────────────────────┐
│  • DatasetSource  — read normalized dataset rows (backend-agnostic)                   │
│  • Concept pages  — markdown + free-form YAML front-matter                            │
│  • Tool hook      — conditionally register one extra agent tool                       │
└───────────────────────────────────────────────────────────────────────────────────┘
            ▲ imports                                   ▲ reads front-matter keys
┌──────────────────────── FINANCE MODULE (concrete, optional/overlay) ─────────────────┐
│  1. Concept-attribute vocabulary   (documented keys it reads)                         │
│  2. Requirements manifest (.md)     (what each category needs)  ← single source of    │
│  3. Validator / source-review tool  (checks workspace vs manifest)   truth (×3)       │
│  4. Gain formulae (CODE) + estimate_alternatives  (the advisory tool)                 │
│  5. Risk = cited prose caveat (read from concept body)                                │
└───────────────────────────────────────────────────────────────────────────────────┘
```

The engine never learns the word "plazo fijo." The module is self‑contained and
**separable** (can be kept private/optional — the fork‑avoidance seam).

---

## 2. Use cases (ground the design)

### 2.1 North‑star — the advisory query (UC‑A) — CONFIRMED

> *"Tengo $X que no voy a necesitar por Y meses. ¿Qué alternativas tengo y
> cuánto ganaría estimado con cada una?"*

Decomposes into: **enumerate** candidate categories → **filter by fit** (horizon
vs. instrument liquidity/term) → **compute estimated gain deterministically** →
**rank** → **annotate** cited risk caveats → present a **cited comparison**.

Gain math is **deterministic code in the module** (never LLM arithmetic — unsafe,
and violates "don't invent numbers").

### 2.2 Component use cases

| # | User asks (es) | Touches | Forces |
|---|----------------|---------|--------|
| UC1 | TNA plazo fijo 30d, Nación? | `plazo_fijo` (Nación,30d,TNA) | single lookup + `as_of`/`fuente` |
| UC2 | ¿Mejor banco @30d? | `plazo_fijo` @30d | filter + **rank** |
| UC3 | ¿Qué es + cuánto rinde? | concept + `plazo_fijo` | **compose** prose + numbers |
| UC4 | ¿Plazo fijo o FCI 30d? | `plazo_fijo`+`fci`+concept | **cross‑category** |
| UC5 | ¿Galicia por plazo? | `plazo_fijo` (all `plazo`) | dimension sweep |
| UC6 | $1M 30d Nación, ¿cuánto? | `plazo_fijo` TNA + formula | deterministic gain (subset of UC‑A) |
| UC7 | ¿Dólar MEP? | `dolar` (compra/venta) | two metrics per key (largo) |
| UC8 | ¿Bono AL30? *(no dataset)* | — | empty → honest, never invent |
| ~~UC9~~ | ~~¿Subió/bajó?~~ | — | **DROPPED — no history** |
| UC10 `[?]` | ¿Mover de Ualá? | `billetera`+`plazo_fijo`+holdings | personal holdings — strategic |

---

## 3. Engine seam (generic)

### 3.1 Dataset access (`DatasetSource`)

Backend‑agnostic; consumers depend only on the Protocol, never on where rows
come from (local markdown today, remote service later).

```python
@dataclass(frozen=True)
class DatasetRow:
    categoria: str; clave: str; metrica: str; valor: float
    unidad: str; dims: dict[str, str]; as_of: date; fuente: str

class DatasetSource(Protocol):
    def categories(self) -> list[str]: ...
    def query(self, categoria: str, *, clave: str | None = None,
              metrica: str | None = None,
              dims: dict[str, str] | None = None) -> list[DatasetRow]: ...
```

Backends: `LocalMarkdownSource` (now), `RemoteServiceSource` (data‑as‑a‑service).
Current‑only (no date param — no history). Storage = replace‑latest.

### 3.2 Concept pages
Markdown + **free‑form** YAML front‑matter (already exists). The engine assigns
no meaning to keys; the finance module reads the ones it documents.

### 3.3 Tool hook
The agent's tool list is conditionally extended with the module's advisory tool
when the module is active (see §6). Default 3‑tool agent unchanged otherwise.

---

## 4. Finance module (concrete)

### 4.1 Concept‑attribute vocabulary (documented, module‑owned)

Read from concept front‑matter. Engine‑neutral keys; meaning defined here.

| Attribute | Type | Meaning | Allowed / unit | Used by | Req |
|-----------|------|---------|----------------|---------|-----|
| `disponibilidad` | enum | when funds are accessible | `inmediata` \| `a_plazo` | eligibility | ✓ |
| `plazos_dias` | list[int] | available fixed terms | days | eligibility + gain | ✓ if `a_plazo` |
| `monto_minimo` | number | minimum to invest | in `moneda`; default 0 | eligibility | – |
| `moneda` | enum | currency | ARS \| USD \| … | eligibility + display | ✓ |
| `metodo_calculo` | enum | gain formula, or `no_deterministico` for variable‑return | `interes_simple_vencimiento` \| `capitalizacion_diaria` \| `no_deterministico` | gain | ✓ |
| `metrica_tasa` | str | which dataset metric is the rate | e.g. `TNA` | gain (links to dataset) | ✓ |

Concept→dataset link: implicit by slug (concept == `categoria`) + `metrica_tasa`.

### 4.2 Requirements manifest (markdown, module‑owned) — single source of truth

Declares, per category, what data the module needs. Authored in markdown so it
doubles as documentation. Sketch:

```
## plazo_fijo
dataset:                 # the datasets/plazo_fijo.md table
  metricas: [TNA]        #   must provide metric TNA (unit %)
concept:                 # the plazo_fijo concept page
  attributes: [disponibilidad, plazos_dias, moneda, metodo_calculo, metrica_tasa]

## dolar
dataset:
  metricas: [compra, venta]
concept:
  attributes: [disponibilidad, moneda, metrica_tasa]
```

**Consumed by three readers** — author (docs), validator (now), producer (spec,
deferred). One file keeps them in lockstep.

### 4.3 Validator / source‑review tool

Validates **structured md only** — **never PDFs or concept prose** (those are
conceptual/narrative, distilled by the concept pipeline; you can't require a PDF
to "contain TNA"). Scope:
- **dataset `.md` files** (transient): present per category? required `metricas`
  present & numeric?
- **concept front‑matter attributes** (structured metadata authored on the page,
  *not* the prose): required `attributes` present & well‑typed?

A **domain lint check** (fits the existing lint subsystem); reports
missing/inconsistent items and gates the advisory honestly ("can't compute
`plazo_fijo`: dataset has no `TNA`").

*(Extraction from raw PDFs = the deferred producer; the manifest is its spec.)*

### 4.4 Gain formulae (code) + advisory tool

- **Deterministic formulae as code**, keyed by `metodo_calculo`
  (`interes_simple_vencimiento`: `gain = P * TNA * n/365`; `capitalizacion_diaria`:
  compounding).
- **Non‑deterministic instruments** (`metodo_calculo: no_deterministico` — stocks,
  CEDEARs, crypto, equity FCIs): **no gain is computed.** Estimating an equity's
  future return would be inventing numbers — forbidden. The advisory flags them
  *"rendimiento variable — no estimable"* and lists them **separately** from the
  gain‑ranked options (eligibility by liquidity still applies; the return does not).
- **`estimate_alternatives(amount, horizon)`** → eligibility filter (attributes)
  → fetch rates (`DatasetSource`) → compute gain for deterministic options & rank;
  list variable‑return options separately with cited caveats → cited comparison.

> **Orthogonal axes:** liquidity (`disponibilidad`) ≠ return‑estimability
> (`metodo_calculo`). A stock is liquid (`inmediata`) yet non‑estimable.

### 4.5 Risk
Qualitative prose in the concept body, grounded in sources; surfaced as a
**cited caveat** by the advisory. Never a number, never an eligibility filter.

---

## 5. Concept ↔ dataset linkage
Implicit by slug (`categoria` == concept slug); `metrica_tasa` names the rate
metric. Composition at **answer time** (read concept + query dataset + compute);
no live values baked into concept prose.

---

## 6. Activation / optionality
Dormant unless a `datasets/` directory is present (spec §4). When active: parse
datasets, register `estimate_alternatives`, add prompt routing, lint treats
`datasets/` as expected‑stale. Guarded by `test_optionality_guard`. The finance
module itself is optional/separable.

---

## 7. Cross‑cutting — freshness, citation, empty
Always surface `as_of` + `fuente`; agent states the date. Empty category →
honest "no data". Stale → state the date, let the reader judge.

---

## 8. Deferred — the "how"
- **Producer / refresh / extraction**: manual → scheduled → remote service; the
  requirements manifest (§4.2) is its spec.
- **Data‑as‑a‑service**: `RemoteServiceSource` implementing the Protocol.
- **Storage**: parse‑on‑read vs. SQLite‑backed (replace‑latest; no history).

---

## 9. Open decisions log

| # | Decision | Lean / Outcome | Status |
|---|----------|----------------|--------|
| ARCH | Generic engine vs concrete module | generic data shapes, concrete logic in a finance module | **CONFIRMED** |
| DS‑generic | Keep dataset format generic in engine | yes | **CONFIRMED** |
| UC‑A | Advisory is v1 north star | yes | **CONFIRMED** |
| UC9 | History | current‑only | **RESOLVED — no history** |
| C1 | Constraints | module reads documented front‑matter keys; no engine mechanism | **RESOLVED** |
| C2 | Gain math | deterministic code in module | **CONFIRMED** |
| C1c | Risk | cited prose caveat, not an attribute | **RESOLVED** |
| MAN | Requirements manifest + validator | markdown manifest + domain lint check over **structured md only** (datasets + concept front‑matter attributes) — never PDFs/prose | **CONFIRMED** |
| MAN‑scope | Concept attributes in validation scope? | **datasets + concept attributes (Option B)** | **RESOLVED** |
| MAN2 | Extraction from raw PDFs | deferred to producer; manifest is its spec | proposed |
| ND | Non‑deterministic instruments (stocks/CEDEARs) | `metodo_calculo: no_deterministico` → no gain estimate; flagged variable, listed separately; eligibility by liquidity still applies | **RESOLVED** |
| C1b | Finance attribute vocabulary (§4.1) | drafted | confirm fields |
| C3 | Concept→dataset rate link | implicit slug + `metrica_tasa` | open |
| Q2.x | Tool return format / discovery | markdown table / prompt injection | open |
| UC10 | Personal holdings | separate/later | open — strategic |
