#!/usr/bin/env python3
"""Test whether the models configured in `.env` can do the work of this wiki.

The script runs the model tests of the product (`services.model_test`), the same tests
as the Test buttons of the Settings screen:

  reading:   three chat turns on a temporary wiki of two pages. The model answers from
             the page and cites it, declines a question the page does not answer, and
             searches the wiki with the tools.
  ingestion: the ingestion of a text of one paragraph into a temporary wiki. The model
             writes the summary page and a concept page, and the lint finds no error.
  vision:    the OCR and the description of a scanned page that ships with the product.
             The fourteen data of the page are in what the model reads.

Each test builds its material in a temporary folder and deletes it at the end. No test
reads your wiki.

Without `--model`, the script tests the reading model (LLM_*), the ingestion model
(WIKI_LLM_*, each blank value taken from LLM_*) and, when VISION_MODEL is set or
OCR_ENGINE is "vision", the vision model (VISION_*, each blank value taken from
WIKI_LLM_* and then from LLM_*). `--model ID` runs the reading test with that model
and the service of LLM_*.

    uv run python scripts/eval_chat_model.py            # the configured models
    uv run python scripts/eval_chat_model.py --model X  # the reading test with model X
    uv run python scripts/eval_chat_model.py --brief    # one line per check

Exit code 0: every check passed. 1: a check that ran failed, so the model is likely
too weak. 2: the tests could not run (a missing value in `.env`, or no answer from
the service).

Passing is necessary, not sufficient: the tests find a model that cannot do the work,
and they do not compare two models that can. A model gives different results in two
runs, so a doubtful result is worth a second run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import Settings
    from domain.modeltest import Check, Report

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BASE = str(_PROJECT_ROOT / "base")
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)
sys.modules.pop("config", None)  # force base/config.py to win

# The name of each check of `domain.modeltest`. The Settings screen shows the same names
# (`web/routes/settings.py`, `CHECK_NAMES`).
CHECK_NAMES = {
    "answers": "It answers from the page and cites it",
    "declines": "It declines when the page has no answer",
    "uses_tools": "It searches the wiki with the tools",
    "ingests": "It writes the summary page and the concept pages",
    "reads_page": "It reads the scanned page",
    "describes": "It describes an image in the format asked",
}

# (label, kind, base_url, api_key, model): one test to run.
Target = tuple[str, str, str, str, str]


def _resolve_targets(settings: Settings, explicit_models: list[str]) -> list[Target]:
    """The tests to run, with the values of `settings`.

    With `explicit_models`, one reading test per model, with the URL and the key of
    LLM_*. Without it, the reading test (LLM_*), the ingestion test (WIKI_LLM_*, each
    blank value taken from LLM_*) and, when VISION_MODEL is set or OCR_ENGINE is
    "vision", the vision test (`settings.vision()`).
    """
    if explicit_models:
        return [("reading", "reading", settings.LLM_BASE_URL, settings.LLM_API_KEY, m)
                for m in explicit_models]
    targets: list[Target] = [
        ("reading (LLM_*)", "reading", settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL),
        ("ingestion (WIKI_LLM_*)", "ingestion",
         settings.WIKI_LLM_BASE_URL or settings.LLM_BASE_URL,
         settings.WIKI_LLM_API_KEY or settings.LLM_API_KEY,
         settings.WIKI_LLM_MODEL or settings.LLM_MODEL),
    ]
    if settings.VISION_MODEL.strip() or settings.OCR_ENGINE.strip() == "vision":
        targets.append(("vision (VISION_*)", "vision", *settings.vision()))
    return targets


def check_line(check: Check) -> str:
    """The line of one check: ✓ passed, ✗ failed, ? did not run."""
    name = CHECK_NAMES.get(check.key, check.key)
    if check.passed:
        return f"✓ {name}"
    if not check.ran:
        return f"? {name}: no answer from the service ({check.detail})"
    return f"✗ {name}: {check.detail}"


def closing_line(report: Report) -> str:
    """The last line of one test. A check that did not run does not count as a failure
    of the model."""
    n = len(report.checks)
    if report.passed:
        return f"✓ The model passed the {n} checks ({report.seconds} s)."
    failed = sum(c.ran and not c.passed for c in report.checks)
    if failed:
        return f"✗ The model failed {failed} of {n} checks ({report.seconds} s)."
    return "? The tests could not run: the service gave no answer."


def _run_one(target: Target, language: str, *, brief: bool) -> Report:
    """Run one test, print one line per check as it ends, and return the `Report`."""
    from services import model_test

    label, kind, base_url, api_key, model = target
    print(f"── {label}: {model}", flush=True)

    def progress(check: Check) -> None:
        print(f"  {check_line(check)}", flush=True)
        if not brief and check.passed:
            print(f"      {check.detail}", flush=True)

    report = model_test.run(kind, base_url, api_key, model, language, progress=progress)
    print(f"  → {closing_line(report)}\n", flush=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Test whether the configured models can do the work of this wiki.",
    )
    parser.add_argument(
        "--model", action="append", default=[], metavar="ID",
        help="run the reading test with this model id, in place of the configured models. Repeatable",
    )
    parser.add_argument(
        "--brief", action="store_true",
        help="one line per check, with no detail for a check that passed (used by the installer)",
    )
    parser.add_argument(
        "--language", default="en", metavar="LANG",
        help="language of the test material: en (default) or es. Any other value uses en",
    )
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv()

    from config import require_llm_config, settings

    targets = _resolve_targets(settings, args.model)

    # A missing value reads as "could not run" (2), never as a failure of the model (1).
    for label, _kind, base_url, api_key, model in targets:
        try:
            require_llm_config(base_url, api_key, model, purpose=label)
        except Exception as exc:  # noqa: BLE001 — print the configuration problem as it is
            print(f"✗ Model not configured for {label}: {exc}")
            return 2

    n = len(targets)
    print(f"Running {n} model test{'s' if n != 1 else ''} on temporary material "
          f"(language: {args.language}).\n")

    reports = [_run_one(target, args.language, brief=args.brief) for target in targets]
    checks = [c for r in reports for c in r.checks]
    failed = sum(c.ran and not c.passed for c in checks)
    if failed:
        print(f"VERDICT: ✗ {failed} of {len(checks)} checks failed. A model that fails is "
              "likely too weak: try a stronger one (see the README model-guidance note).")
        return 1
    if checks and all(c.passed for c in checks):
        print(f"VERDICT: ✓ the {len(checks)} checks passed. Passing is necessary, not sufficient.")
        return 0
    print("VERDICT: ? the tests could not run: the service gave no answer. "
          "Start the service or check .env, and try again.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
