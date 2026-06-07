"""Frozen judge instructions, scoring rubric and chat probe set.

These strings are embedded verbatim into every eval packet so a judge with no
repo access can score consistently. They are FROZEN: editing the rubric or the
probe set breaks score comparability across runs — treat any change as a
deliberate re-baseline (and bump ``RUBRIC_VERSION``).
"""

from __future__ import annotations

RUBRIC_VERSION = "1.0"

# Each probe is (label, question, intent). ``intent`` ∈ {"answerable",
# "unanswerable"} tells the judge what "Appropriate response" should be: an
# answerable question expects a grounded, cited answer; an unanswerable one
# expects a refusal. The two unanswerable probes bait different failures — the
# first leaks world knowledge ("Paris"), the second a plausible fairy tale that
# simply isn't in this wiki.
CHAT_PROBES: list[tuple[str, str, str]] = [
    ("Single-source recall", "Who is Cinderella?", "answerable"),
    (
        "Cross-source synthesis",
        "What do Cinderella and Snow White have in common?",
        "answerable",
    ),
    (
        "Specific detail",
        "In Snow White, what finally wakes her from the poisoned-apple sleep?",
        "answerable",
    ),
    ("Off-corpus refusal", "What is the capital of France?", "unanswerable"),
    (
        "In-domain but absent",
        "How does Rapunzel escape from the tower?",
        "unanswerable",
    ),
]

JUDGE_INSTRUCTIONS = """\
You are grading an LLM-powered "wiki" assistant. This document contains
everything you need: the questions asked, the assistant's verbatim answers, the
wiki pages and source texts that serve as ground truth, and — for ingestion —
the source text the model was given alongside the pages it generated from it.

Rules:
- Judge using ONLY the evidence in this document. Do not rely on outside
  knowledge, except to recognise when the assistant *should have refused* — i.e.
  a question whose answer is not present anywhere in the wiki.
- Be skeptical. For every citation, verify that the cited page actually supports
  the claim. A confident answer with a wrong or missing citation scores low.
- Score each item against the rubric below on a 1–5 scale (5 = best), then fill
  in the SCORECARD at the end. Add a one-line note for any score of 3 or lower.
- If you are one of several judges, score independently; there is no answer key
  beyond what the evidence supports.
"""

RUBRIC_MD = """\
### Chat rubric (score each question 1–5)

- **Groundedness** — are the answer's claims supported by the provided evidence?
  - 5: every claim supported, nothing invented · 3: mostly, one embellished or
    unsupported claim · 1: a clear hallucination or a claim contradicting the
    evidence.
- **Citation correctness** — do citations point to pages that actually contain
  the claim?
  - 5: claims carry citations and each cited page genuinely supports it · 3:
    present but one wrong/weak page, or a claim left uncited · 1: none, or
    citations that don't support their claims.
  - *(Synthesis questions expect at least two distinct, correct citations.)*
- **Appropriate response** — answer when the wiki supports it, decline when it
  doesn't.
  - 5: answered an answerable question fully / cleanly refused an unanswerable
    one · 3: over-hedged an answerable one, or answered an unanswerable one but
    flagged uncertainty · 1: confidently answered something absent (leaked world
    knowledge), or refused something clearly present.
- **Completeness & relevance** — does it actually answer, covering the points the
  corpus offers?
  - 5: directly answers, hits the salient points · 3: thin or partly off-target ·
    1: evasive, off-topic, or misses the main point.

### Ingestion rubric (score each source document 1–5)

- **Faithfulness to source** — does the generated page state only what the source
  supports?
  - 5: no invented facts, names, or figures · 3: one minor embellishment or
    imprecise number · 1: a fabricated fact/figure or unsupported claim.
- **Coverage** — does it capture the source's key entities, events and important
  data?
  - 5: all main points, nothing important dropped · 3: the gist, but misses a
    notable entity/event · 1: misses the central point.
- **Concept quality** — are the extracted concept pages the genuinely salient
  concepts (right granularity, non-trivial, distinct, actually about the source)?
  - 5: the right salient concepts, well-scoped, distinct · 3: mostly, but one
    trivial/duplicated/mis-scoped · 1: noise, duplicates, or ungrounded concepts.
- **Structure & usefulness** — well-formed, readable, source correctly attributed?
  - 5: clean structure, readable, attributes its source · 3: usable but uneven or
    weak attribution · 1: malformed or unattributed.
"""
