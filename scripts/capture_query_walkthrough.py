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
# A wiki of documents and nothing else, which ships with the pre-retrieval box
# unticked. Probing it answers "what would ticking it cost here?" — deterministic
# and free, so the walkthrough can show the trade-off instead of asserting it.
PLAIN_DEMO = _PROJECT_ROOT / "examples" / "fairy-tales"
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
class PlainCase:
    """One question put to the plain wiki with the pre-retrieval box unticked."""

    act: str
    question: str
    teaches: str


# Three of the fairy-tale demo's own suggested_prompts — the questions its author
# puts in front of a newcomer. All three are about the collection rather than any
# one page, which is exactly what an agent free to search can do and a coverage
# roster built from concept names cannot recognise.
PLAIN_CASES = [
    PlainCase("The collection, not a page", "What tales are in this wiki?",
              "The agent has to find out what exists; no single page answers this."),
    PlainCase("Synthesis across pages", "What characters and themes do the tales share?",
              "Retrieval of several pages, then a comparison the sources never state."),
    PlainCase("A comparison the prompt demands be retrieved", "Compare how each story ends",
              "The system prompt forbids comparing from memory — each tale must be read first."),
]


@dataclass
class PlainTurn:
    """What the agentic path did with one of those questions.

    Captured with BOTH boxes unticked, deliberately: that isolates what the model
    does when nothing checks it. The read app's default is stricter than this
    (`Modo estricto` ships on), so the last three fields record what that default
    would have done to this very answer — computed from the run's own messages by
    the same functions the app calls, so it costs no second model call and cannot
    drift from the app's behaviour.
    """

    case: PlainCase
    answer: str = ""
    tool_calls: list[str] = field(default_factory=list)
    cited: bool = False
    # What `Modo estricto` (guardrail.enforce_grounding + postprocess.ensure_citation)
    # would have made of the same run.
    grounded: bool = False
    strict_refuses: bool = False
    strict_adds: str = ""


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


def _probe_plain_wiki() -> list[tuple[str, int, bool, str]]:
    """What ticking the pre-retrieval box would do to a wiki of documents alone.

    Runs the same gate over the plain demo's own suggested prompts — the four
    questions its author expects people to ask. No LLM: this is the deterministic
    half applied to a workspace that ships with the box unticked, so the
    walkthrough can show the cost of the other setting rather than claim it.
    """
    from domain.chat.config import load_config
    from domain.chat.preretrieval import (build_vocabulary, plan_retrieval,
                                          retrieve_collection_pages,
                                          retrieve_source_chunks, retrieve_wiki)
    from domain.chat.scope import (advisory_intent, collection_intent,
                                   is_off_limits, mentions_known_data)
    from domain.datasets.source import LocalMarkdownSource
    from domain.tools.wiki_fs import concept_page_names

    db_path = str(PLAIN_DEMO / ".llmwiki" / "index.db")
    if not Path(db_path).exists():
        return []

    cfg = load_config(PLAIN_DEMO)
    vocab = build_vocabulary(LocalMarkdownSource(PLAIN_DEMO / "datasets"))
    coverage = set(vocab) | set(concept_page_names(db_path))
    aliases = [a for names in cfg.data_aliases.values() for a in names]

    rows = []
    for q in list(cfg.suggested_prompts or []):
        off = is_off_limits(q, cfg.off_limits)
        in_roster = mentions_known_data(q, coverage, aliases)
        wiki_hits = [] if off else retrieve_wiki(db_path, q)
        doc_hits = (retrieve_source_chunks(db_path, q)
                    if (not off and not wiki_hits and in_roster) else [])
        has_data = mentions_known_data(q, vocab, aliases) or advisory_intent(q)
        collection_hits = ([] if off else
                           retrieve_collection_pages(PLAIN_DEMO) if collection_intent(q) else [])
        plan = plan_retrieval(q, off_limits=cfg.off_limits, wiki_hits=wiki_hits,
                              doc_hits=doc_hits, has_data=has_data, in_roster=in_roster,
                              collection_hits=collection_hits)
        rows.append((q, len(wiki_hits), in_roster, len(collection_hits), plan.action))
    return rows


def _gate(case: Case, cfg, db_path: str, vocab, coverage, aliases) -> Decision:
    """The deterministic half — no LLM, identical on every run."""
    from domain.chat.preretrieval import (plan_retrieval, retrieve_collection_pages,
                                          retrieve_source_chunks, retrieve_wiki)
    from domain.chat.scope import (advisory_intent, collection_intent,
                                   is_off_limits, mentions_known_data)

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

    collection_hits = ([] if d.off_limits else
                       retrieve_collection_pages(DEMO) if collection_intent(q) else [])
    plan = plan_retrieval(q, off_limits=cfg.off_limits, wiki_hits=wiki_hits,
                          doc_hits=doc_hits, has_data=d.has_data, in_roster=d.in_roster,
                          collection_hits=collection_hits)
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


async def _answer_plain(turn: PlainTurn, db_path: str, agent, language: str) -> None:
    """The unticked path — the agent is handed the question and its own tools.

    No gate, no injection, no plan: this is the model deciding for itself what
    to search for, which is the whole of the mode Part 1 describes.

    Then the same run is re-scored through the app's default post-processing,
    without invoking the model again: both are pure functions of the message
    history, so this reports what `Modo estricto` would have returned instead.
    """
    from domain.chat.guardrail import has_grounding, refusal_for
    from domain.chat.postprocess import ensure_citation
    from domain.chat.trace import _looks_cited

    result = await agent.run(turn.case.question, deps=db_path, message_history=[])
    messages = result.all_messages()
    for msg in messages:
        for part in getattr(msg, "parts", []):
            if getattr(part, "part_kind", None) == "tool-call":
                turn.tool_calls.append(getattr(part, "tool_name", "?"))
    turn.answer = result.output
    turn.cited = _looks_cited(turn.answer)

    # `Modo estricto`, replayed over this run. A run with no substantive tool
    # return is replaced wholesale by the refusal; otherwise the answer stands and
    # only a missing attribution line is appended.
    turn.grounded = has_grounding(messages)
    turn.strict_refuses = not turn.grounded
    if turn.grounded:
        strict = ensure_citation(turn.answer, messages)
        turn.strict_adds = strict[len(turn.answer):].strip() if strict != turn.answer else ""
    else:
        turn.strict_adds = refusal_for(language)


def _render_plain(turns: list[PlainTurn]) -> list[str]:
    """The Part 1 half of the appendix: the same wiki, the box left unticked."""
    if not turns:
        return []
    out = [
        "## The unticked mode, answering (live model)",
        "",
        f"Three of `{PLAIN_DEMO.name}`'s own suggested prompts, put to the agentic",
        "path: wiki search tools in the model's hands, no gate in front of it. The",
        "tools column is the point — nothing in the code decided to call those, the",
        "model did.",
        "",
        "These were captured with **both** checkboxes unticked, which is not the read",
        "app's default: `Modo estricto` ships on. The last two lines of each entry",
        "replay that default over this same run — `guardrail.has_grounding` and",
        "`postprocess.ensure_citation` are pure functions of the message history, so",
        "the replay needs no second model call and cannot disagree with the app.",
        "",
    ]
    for i, t in enumerate(turns, 1):
        answer = t.answer.strip()
        if len(answer) > _MAX_ANSWER_CHARS:
            answer = answer[:_MAX_ANSWER_CHARS].rstrip() + "\n…[truncated for the appendix]"
        if t.strict_refuses:
            strict = f"**replaces the whole answer** with `{t.strict_adds}`"
        elif t.strict_adds:
            strict = f"appends `{t.strict_adds}`"
        else:
            strict = "**leaves it exactly as it is**"
        out += [
            f"### P{i}. {t.case.act}", "",
            f"> {t.case.question}", "",
            f"*What it exercises:* {t.case.teaches}", "",
            f"- tools the model chose to call: "
            f"{', '.join(f'`{c}`' for c in t.tool_calls) or '— **none**'}",
            f"- carries a citation: **{t.cited}**",
            f"- a tool returned real evidence (`has_grounding`): **{t.grounded}**",
            f"- what `Modo estricto` would do to this answer: {strict}", "",
            "```text", answer, "```", "",
        ]
    return out


def _render(decisions: list[Decision], with_answers: bool,
            plain_turns: list[PlainTurn] | None = None) -> str:
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
        "any wiki with the box ticked has. The rest are reachable only because",
        "this one has a `datasets/` folder: without it the `data` column is `False`",
        "throughout and that branch of the chain is never taken.",
        "",
    ]

    plain = _probe_plain_wiki()
    if plain:
        out += [
            "## The same gate on a wiki of documents alone",
            "",
            f"`{PLAIN_DEMO.name}` ships with the pre-retrieval box **unticked**. These are",
            "its own `suggested_prompts` — the questions its author expects — run through",
            "the gate as if the box were ticked. Also pure code, also free to reproduce.",
            "",
            "| Question | wiki | roster | collection | plan if ticked |",
            "|---|---|---|---|---|",
        ]
        for q, hits, in_roster, coll, action in plain:
            out.append(f"| {q} | {hits} | {in_roster} | {coll} | **{action}** |")
        refused = sum(1 for *_, a in plain if a == "refuse")
        out += [
            "",
            f"{refused} of {len(plain)} would be refused. Note the `roster` column: none of",
            "these names a concept page, so the coverage roster does not cover any of them,",
            "and it never will — a question about the collection names no item. What answers",
            "them is the `collection` column: the pages whose job is to describe the whole",
            "wiki, injected directly. Before that branch existed all four refused here.",
            "",
        ]

    if not with_answers:
        return "\n".join(out) + "\n"

    out += _render_plain(plain_turns or [])
    out += ["## The ticked mode, answering (live model — wording varies run to run)", ""]
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
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--plan-only", action="store_true",
                        help="capture only the deterministic routing table (no LLM, no cost); "
                             "prints unless --out is given, so it cannot clobber the answers")
    args = parser.parse_args()
    args.out_given = args.out is not None
    if args.out is None:
        args.out = _PROJECT_ROOT / "docs" / "query_walkthrough_appendix.md"

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

    plain_turns: list[PlainTurn] = []
    plain_language = "en"
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

        # Part 1: the same engine with the box unticked, on the plain wiki. Its
        # own config and prompt — no finance overlay, no pre-retrieval block, and
        # include_wiki_tools left on, which is the whole difference.
        plain_db = str(PLAIN_DEMO / ".llmwiki" / "index.db")
        plain_agent = None
        if Path(plain_db).exists():
            plain_cfg = load_config(PLAIN_DEMO)
            plain_agent = create_agent(
                settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL,
                system_prompt=plain_cfg.system_prompt, language=plain_cfg.language,
                workspace=PLAIN_DEMO, include_wiki_tools=True,
            )
            plain_turns = [PlainTurn(case=c) for c in PLAIN_CASES]
            plain_language = plain_cfg.language
        else:
            print(f"! no index at {plain_db} — skipping the unticked capture")

        async def _run_all() -> None:
            for t in plain_turns:
                print(f"\n→ [unticked] {t.case.question}")
                await _answer_plain(t, plain_db, plain_agent, plain_language)
                print(f"   tools={t.tool_calls} cited={t.cited} "
                      f"strict={'refuses' if t.strict_refuses else (t.strict_adds or 'unchanged')}")
            for d in decisions:
                print(f"\n→ [ticked] {d.case.question}")
                await _answer(d, cfg, db_path, agent)
                print(f"   invoked={d.model_invoked} tools={d.tool_calls} cited={d.cited}")

        asyncio.run(_run_all())

    if args.plan_only and not args.out_given:
        # --plan-only produces the routing table and nothing else. Writing that
        # over the appendix would silently delete the captured answers, which is
        # a bad trade for a command whose whole appeal is that it costs nothing
        # and can be run casually. Print it instead; pass --out to write.
        print("\n" + _render(decisions, with_answers=False))
        print("[note] --plan-only printed the table instead of overwriting "
              f"{args.out.name} (its answers would be lost). Pass --out to write.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        _render(decisions, with_answers=not args.plan_only,
                plain_turns=plain_turns),
        encoding="utf-8")
    print(f"\n[OK] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
