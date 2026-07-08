#!/usr/bin/env python3
"""Smoke-test whether your chosen *chat* model is good enough for this wiki.

Plain English: this asks the assistant a few fixed questions about a ready-made
sample wiki (four fairy tales) and checks the answers for the failures a weak
model makes:

  1. It answers questions that aren't in the wiki at all — it should search,
     find nothing, and refuse (a refusal reached without searching is a guess,
     not grounding).
  2. It states facts without showing where they came from (it should cite a page).
  3. It "cites" a page without ever calling a retrieval tool — a citation
     fabricated from memory (often copied from the prompt's own example paths).
     Every question requires a real tool call, checked against the run's message
     history — not just that the answer text looks cited.

By default it validates every model configured in your .env — the chat model
(LLM_MODEL) and, when set to a different client, the wiki-generation model
(WIKI_LLM_MODEL). It does NOT need you to ingest anything — it reads a frozen
sample wiki that ships with the project. A few cheap chat calls, then a
PASS / FAIL verdict.

    uv run python scripts/eval_chat_model.py            # configured model(s)
    uv run python scripts/eval_chat_model.py --model X  # a specific model id
    uv run python scripts/eval_chat_model.py --brief    # compact output

Exit code 0 = good enough, 1 = a check failed (a model likely too weak), 2 =
could not run (missing LLM config, sample wiki, etc.).

NOTE: this is a smoke test, not a benchmark. It reliably catches "this model is
too weak", not fine differences between two good models. Run it a couple of times
if a result looks borderline — the model is non-deterministic.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BASE = str(_PROJECT_ROOT / "base")
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)
# Expose the project root too, so `tests.helpers.golden` (the frozen sample-wiki
# loader) imports when this is run as a script (where sys.path[0] is scripts/, not
# the repo root). Append, not insert — base/ must still win for the `config` module.
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.append(str(_PROJECT_ROOT))
sys.modules.pop("config", None)  # force base/config.py to win

# The pure citation/leak graders now live in domain.eval.graders so the eval
# packet builder can share them. Re-exported here so existing callers and tests
# keep importing them from this module.
from domain.eval.graders import (  # noqa: E402 -- after the sys.path bootstrap above
    answered_off_corpus,
    citation_count,
    has_citation,
)


# ── The checks ────────────────────────────────────────────────────────────────

# (label, question, grader(answer) -> (passed, detail))
def _check_refusal(answer: str) -> tuple[bool, str]:
    if answered_off_corpus(answer):
        return False, "answered a question that isn't in the wiki (said 'Paris')"
    return True, "correctly declined a question outside the wiki"


def _check_cited(answer: str) -> tuple[bool, str]:
    if has_citation(answer):
        return True, "showed its source (a citation)"
    return False, "stated facts without showing any source"


def _check_synthesis_cited(answer: str) -> tuple[bool, str]:
    n = citation_count(answer)
    if n == 0:
        return False, "compared two stories without citing either"
    if n == 1:
        return True, "cited its source (only one — a stronger model cites both)"
    return True, f"cited {n} sources across the comparison"


# Domain-blind, strict-search prompt used *only* for the off-topic check.
#
# The production prompt can't fairly test "does the model search before it
# refuses" for two reasons: (a) its worked example names the wiki's subject
# (Cinderella, Snow White…), so the model can tell "capital of France" is
# off-topic without searching; and (b) it explicitly permits declining "clearly
# outside" trivia *without* a search. So a strong model refuses "capital of
# France" with zero tool calls — and is right to, per that prompt. This variant
# names no subject and grants no such shortcut: the model cannot know the wiki's
# domain, so it must retrieve before it may decline. That turns the off-topic
# question into a real test of the discipline the production prompt relaxes for
# efficiency — resist parametric knowledge, retrieve first — instead of a flaky
# artifact of the prompt happening to mention fairy tales.
_STRICT_SEARCH_PROMPT = """\
You are a personal knowledge-base assistant. You answer **only** from the user's own \
wiki and source documents — never from your own background knowledge.

## Grounding mandate — non-negotiable
- **Never answer from memory or world knowledge.** Even if you are certain you know \
the answer, you must retrieve it from the knowledge base first.
- **Always call a retrieval tool before you respond — including before you decline.** \
You do not know what subjects this wiki covers, so you may not assume a question is \
off-topic; you must search to find out. A response (an answer *or* a refusal) that is \
not preceded by a tool call is not allowed.
- After searching, if nothing relevant comes back, say plainly that you couldn't find \
it in the wiki. Do not fall back to general knowledge to fill the gap.
"""


# (label, question, grader, requires_retrieval, system_prompt)
# Each check runs under the system prompt that lets it test what it means to:
#   - the off-topic check uses _STRICT_SEARCH_PROMPT (domain-blind, no
#     decline-without-searching shortcut) so a zero-tool-call refusal is a real
#     failure, not an artifact of the production prompt naming the wiki's subject;
#   - the citation checks use the production default (system_prompt=None), whose
#     worked example is what makes cross-document citation reliable.
# requires_retrieval stays True for all three: every check must be preceded by a
# real tool call, or any citation/refusal is ungrounded.
_QUESTIONS = [
    ("Refuses off-topic questions", "What is the capital of France?", _check_refusal, True, _STRICT_SEARCH_PROMPT),
    ("Shows its sources", "Who is Cinderella?", _check_cited, True, None),
    (
        "Cites when comparing",
        "What do Cinderella and Snow White have in common?",
        _check_synthesis_cited,
        True,
        None,
    ),
]


# ── Model resolution ──────────────────────────────────────────────────────────

# One evaluation target: a human label plus the client triple to test.
def _resolve_targets(settings, explicit_models: list[str]) -> list[tuple[str, str, str, str]]:
    """Which (label, base_url, api_key, model) combos to evaluate.

    Default: the configured chat model, plus the wiki-generation model when it is
    set *and* resolves to a different client (blank WIKI_LLM_* values fall back to
    their LLM_* counterparts, so a bare WIKI_LLM_MODEL still points at the chat
    endpoint). `--model` overrides all of this with an explicit list, each tested
    against the chat endpoint's base_url / api_key.
    """
    if explicit_models:
        return [
            (f"model {m}", settings.LLM_BASE_URL, settings.LLM_API_KEY, m)
            for m in explicit_models
        ]

    targets: list[tuple[str, str, str, str]] = [
        ("chat (LLM_*)", settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL),
    ]
    if settings.WIKI_LLM_MODEL.strip():
        ingest = (
            "ingest (WIKI_LLM_*)",
            settings.WIKI_LLM_BASE_URL or settings.LLM_BASE_URL,
            settings.WIKI_LLM_API_KEY or settings.LLM_API_KEY,
            settings.WIKI_LLM_MODEL,
        )
        if ingest[1:] != targets[0][1:]:  # a genuinely different client than chat
            targets.append(ingest)
    return targets


def _count_tool_calls(result) -> int:
    """How many retrieval tool calls the agent actually made during the run.

    A "cited" answer with zero tool calls never retrieved anything — the citation
    is fabricated from memory (models often copy the example page paths straight
    out of the system prompt). We trust the run's message history, not the answer
    text. Kept defensive about pydantic-ai versions by matching `part_kind`.
    """
    try:
        messages = result.all_messages()
    except Exception:  # noqa: BLE001 — never let introspection crash the eval
        return 0
    return sum(
        1
        for message in messages
        for part in getattr(message, "parts", [])
        if getattr(part, "part_kind", None) == "tool-call"
    )


def _made_tool_call(result) -> bool:
    """True if the agent made at least one retrieval tool call."""
    return _count_tool_calls(result) > 0


def _evaluate_one(
    label: str, base_url: str, api_key: str, model: str, db_path: str, *, brief: bool
) -> tuple[int, bool]:
    """Run the fixed questions against one model.

    Returns `(failures, answered_any)`. `answered_any` is False when *every*
    question raised (provider unreachable) — the caller uses it to tell "could
    not run" (exit 2) apart from "answered but too weak" (exit 1).
    """
    from domain.chat.agent import create_agent

    print(f"── {label}: {model}")

    # Build one agent per distinct system prompt (cheap; no network happens until
    # .run_sync). The citation checks share the production default; the off-topic
    # check uses the strict-search prompt. Cache by prompt so we build at most two.
    _agents: dict[str | None, object] = {}

    def _agent_for(prompt: str | None):
        if prompt not in _agents:
            _agents[prompt] = (
                create_agent(base_url, api_key, model)
                if prompt is None
                else create_agent(base_url, api_key, model, system_prompt=prompt)
            )
        return _agents[prompt]

    failures = 0
    answered_any = False
    for q_label, question, grader, requires_retrieval, system_prompt in _QUESTIONS:
        agent = _agent_for(system_prompt)
        try:
            result = agent.run_sync(question, deps=db_path)
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {q_label}: could not get an answer: {exc}")
            failures += 1
            continue
        answer = result.output
        answered_any = True
        n_tools = _count_tool_calls(result)
        passed, detail = grader(answer)
        # A citation (or a refusal) only counts if the model actually retrieved.
        # Zero tool calls => it answered from memory; any citation is fabricated.
        if requires_retrieval and n_tools == 0:
            passed = False
            detail = "answered without calling any retrieval tool — no grounding happened (any citation is fabricated)"
        mark = "✓" if passed else "✗"
        if not passed:
            failures += 1
        tag = f"[{n_tools} tool call{'' if n_tools == 1 else 's'}]"
        if brief:
            print(f"  {mark} {q_label} {tag}: {detail}")
        else:
            snippet = " ".join(answer.split())[:160]
            print(f"  {mark} {q_label} {tag}")
            print(f'      asked: "{question}"')
            print(f"      result: {detail}")
            print(f"      answer: {snippet}…")
    verdict = "good enough" if failures == 0 else f"failed {failures}/{len(_QUESTIONS)}"
    print(f"  → {model}: {'✓' if failures == 0 else '✗'} {verdict}\n")
    return failures, answered_any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test whether the configured chat model(s) are grounded enough.",
    )
    parser.add_argument(
        "--model", action="append", default=[], metavar="ID",
        help="evaluate this model id (repeatable); overrides the configured models",
    )
    parser.add_argument(
        "--brief", action="store_true",
        help="compact output (one line per check) — used by the installer",
    )
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv()

    from config import require_llm_config, settings
    from tests.helpers.golden import golden_available, restore_golden

    if not golden_available():
        print("✗ Sample wiki not found. Build it once with:")
        print("    python scripts/build_golden_corpus.py build && "
              "python scripts/build_golden_corpus.py freeze")
        return 2

    targets = _resolve_targets(settings, args.model)

    # Validate config up front so a missing model reads as "couldn't run" (2),
    # never as a grounding failure (1).
    for label, base_url, api_key, model in targets:
        try:
            require_llm_config(base_url, api_key, model, purpose=label)
        except Exception as exc:  # noqa: BLE001 — surface config problems plainly
            print(f"✗ Model not configured for {label}: {exc}")
            print("  Set LLM_BASE_URL / LLM_API_KEY / LLM_MODEL in your .env.")
            return 2

    n = len(targets)
    print(f"Evaluating {n} model{'s' if n != 1 else ''} against the built-in sample "
          "wiki (four fairy tales).\n")

    tmp = Path(tempfile.mkdtemp(prefix="model-eval-"))
    db_path, _workspace = restore_golden(tmp)

    total_failures = 0
    any_answered = False
    for label, base_url, api_key, model in targets:
        failures, answered = _evaluate_one(
            label, base_url, api_key, model, db_path, brief=args.brief
        )
        total_failures += failures
        any_answered = any_answered or answered

    if not any_answered:
        print("VERDICT: ? could not get any answer — the provider looks unreachable "
              "or misconfigured. Start it / check .env and try again.")
        return 2
    if total_failures == 0:
        print("VERDICT: ✓ grounded and traceable — good enough for this wiki.")
        return 0
    print(
        f"VERDICT: ✗ {total_failures} check(s) failed across {n} model(s). A failing "
        "model is likely too weak — try a stronger one (see the README model-guidance note)."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
