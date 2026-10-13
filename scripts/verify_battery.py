#!/usr/bin/env python3
"""Ask a list of questions to the chat of a wiki, verify each answer sentence by
sentence, and write the results.

For each question the script asks the chat service (`services.chat`) in one mode,
N times. It verifies each answer against the passages the model read
(`domain.verify`) and writes two files in `--out-dir`:

  * `battery_<time>.jsonl`: one JSON object per question and run;
  * `battery_<time>.md`: per question, the share of sentences supported and the
    sentences no passage supported.

The script calls the chat model, so it needs the `LLM_*` settings of `.env` and a
local NLI model (`uv sync --group verify`). It makes real calls and costs what
the model costs. Run it on a copy of the wiki: opening a wiki creates folders.

    uv run python scripts/verify_battery.py \\
        --wiki examples/finanzas-argentinas \\
        --questions scripts/verify_battery/finanzas-argentinas.txt \\
        --mode pre-retrieval --runs 3

`--verifier fake` replaces the NLI model with a lexical-overlap stand-in, for a
dry run of the pipeline; its numbers mean nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "base"))

from domain.verify import FakeVerifier, verify_answer  # noqa: E402
from domain.verify.core import DEFAULT_THRESHOLD  # noqa: E402
from services import chat  # noqa: E402

MODES = (chat.PRE_RETRIEVAL, chat.STRICT, chat.STREAMING)


def read_questions(path: Path) -> list[str]:
    """The questions of a file: one per line, without blank and `#` lines."""
    lines = (line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


async def ask(wiki, agents, question: str, mode: str) -> chat.ChatResult:
    """One answer of the chat service, as a `ChatResult`, in any mode."""
    messages = [SimpleNamespace(role="user", content=question)]
    if mode == chat.STREAMING:
        result = None
        async for item in chat.chat_turn_stream_with_context(wiki, agents, messages):
            if isinstance(item, chat.ChatResult):
                result = item
        assert result is not None
        return result
    return await chat.chat_turn_with_context(wiki, agents, messages, mode=mode)


async def run_battery(
    wiki, agents, verifier, questions: Sequence[str], *, mode: str, runs: int,
    threshold: float = DEFAULT_THRESHOLD, model: str = "",
) -> list[dict]:
    """One result per question and run. A failed run is a result with `error`."""
    results = []
    for question in questions:
        for run in range(1, runs + 1):
            record = {"question": question, "mode": mode, "run": run, "model": model,
                      "verifier": verifier.name, "threshold": threshold,
                      "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            started = time.monotonic()
            try:
                result = await ask(wiki, agents, question, mode)
                report = await asyncio.to_thread(
                    verify_answer, result.answer, list(result.context), verifier,
                    threshold=threshold, generated=result.generated)
            except Exception as exc:  # noqa: BLE001 — recorded, the battery goes on
                results.append({**record, "error": f"{type(exc).__name__}: {exc}"})
                continue
            results.append({
                **record,
                "answer": result.answer,
                "context_pages": sorted({p.page for p in result.context}),
                "checked": report.checked, "supported": report.supported,
                "unsupported": report.unsupported,
                "supported_share": report.supported / report.checked if report.checked else None,
                "sentences": [
                    {"text": s.text, "start": s.start, "end": s.end, "supported": s.supported,
                     "probability": round(s.probability, 4), "page": s.page}
                    for s in report.sentences],
                "seconds": round(time.monotonic() - started, 2),
            })
    return results


def write_jsonl(results: Sequence[dict], path: Path) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8")


def render_summary(results: Sequence[dict], *, title: str = "Sentence verification battery") -> str:
    """The markdown summary: per question, the supported share and the
    unsupported sentences with the number of runs that produced each."""
    if not results:
        return f"# {title}\n\nNo results.\n"
    first = results[0]
    lines = [f"# {title}", "",
             f"Mode `{first['mode']}`, verifier `{first['verifier']}`, threshold {first['threshold']}, "
             f"model `{first.get('model') or 'unknown'}`.", ""]
    questions = list(dict.fromkeys(r["question"] for r in results))
    shares = []
    for question in questions:
        runs = [r for r in results if r["question"] == question]
        done = [r for r in runs if "error" not in r]
        with_sentences = [r for r in done if r["checked"]]
        lines += [f"## {question}", ""]
        if not with_sentences:
            reason = "all runs failed" if not done else "no sentence to verify (a refusal or an empty answer)"
            lines += [f"Runs: {len(runs)}. {reason}.", ""]
            lines += [f"- Error: {r['error']}" for r in runs if "error" in r]
            lines += [""] if any("error" in r for r in runs) else []
            continue
        share = sum(r["supported"] for r in with_sentences) / sum(r["checked"] for r in with_sentences)
        shares.append(share)
        lines += [f"Runs: {len(runs)} ({len(done)} verified). Supported: {share:.0%} of "
                  f"{sum(r['checked'] for r in with_sentences)} sentences.", ""]
        unsupported = Counter(s["text"] for r in with_sentences for s in r["sentences"] if not s["supported"])
        if unsupported:
            lines.append("Unsupported sentences (runs that produced each):")
            lines += [f"- {text} ({count}/{len(with_sentences)})" for text, count in unsupported.most_common()]
        else:
            lines.append("Every checked sentence is supported.")
        lines += [""]
    if shares:
        lines += ["## Total", "", f"Mean supported share over {len(shares)} questions: {sum(shares) / len(shares):.0%}.", ""]
    return "\n".join(lines)


def _build_agents(wiki_path: Path):
    from config import require_llm_config, settings
    from services.wiki import open_wiki

    require_llm_config(settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL,
                       purpose="the verification battery")
    wiki = open_wiki(wiki_path)
    agents = chat.build_agents(wiki, settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL)
    return wiki, agents, settings.LLM_MODEL


def _build_verifier(kind: str):
    if kind == "fake":
        return FakeVerifier()
    from domain.verify import nli

    if not nli.is_available():
        sys.exit("The local NLI verifier is not installed: run `uv sync --group verify` and "
                 "download the model (see CONTRIBUTING.md), or use --verifier fake for a dry run.")
    return nli.LocalNliVerifier()


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verify the sentences of the answers to a list of questions.")
    p.add_argument("--wiki", type=Path, required=True, help="the wiki folder")
    p.add_argument("--questions", type=Path, required=True, help="a file with one question per line")
    p.add_argument("--mode", choices=MODES, default=chat.PRE_RETRIEVAL)
    p.add_argument("--runs", type=int, default=3, help="times each question is asked (default 3)")
    p.add_argument("--threshold", type=float, help="override [verify] threshold of the wiki")
    p.add_argument("--verifier", choices=("nli", "fake"), default="nli")
    p.add_argument("--out-dir", type=Path, default=Path("battery_results"))
    return p.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    questions = read_questions(args.questions)
    if not questions:
        sys.exit(f"No questions in {args.questions}")
    wiki, agents, model = _build_agents(args.wiki)
    threshold = args.threshold if args.threshold is not None else agents.config.verify_threshold
    verifier = _build_verifier(args.verifier)
    results = asyncio.run(run_battery(wiki, agents, verifier, questions, mode=args.mode,
                                      runs=args.runs, threshold=threshold, model=model))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl, summary = args.out_dir / f"battery_{stamp}.jsonl", args.out_dir / f"battery_{stamp}.md"
    write_jsonl(results, jsonl)
    summary.write_text(render_summary(results), encoding="utf-8")
    print(f"{len(results)} results → {jsonl}\nsummary → {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
