# Half-automated eval packet generator

## Goal

Produce a tool that generates a **self-contained, paste-ready evaluation packet** (a
single markdown file) for an LLM Wiki. The packet bundles the system-under-test's
own outputs together with the ground-truth evidence and a frozen scoring rubric,
so that **any chat LLM** (a free Gemini / ChatGPT / Claude web tab) can act as the
judge by reading the packet and filling in a scorecard.

This is the **automated half of Part B (UAT)**: the generation of the evaluation
material is automated; the judging stays human-in-the-loop (copy/paste to one or
more judges). It is explicitly **not a strict regression test** — it is a
*measurement instrument* for the quality of the two models the wiki engine uses:

- the **chat model** (`LLM_MODEL`) — answering questions in `read_app`
- the **ingestion model** (`WIKI_LLM_MODEL`) — writing wiki pages in `ingest_app`

## Background / why this shape

`scripts/eval_chat_model.py` already proves the seam: it drives the real agent
(`agent.run_sync(question, deps=db_path).output`) against the frozen golden corpus
and grades answers. Its graders are **regex/string** functions — they catch gross
failures ("said Paris", "no citation-shaped string") but cannot judge whether a
citation points at the *right* page or whether an answer is *faithful* to its
source. An LLM judge closes that gap.

Going **half-automated** (generate a packet, paste to free judges) instead of
fully-automated (call a judge API) was a deliberate choice: it needs no judge API
keys, costs nothing to judge, gives judge **diversity** for free, and avoids
self-grading bias. The trade-off accepted: it is **not CI-gateable**. The packet
composes forward — the same artifact can later be POSTed to an API judge if a
gated version is ever wanted (see Non-goals / Future).

## Requirements

### Functional

1. **One command, one markdown file.** A `scripts/build_eval_packet.py` entrypoint
   that writes a single self-contained `.md` to a gitignored `eval_reports/`
   directory, with model names + corpus hash + timestamp in the filename.
2. **Two sections in the packet:**
   - **Part 1 — Chat grounding & citations.** For each probe question, run the
     live chat agent (`LLM_MODEL`) against the target wiki and capture the verbatim
     answer. Inline the wiki page(s) the answer cited (and source text where needed)
     so the judge can verify citations against ground truth.
   - **Part 2 — Ingestion faithfulness & coverage.** For each source document,
     inline the **extracted source text the model was given** alongside the
     **generated** summary + concept pages, so the judge checks output-faithful-to-input.
3. **Target selection (`--wiki PATH`).** Defaults to the **frozen golden corpus**
   (the standardized 4-fairy-tale benchmark). When pointed at a real wiki, it reads
   that wiki's DB + pages + sources and emits a packet for it (ad-hoc quality check).
4. **Ingestion section measures the *current* model.** In benchmark mode, re-ingest
   the 4 golden PDFs with the current `WIKI_LLM_MODEL` into a temp wiki and report
   on those fresh pages (reuse the batch-ingest path). In `--wiki` mode, report on
   the existing pages of the given wiki (no re-ingest).
5. **Embed the cheap auto-checks.** Run the existing regex graders from
   `eval_chat_model.py` and show their verdict inline per chat probe, as a free
   pre-screen the judge sees alongside the rubric.
6. **Embedded judge instructions + frozen rubric.** The packet opens with explicit
   judge instructions ("use ONLY the evidence below; verify every citation") and the
   full rubric text, so it is self-contained for a judge with no repo access.
7. **Quantitative scorecard template** at the end for the judge to fill — designed so
   multiple judges' tables can be tabulated/averaged and tracked across model swaps.
8. **Provenance header** — `LLM_MODEL`, `WIKI_LLM_MODEL`, corpus identity + content
   hash, and generation date, so any two packets are comparable.

### The rubric (frozen — acceptance content)

Scale: **1–5, anchored** (anchors keep independent judges aligned and make scores
averageable across judges/models). Each section also gets a holistic **Overall
(1–5)** and a free-text **Notes** field. The crude binary checks (Paris leak,
citation-shaped string) are **not** in the rubric — they live in the auto-checks
block — so judge attention is reserved for what only a judge can assess.

**Chat rubric (scored per question):**

- **Groundedness** — are the answer's claims supported by the provided evidence?
  - 5: every claim supported, nothing invented · 3: mostly, one embellished/unsupported
    claim · 1: a clear hallucination or a claim contradicting the evidence.
- **Citation correctness** — do citations point to pages that actually contain the claim?
  - 5: claims carry citations and each cited page genuinely supports it · 3: present but
    one wrong/weak page, or a claim left uncited · 1: none, or citations that don't
    support their claims. *(Synthesis questions expect ≥2 distinct correct citations.)*
- **Appropriate response** — answer when the wiki supports it, decline when it doesn't.
  - 5: answered an answerable Q fully / cleanly refused an unanswerable one · 3:
    over-hedged, or answered-but-flagged-uncertainty · 1: confidently answered something
    absent (leaked world knowledge), or refused something clearly present.
- **Completeness & relevance** — does it actually answer, covering the points the corpus offers?
  - 5: directly answers, hits the salient points · 3: thin or partly off-target · 1:
    evasive, off-topic, or misses the main point.

**Ingestion rubric (scored per source document):**

- **Faithfulness to source** — does the generated page state only what the source supports?
  - 5: no invented facts, names, or figures · 3: one minor embellishment or imprecise
    number · 1: a fabricated fact/figure or unsupported claim. *(Number hallucination is
    the classic failure — figures are called out on purpose.)*
- **Coverage** — does it capture the source's key entities, events, and important data?
  - 5: all main points, nothing important dropped · 3: gist but misses a notable
    entity/event · 1: misses the central point.
- **Concept quality** — are the extracted concept pages the genuinely salient concepts
  (right granularity, non-trivial, distinct, actually about the source)?
  - 5: right salient concepts, well-scoped, distinct · 3: mostly, but one
    trivial/duplicated/mis-scoped · 1: noise, duplicates, or ungrounded concepts.
- **Structure & usefulness** — well-formed, readable, source correctly attributed?
  - 5: clean structure, readable, attributes its source · 3: usable but uneven or weak
    attribution · 1: malformed or unattributed.

### Chat probe set (initial)

Each probe is tagged **answerable** or **unanswerable** so the judge knows what
"Appropriate response" should be. Initial set (extends the 3 in `eval_chat_model.py`):

- *answerable, single-source*: "Who is Cinderella?"
- *answerable, synthesis*: "What do Cinderella and Snow White have in common?"
- *answerable, detail/figure*: a question targeting a specific detail in one tale.
- *unanswerable, off-corpus*: "What is the capital of France?"
- *unanswerable, in-domain-but-absent*: a question about a fairy tale **not** in the
  four (e.g. Rapunzel) — baits world knowledge harder than the France probe.

Probe list lives in code as `(label, question, intent)` and is frozen alongside the rubric.

## Acceptance Criteria

- [ ] `uv run python scripts/build_eval_packet.py` against the golden corpus writes a
      single self-contained `.md` under `eval_reports/` (gitignored).
- [ ] The packet contains: provenance header, judge instructions, frozen rubric,
      Part 1 (chat probes with verbatim answers + inlined cited evidence + auto-checks),
      Part 2 (per-source: extracted source text + generated pages), and a blank
      quantitative scorecard.
- [ ] `--wiki PATH` targets an arbitrary existing wiki and emits a packet without
      re-ingesting; default (no flag) uses the frozen golden corpus and re-ingests for
      Part 2 with the current `WIKI_LLM_MODEL`.
- [ ] Pasting the packet into a fresh chat LLM yields a filled scorecard with no
      missing evidence (manually verified once for the golden corpus).
- [ ] The header records both engine models + a corpus content hash.
- [ ] Skips cleanly with an actionable message when the golden corpus isn't frozen or
      LLM config is missing (mirror `eval_chat_model.py` / `golden_available()`).
- [ ] **Pure assembly logic is unit-tested** in the fast gate (`tests/unit/`):
      templating, evidence selection, truncation, hashing, scorecard rendering,
      auto-check embedding — no network. Live chat/ingestion calls are opt-in and NOT
      in `tests/unit` or `tests/regression`.
- [ ] `ruff` clean; new unit tests pass under `uv run pytest tests/unit tests/regression`.

## Technical Notes

- **Reuse, don't reinvent:** `tests/helpers/golden.py` (`golden_available`,
  `restore_golden`), `domain.chat.agent.create_agent` + `run_sync` (chat driver),
  the batch-ingest path under `base/domain/ingestion/batch.py` (Part 2 re-ingest),
  and the regex graders in `scripts/eval_chat_model.py` (auto-checks — import, don't
  copy; refactor those graders to an importable location if needed).
- **Determinism split (project convention):** the packet *assembly* is pure and lives
  in a small importable module so it can be unit-tested deterministically; the script
  shell does the live calls. Same philosophy as `eval_chat_model.py` (pure graders +
  thin `main`).
- **Self-containment vs token budget:** the golden corpus (4 short tales) fits easily
  in a judge context window. Bound per-document excerpt sizes so a large `--wiki`
  target degrades gracefully (truncate with an explicit "[excerpt truncated]" marker
  rather than emitting a 200k-token file).
- **Evidence shapes differ by section:** Part 1 ground truth = the wiki pages the
  answer cited (+ source text to catch wrong citations); Part 2 ground truth = the
  **extracted** source text (what the model saw), not the PDF binary.
- **Output:** `eval_reports/` added to `.gitignore`; filenames like
  `eval_<chatmodel>_<wikimodel>_<corpushash>_<YYYYMMDD-HHMM>.md`.
- **Rubric + probe set are frozen in the repo** (a constant/module) and embedded into
  every packet; changing either breaks score comparability, so treat edits as a
  deliberate re-baseline.

## Non-goals / Future

- **Not a regression test, not a CI gate.** Scores drift with provider/model updates;
  this is a thresholded *measurement*, audited by a human.
- **No API judge in v1.** The packet is judged by copy/paste. Future: an optional
  `--judge` mode that POSTs the same packet to a pinned `EVAL_JUDGE_*` model for a
  gated, automated verdict (the full-auto Part B). v1 must not preclude this.
- **No automatic score aggregation in v1.** A later helper can ingest filled
  scorecards (paste-back) and emit per-model averages / trend tables.
- **Lint/repair finding-quality (B5)** is out of scope for v1 (chat + ingestion only).
