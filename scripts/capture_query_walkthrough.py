#!/usr/bin/env python3
"""Ask the assistant a spectrum of questions and capture how it decided each one.

Plain English: the counterpart to `capture_ingestion_walkthrough.py`. Where that
one records what ingestion *builds*, this one records what a query *does* — which
retrieval path the code chose before the model saw anything, which tools ran, and
whether the answer survived the grounding check. It exists so
`docs/query_walkthrough.md` can be regenerated from a real run instead of
hand-maintained.

Two halves, deliberately separated:

  * The **gate table** is pure code — `mentions_known_data`, `is_off_limits`,
    the FTS hits and `plan_retrieval` — so it is deterministic, free, and the
    same on every run. That is the interesting half: the routing decision is
    made *before* the model is consulted, and often instead of consulting it.
  * The **answers** need a live LLM and vary in wording run to run. Never assert
    on their prose; read them for behaviour (does it cite? does it refuse?).

    uv run python scripts/capture_query_walkthrough.py             # both halves
    uv run python scripts/capture_query_walkthrough.py --plan-only # no LLM, no cost
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "base"))

DEMO = _PROJECT_ROOT / "examples" / "finanzas-argentinas"
_MAX_ANSWER_CHARS = 1200          # the advisory table is ~40 rows; keep the appendix readable


@dataclass
class Case:
    """One question, and the mechanism it is meant to exercise."""

    act: str
    question: str
    teaches: str
    # True when the case only exists because the wiki has a datasets/ folder —
    # the has_data branch, the dataset vocabulary, the finance advisory tools.
    needs_datasets: bool = False


# Ordered so the appendix reads the way the walkthrough does: the mechanisms
# every wiki gets first, then the ones a datasets/ folder adds. Reordering this
# list reorders the appendix, so the two cannot drift apart.
CASES = [
    # ── Any wiki ──────────────────────────────────────────────────────────────
    Case("A curated page answers", "¿Qué es una caución bursátil y por qué se la considera de bajo riesgo?",
         "Tier-1: the code injects the curated page; the answer cites it."),
    Case("In scope, not covered", "¿Qué es un ETF?",
         "The roster gate refuses without reading raw sources — no tangential chunk to leak from."),
    Case("Off topic", "¿Cuál es la capital de Francia?",
         "Refused deterministically, without invoking the model at all."),
    # ── Only reachable when the wiki has a datasets/ folder ───────────────────
    Case("A datum, with its date", "¿A cuánto está el dólar MEP?",
         "The value comes from a dataset via query_dataset, quoted with its as_of date.",
         needs_datasets=True),
    Case("An alias reaches the datum", "¿A cuánto está el billete verde?",
         "The vocabulary built at ingest lets a nickname resolve to the same dataset.",
         needs_datasets=True),
    Case("Deterministic advisory", "Tengo $1.000.000 que no necesito por 3 meses, ¿qué alternativas tengo y cuánto ganaría?",
         "The ranking and every gain are computed in Python; the model only narrates.",
         needs_datasets=True),
    Case("The honest limit", "¿Cuánto ganaría con acciones de YPF?",
         "Variable-return instruments are declared non-estimable instead of guessed.",
         needs_datasets=True),
]


@dataclass
class Decision:
    """What the code decided before (or instead of) calling the model."""

    case: Case
    off_limits: bool = False
    has_data: bool = False
    in_roster: bool = False
    wiki_hits: int = 0
    doc_hits: int = 0
    action: str = ""
    tier: str = ""
    answer: str = ""
    tool_calls: list[str] = field(default_factory=list)
    model_invoked: bool = False
    refusal_substituted: bool = False
    cited: bool = False


def _gate(case: Case, cfg, db_path: str, vocab, coverage, aliases) -> Decision:
    """The deterministic half — no LLM, identical on every run."""
    from domain.chat.preretrieval import plan_retrieval, retrieve_source_chunks, retrieve_wiki
    from domain.chat.scope import advisory_intent, is_off_limits, mentions_known_data

    q = case.question
    d = Decision(case=case)
    d.off_limits = is_off_limits(q, cfg.off_limits)
    d.has_data = mentions_known_data(q, vocab, aliases) or advisory_intent(q)
    d.in_roster = mentions_known_data(q, coverage, aliases)

    wiki_hits = [] if d.off_limits else retrieve_wiki(db_path, q)
    doc_hits = (
        retrieve_source_chunks(db_path, q)
        if (not d.off_limits and not wiki_hits and d.in_roster) else []
    )
    d.wiki_hits, d.doc_hits = len(wiki_hits), len(doc_hits)

    plan = plan_retrieval(q, off_limits=cfg.off_limits, wiki_hits=wiki_hits,
                          doc_hits=doc_hits, has_data=d.has_data, in_roster=d.in_roster)
    d.action, d.tier = plan.action, plan.tier or "—"
    return d


async def _answer(d: Decision, cfg, db_path: str, agent) -> None:
    """The live half — one turn through the real pre-retrieval engine."""
    from domain.chat.preretrieval import pre_retrieval_answer
    from domain.chat.trace import _looks_cited

    async def run_agent(prompt, history):
        return await agent.run(prompt, deps=db_path, message_history=history)

    def on_trace(*, raw, final, result, refusal_substituted):
        d.model_invoked = result is not None
        d.refusal_substituted = refusal_substituted
        if result is not None:
            for msg in result.all_messages():
                for part in getattr(msg, "parts", []):
                    if getattr(part, "part_kind", None) == "tool-call":
                        d.tool_calls.append(getattr(part, "tool_name", "?"))

    d.answer = await pre_retrieval_answer(
        d.case.question, config=cfg, db_path=db_path, workspace=DEMO,
        history=[], language=cfg.language, run_agent=run_agent, on_trace=on_trace,
    )
    d.cited = _looks_cited(d.answer)


def _render(decisions: list[Decision], with_answers: bool) -> str:
    out = [
        "<!-- GENERATED by scripts/capture_query_walkthrough.py — do not edit by hand. -->",
        "# Appendix — how each question was routed, captured from a real run",
        "",
        "Regenerate with:",
        "",
        "```bash",
        "uv run python scripts/capture_query_walkthrough.py",
        "```",
        "",
        "## The routing decision (deterministic — no model involved)",
        "",
        "`off_limits` and `roster` come from the wiki's own vocabulary; `wiki`/`docs`",
        "are FTS hit counts; `plan` is what `plan_retrieval` returned. Every column",
        "here is computed by code before the model is consulted — and for the last two",
        "rows, instead of consulting it.",
        "",
        "| # | Question | off_limits | data | roster | wiki | docs | plan |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, d in enumerate(decisions, 1):
        q = d.case.question if len(d.case.question) <= 60 else d.case.question[:57] + "…"
        tier = "" if d.tier == "—" else f" ({d.tier})"
        out.append(
            f"| {i} | {q} | {d.off_limits} | {d.has_data} | {d.in_roster} "
            f"| {d.wiki_hits} | {d.doc_hits} | **{d.action}**{tier} |"
        )
    out += [
        "",
        f"Rows 1–{sum(1 for d in decisions if not d.case.needs_datasets)} are mechanisms "
        "any wiki has. The rest are reachable only because this one has a",
        "`datasets/` folder: without it the `data` column is `False` throughout and that",
        "branch of the chain is never taken.",
    ]
    out.append("")

    if not with_answers:
        return "\n".join(out) + "\n"

    out += ["## The answers (live model — wording varies run to run)", ""]
    for i, d in enumerate(decisions, 1):
        answer = d.answer.strip()
        truncated = len(answer) > _MAX_ANSWER_CHARS
        if truncated:
            answer = answer[:_MAX_ANSWER_CHARS].rstrip() + "\n…[truncated for the appendix]"
        out += [
            f"### {i}. {d.case.act}", "",
            f"> {d.case.question}", "",
            f"*What it exercises:* {d.case.teaches}", "",
            f"- model invoked: **{d.model_invoked}**",
            f"- tools called: {', '.join(f'`{t}`' for t in d.tool_calls) or '—'}",
            f"- answer replaced by the guardrail: **{d.refusal_substituted}**",
            f"- carries a citation: **{d.cited}**", "",
            "```text", answer, "```", "",
        ]
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path,
                        default=_PROJECT_ROOT / "docs" / "query_walkthrough_appendix.md")
    parser.add_argument("--plan-only", action="store_true",
                        help="capture only the deterministic routing table (no LLM, no cost)")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")

    from domain.chat.config import load_config
    from domain.chat.preretrieval import build_vocabulary
    from domain.datasets.source import LocalMarkdownSource
    from domain.tools.wiki_fs import concept_page_names

    db_path = str(DEMO / ".llmwiki" / "index.db")
    if not Path(db_path).exists():
        print(f"✗ no index at {db_path} — this needs the pre-ingested demo")
        return 2

    cfg = load_config(DEMO)
    aliases = [a for names in cfg.data_aliases.values() for a in names]
    vocab = build_vocabulary(LocalMarkdownSource(DEMO / "datasets"))
    coverage = set(vocab) | set(concept_page_names(db_path))

    decisions = [_gate(c, cfg, db_path, vocab, coverage, aliases) for c in CASES]
    for d in decisions:
        print(f"  {d.action:<7} {d.tier:<7} {d.case.question[:52]}")

    if not args.plan_only:
        from openai import OpenAI  # noqa: F401 — imported by create_agent's provider
        from config import require_llm_config, settings
        from domain.chat.agent import create_agent
        from domain.finance_argentina.agent_tool import activate as activate_finance

        require_llm_config(settings.LLM_BASE_URL, settings.LLM_API_KEY,
                           settings.LLM_MODEL, purpose="the query walkthrough")
        fin_tools, fin_prompt = activate_finance(DEMO)
        preret_prompt = (
            "\n\n## Modo pre-retrieval\n"
            "NO tenés herramientas de búsqueda de wiki: las páginas relevantes ya "
            "vienen inyectadas en el CONTEXTO. Respondé exclusivamente desde ese "
            "contexto, citando la fuente. Las herramientas de datos (query_dataset) "
            "y de cálculo (estimar_alternativas) siguen disponibles."
        )
        agent = create_agent(
            settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL,
            system_prompt=cfg.system_prompt, language=cfg.language, workspace=DEMO,
            extra_tools=fin_tools, extra_prompt=(fin_prompt or "") + preret_prompt,
            include_wiki_tools=False,
        )

        async def _run_all() -> None:
            for d in decisions:
                print(f"\n→ {d.case.question}")
                await _answer(d, cfg, db_path, agent)
                print(f"   invoked={d.model_invoked} tools={d.tool_calls} cited={d.cited}")

        asyncio.run(_run_all())

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(_render(decisions, with_answers=not args.plan_only), encoding="utf-8")
    print(f"\n[OK] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
