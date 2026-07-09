#!/usr/bin/env python3
"""Run the finanzas-argentinas demo's UAT — the 9 questions from GUIA_DEMO.md.

Plain English: this asks the finance assistant the nine acceptance questions
documented in `examples/finanzas-argentinas/GUIA_DEMO.md` and checks each answer
against that guide's criteria. It exercises the whole demo end-to-end against a
live LLM: the curated wiki, the `datasets/` tables, and the deterministic
`estimar_alternativas` advisory overlay.

What each question proves (see the guide for the full write-up):

  A  concept + citation      — explains an instrument and cites its wiki page
  B  advisory (the number)   — ranks alternatives; top gain is code-computed ($90,000)
  C  a datum with its date   — quotes the MEP value verbatim with its as_of date
  D  nominal vs. real        — flags the gain as nominal, ties it to inflation/UVA·CER
  E  the honest limit        — refuses to estimate equities (renta variable)
  F  cross-comparison        — for $1.000.000 the 60-day plazo fijo yields $58,356
  G  in-topic but not loaded — admits CEDEARs aren't in this wiki (no fabrication)
  H  an absent datum         — admits it has no Comafi rate
  I  off-topic               — declines a question outside the wiki's scope

Some checks are **deterministic** (B/C/F assert the exact code-computed figures
and dates); the rest are **behavioral** (they look for the honest hedge/refusal,
which a language model phrases differently each run). It reads the pre-ingested
demo that ships in `examples/`, so no ingestion is needed — a handful of chat
calls, then a PASS / FAIL verdict.

    uv run python scripts/uat_finanzas.py            # against the bundled demo
    uv run python scripts/uat_finanzas.py --brief    # compact, one line per check
    uv run python scripts/uat_finanzas.py --workspace path/to/wiki

Exit code 0 = every check passed, 1 = a check failed, 2 = could not run (missing
LLM config, the demo isn't a finance workspace, the index is absent, etc.).

NOTE: this is an acceptance smoke test, not a benchmark. The deterministic checks
are stable; the behavioral ones can vary with a weak model — re-run if borderline.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BASE = str(_PROJECT_ROOT / "base")
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)
sys.modules.pop("config", None)  # force base/config.py to win

# Shared pure graders (also used by eval_chat_model.py + the eval packet builder).
from domain.eval.graders import (  # noqa: E402 -- after the sys.path bootstrap above
    answered_off_corpus,
    has_citation,
)

_DEFAULT_WORKSPACE = _PROJECT_ROOT / "examples" / "finanzas-argentinas"


# ── Graders ─────────────────────────────────────────────────────────────────
# Each returns (passed, human-readable detail). They read only the answer text;
# the retrieval requirement is enforced separately from the run's tool calls.

def _has_any(answer: str, *needles: str) -> bool:
    low = answer.lower()
    return any(n.lower() in low for n in needles)


def _check_concept_cited(answer: str) -> tuple[bool, str]:
    if has_citation(answer):
        return True, "explained the concept and cited a wiki page"
    return False, "stated facts without citing a wiki page"


def _check_advisory_top(answer: str) -> tuple[bool, str]:
    # $1.000.000 for 3 months: the top-ranked deterministic gain is $90,000.
    if _has_any(answer, "90,000", "90.000"):
        return True, "advisory led with the code-computed top gain ($90,000)"
    return False, "did not surface the expected $90,000 top gain"


def _check_dolar_dato(answer: str) -> tuple[bool, str]:
    has_value = _has_any(answer, "1180", "1.180", "1,180")
    has_date = _has_any(answer, "2026-06-25", "25/06/2026", "25 de junio")
    if has_value and has_date:
        return True, "quoted the MEP value with its as_of date"
    missing = "value" if not has_value else "as_of date"
    return False, f"missing the {missing}"


def _check_nominal_real(answer: str) -> tuple[bool, str]:
    if _has_any(answer, "nominal") and _has_any(
        answer, "inflación", "inflacion", "uva", "cer"
    ):
        return True, "flagged the gain as nominal and tied it to inflation (UVA/CER)"
    return False, "did not distinguish nominal from real (inflation)"


def _check_non_estimable(answer: str) -> tuple[bool, str]:
    if _has_any(
        answer,
        "no estimable",
        "no es estimable",
        "no se puede estimar",
        "no tiene una ganancia estimable",
        "renta variable",
    ):
        return True, "declined to estimate equities and explained why"
    return False, "did not flag equities as non-estimable"


def _check_comparison(answer: str) -> tuple[bool, str]:
    # For $1.000.000 at 60 days the plazo fijo (Credicoop) yields $58,356.
    if _has_any(answer, "58,356", "58.356"):
        return True, "reproduced the deterministic $58,356 for the 60-day plazo fijo"
    return False, "did not reproduce the expected $58,356 figure"


def _check_not_in_wiki(answer: str) -> tuple[bool, str]:
    # Pass = admits CEDEARs aren't in this wiki (a general aside is fine as long
    # as the absence is stated; the guide's real test is no fabrication).
    if _has_any(
        answer,
        "no pude encontrar",
        "no encontré",
        "no encontre",
        "no está en",
        "no esta en",
        "no hay información",
        "no hay informacion",
        "no se encuentra",
        "no dispongo",
        "no cuento con",
    ):
        return True, "admitted CEDEARs aren't in this wiki"
    return False, "did not admit the topic is absent from the wiki"


def _check_absent_datum(answer: str) -> tuple[bool, str]:
    if _has_any(
        answer,
        "no encontré",
        "no encontre",
        "no pude encontrar",
        "no tengo",
        "no dispongo",
        "no figura",
        "no hay datos",
    ):
        return True, "admitted it has no Comafi rate"
    return False, "did not admit Comafi's rate is missing"


def _check_offtopic(answer: str) -> tuple[bool, str]:
    if answered_off_corpus(answer):
        return False, "answered the off-topic question (said 'Paris')"
    return True, "declined the off-topic question"


# ── The nine questions (label, question, grader, requires_retrieval) ──────────
# requires_retrieval mirrors the guide: A/B/C/F/G/H quote or rank corpus facts
# (a wiki page, a dataset value, an advisory figure), so a zero-tool-call answer
# there is a fabrication. D/E/I are *conceptual* and legitimately need no tool
# call: D is the nominal-vs-real caveat (a principle the system prompt supplies —
# the ideal answer explains it without quoting a specific rate); E states equities
# are non-estimable by definition; I declines an off-topic question (the demo
# prompt permits declining without searching). Their text graders carry the signal.
_QUESTIONS = [
    ("A concepto+cita", "¿Qué es una caución bursátil y por qué se la considera de bajo riesgo?", _check_concept_cited, True),
    ("B asesor", "Tengo $1.000.000 que no necesito por 3 meses. ¿Qué alternativas tengo y cuánto ganaría?", _check_advisory_top, True),
    ("C dato+fecha", "¿A cuánto está el dólar MEP?", _check_dolar_dato, True),
    ("D nominal/real", "Si hago un plazo fijo, ¿le estoy ganando a la inflación?", _check_nominal_real, False),
    ("E no estimable", "¿Cuánto voy a ganar si compro acciones de YPF (YPFD) en 3 meses?", _check_non_estimable, False),
    ("F comparación", "Para $1.000.000, ¿me conviene un plazo fijo o un FCI money market para 60 días?", _check_comparison, True),
    ("G no en wiki", "¿Qué son los CEDEARs y conviene comprarlos?", _check_not_in_wiki, True),
    ("H dato ausente", "¿Qué tasa de plazo fijo ofrece el Banco Comafi?", _check_absent_datum, True),
    ("I fuera de tema", "¿Cuál es la capital de Francia?", _check_offtopic, False),
]


def _count_tool_calls(result) -> int:
    """How many tool calls the agent actually made during the run.

    An answer that "cites" or "refuses" with zero tool calls never retrieved
    anything — the grounding is fabricated. We trust the run's message history,
    not the answer text. Defensive about pydantic-ai versions via `part_kind`.
    """
    try:
        messages = result.all_messages()
    except Exception:  # noqa: BLE001 — never let introspection crash the UAT
        return 0
    return sum(
        1
        for message in messages
        for part in getattr(message, "parts", [])
        if getattr(part, "part_kind", None) == "tool-call"
    )


def _build_agent(workspace: Path):
    """Compose the demo agent exactly as the read app does.

    Returns (agent, db_path). Raises RuntimeError with a plain message when the
    workspace can't back the UAT (no finance overlay, or no built index).
    """
    from config import require_llm_config, settings
    from domain.chat.agent import create_agent
    from domain.chat.config import load_config
    from domain.finance_argentina.agent_tool import activate as activate_finance

    require_llm_config(
        settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL, purpose="chat"
    )

    db_path = workspace / ".llmwiki" / "index.db"
    if not db_path.exists():
        raise RuntimeError(
            f"no built index at {db_path} — this UAT needs the pre-ingested demo "
            "(examples/finanzas-argentinas ships one)"
        )

    cfg = load_config(workspace)
    fin_tools, fin_prompt = activate_finance(workspace)
    if not fin_tools:
        raise RuntimeError(
            f"{workspace} is not a finance workspace (no datasets/ advisory overlay) "
            "— point --workspace at the finanzas-argentinas demo"
        )

    agent = create_agent(
        settings.LLM_BASE_URL,
        settings.LLM_API_KEY,
        settings.LLM_MODEL,
        system_prompt=cfg.system_prompt,
        language=cfg.language,
        workspace=workspace,
        extra_tools=fin_tools,
        extra_prompt=fin_prompt,
    )
    return agent, str(db_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the finanzas-argentinas demo UAT (the 9 GUIA_DEMO.md questions).",
    )
    parser.add_argument(
        "--workspace", type=Path, default=_DEFAULT_WORKSPACE, metavar="PATH",
        help="wiki workspace to test (default: the bundled finanzas-argentinas demo)",
    )
    parser.add_argument(
        "--brief", action="store_true",
        help="compact output (one line per check)",
    )
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv()

    try:
        agent, db_path = _build_agent(args.workspace.resolve())
    except Exception as exc:  # noqa: BLE001 — surface setup problems as "couldn't run"
        print(f"✗ Could not run the UAT: {exc}")
        return 2

    from config import settings

    print(
        f"UAT · finanzas-argentinas · model {settings.LLM_MODEL}\n"
        f"workspace: {args.workspace}\n"
    )

    failures = 0
    errored = 0  # provider/transport errors (quota, auth, unreachable) — not acceptance failures
    for label, question, grader, requires_retrieval in _QUESTIONS:
        try:
            result = agent.run_sync(question, deps=db_path)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ {label}: could not get an answer: {exc}")
            errored += 1
            continue
        answer = result.output
        n_tools = _count_tool_calls(result)
        passed, detail = grader(answer)
        if requires_retrieval and n_tools == 0:
            passed = False
            detail = "answered without calling any tool — no grounding happened (any citation is fabricated)"
        if not passed:
            failures += 1
        mark = "✓" if passed else "✗"
        tag = f"[{n_tools} tool call{'' if n_tools == 1 else 's'}]"
        if args.brief:
            print(f"  {mark} {label} {tag}: {detail}")
        else:
            snippet = " ".join(answer.split())[:160]
            print(f"  {mark} {label} {tag}")
            print(f'      asked: "{question}"')
            print(f"      result: {detail}")
            print(f"      answer: {snippet}…")

    print()
    answered = len(_QUESTIONS) - errored
    # A provider error (quota/auth/unreachable) is not an acceptance failure — it
    # leaves the run incomplete, so we can't return a clean pass/fail verdict.
    if errored:
        if answered == 0:
            print("VERDICT: ? could not get any answer — the provider looks unreachable "
                  "or misconfigured (e.g. quota/key limit). Check .env and try again.")
        else:
            print(f"VERDICT: ? incomplete — {errored}/{len(_QUESTIONS)} question(s) hit a "
                  f"provider error (e.g. quota/key limit). {failures} of the {answered} that ran "
                  "failed acceptance. Re-run once the provider is available.")
        return 2
    if failures == 0:
        print(f"VERDICT: ✓ all {len(_QUESTIONS)} checks passed — the demo behaves as documented.")
        return 0
    print(f"VERDICT: ✗ {failures}/{len(_QUESTIONS)} check(s) failed. See GUIA_DEMO.md for the expected behavior.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
