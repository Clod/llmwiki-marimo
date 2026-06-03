#!/usr/bin/env python3
"""Smoke-test whether your chosen *chat* model is good enough for this wiki.

Plain English: this asks the assistant a few fixed questions about a ready-made
sample wiki (four fairy tales) and checks the answers for the two failures a weak
model makes:

  1. It answers questions that aren't in the wiki at all (it should refuse).
  2. It states facts without showing where they came from (it should cite a page).

It uses whatever model is set as LLM_MODEL in your .env, and it does NOT need you
to ingest anything — it reads a frozen sample wiki that ships with the project.
A few cheap chat calls, then a PASS / FAIL verdict.

    uv run python scripts/eval_chat_model.py

Exit code 0 = good enough, 1 = a check failed (model likely too weak), 2 = could
not run (missing LLM config, etc.).

NOTE: this is a smoke test, not a benchmark. It reliably catches "this model is
too weak", not fine differences between two good models. Run it a couple of times
if a result looks borderline — the model is non-deterministic.
"""

from __future__ import annotations

import re
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

# A citation is "(wiki/…/something.md)" or "(Something.pdf, p. 3)".
_CITATION = re.compile(r"\((?:wiki/[^)]*\.md|[^)]*\.pdf[^)]*)\)", re.IGNORECASE)


# ── Pure grading helpers (no network — unit-testable) ─────────────────────────

def answered_off_corpus(answer: str) -> bool:
    """True if the model leaked a world-knowledge answer it should have refused.

    The probe asks for the capital of France (not in the wiki). A grounded model
    declines; a weak one says "Paris".
    """
    return "paris" in answer.lower()


def has_citation(answer: str) -> bool:
    """True if the answer carries at least one page/source citation."""
    return _CITATION.search(answer) is not None


def citation_count(answer: str) -> int:
    """Number of distinct citations in the answer."""
    return len({m.group(0).lower() for m in _CITATION.finditer(answer)})


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


_QUESTIONS = [
    ("Refuses off-topic questions", "What is the capital of France?", _check_refusal),
    ("Shows its sources", "Who is Cinderella?", _check_cited),
    (
        "Cites when comparing",
        "What do Cinderella and Snow White have in common?",
        _check_synthesis_cited,
    ),
]


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv()

    from config import require_llm_config, settings
    from domain.chat.agent import create_agent
    from tests.helpers.golden import golden_available, restore_golden

    if not golden_available():
        print("✗ Sample wiki not found. Build it once with:")
        print("    python scripts/build_golden_corpus.py build && "
              "python scripts/build_golden_corpus.py freeze")
        return 2

    try:
        require_llm_config(
            settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL,
            purpose="chat",
        )
    except Exception as exc:  # noqa: BLE001 — surface config problems plainly
        print(f"✗ Chat model not configured: {exc}")
        print("  Set LLM_BASE_URL / LLM_API_KEY / LLM_MODEL in your .env.")
        return 2

    model = settings.LLM_MODEL
    print(f"Evaluating chat model:  {model}")
    print("Against the built-in sample wiki (four fairy tales).\n")

    tmp = Path(tempfile.mkdtemp(prefix="model-eval-"))
    db_path, _workspace = restore_golden(tmp)
    agent = create_agent(settings.LLM_BASE_URL, settings.LLM_API_KEY, model)

    failures = 0
    for label, question, grader in _QUESTIONS:
        try:
            answer = agent.run_sync(question, deps=db_path).output
        except Exception as exc:  # noqa: BLE001
            print(f"✗ {label}\n    could not get an answer: {exc}\n")
            failures += 1
            continue
        passed, detail = grader(answer)
        mark = "✓" if passed else "✗"
        if not passed:
            failures += 1
        snippet = " ".join(answer.split())[:160]
        print(f"{mark} {label}")
        print(f'    asked: "{question}"')
        print(f"    result: {detail}")
        print(f"    answer: {snippet}…\n")

    if failures == 0:
        print(f"VERDICT: ✓ {model} looks good enough for chat — grounded and traceable.")
        return 0
    print(
        f"VERDICT: ✗ {model} failed {failures} of {len(_QUESTIONS)} checks. It is "
        "likely too weak — try a stronger model (see the README model-guidance note)."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
