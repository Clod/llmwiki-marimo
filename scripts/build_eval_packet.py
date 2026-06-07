#!/usr/bin/env python3
"""Generate a half-automated UAT eval packet for an LLM Wiki.

Plain English: this drives the wiki's own models against a known corpus, captures
what they produce, and writes a single self-contained markdown file. You paste
that file into any capable chat model (a free Gemini / ChatGPT / Claude tab) and
ask it to grade — so the *generation* is automated and the *judging* stays
human-in-the-loop, with as many free judges as you like. It doubles as a way to
compare the models the wiki engine uses.

Two sections:
  Part 1 — Chat grounding & citations: asks the chat model (LLM_MODEL) fixed
           questions against a stable wiki and inlines the pages it cited.
  Part 2 — Ingestion faithfulness & coverage: shows, per source, the text the
           ingestion model (WIKI_LLM_MODEL) was given and the pages it generated.

Default (no flags) benchmarks the frozen golden corpus (four fairy tales):
  - chat runs against the frozen, human-verified wiki (isolates the chat model);
  - ingestion RE-INGESTS the four PDFs with your current WIKI_LLM_MODEL (isolates
    the ingestion model) — this makes real LLM calls and costs a little.

    uv run python scripts/build_eval_packet.py                 # benchmark corpus
    uv run python scripts/build_eval_packet.py --wiki PATH      # an existing wiki
    uv run python scripts/build_eval_packet.py --skip-ingestion # chat only (cheap)

Exit codes: 0 = packet written, 2 = could not run (missing config/corpus).
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BASE = str(_PROJECT_ROOT / "base")
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)
# Expose the project root too so `tests.helpers.golden` imports when run as a
# script. Append, not insert — base/ must still win for the `config` module.
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.append(str(_PROJECT_ROOT))
sys.modules.pop("config", None)  # force base/config.py to win


def _slug(text: str) -> str:
    """Filesystem-safe fragment of a model name for the output filename."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "unknown"


def _build_chat_results(db_path: str, base_url: str, api_key: str, model: str):
    """Run the fixed chat probes against ``db_path`` and gather evidence."""
    from domain.eval import graders
    from domain.eval.packet import AutoCheck, ChatProbeResult
    from domain.eval.reader import resolve_citations
    from domain.eval.rubric import CHAT_PROBES
    from domain.chat.agent import create_agent

    agent = create_agent(base_url, api_key, model)
    results: list[ChatProbeResult] = []
    for label, question, intent in CHAT_PROBES:
        print(f"  chat: {label} …")
        answer = agent.run_sync(question, deps=db_path).output

        if intent == "answerable":
            checks = (
                AutoCheck("has citation", "yes" if graders.has_citation(answer) else "no"),
                AutoCheck("distinct citations", str(graders.citation_count(answer))),
            )
        else:
            leaked = graders.has_citation(answer) or graders.answered_off_corpus(answer)
            checks = (
                AutoCheck(
                    "looks like it answered anyway",
                    "yes (should have refused)" if leaked else "no (refused or hedged)",
                ),
            )

        refs = graders.extract_citations(answer)
        evidence = tuple(resolve_citations(db_path, refs))
        results.append(
            ChatProbeResult(
                label=label,
                question=question,
                intent=intent,
                answer=answer,
                auto_checks=checks,
                cited_evidence=evidence,
            )
        )
    return results


def _fresh_ingest(pdfs: list[Path], base_url: str, api_key: str, model: str) -> str:
    """Re-ingest ``pdfs`` with the current ingestion model into a temp wiki.

    Returns the new DB path. The temp workspace is intentionally left on disk for
    the run; the OS cleans temp dirs, and leaving it lets a curious user inspect
    the freshly generated pages.
    """
    from openai import OpenAI

    from domain.ingestion.batch import batch_ingest
    from domain.tools.db import seed_workspace_row

    workspace = Path(tempfile.mkdtemp(prefix="eval-ingest-"))
    sources_dir = workspace / "sources"
    sources_dir.mkdir(parents=True)
    for pdf in pdfs:
        shutil.copy(pdf, sources_dir / pdf.name)

    db_path = str(workspace / ".llmwiki" / "index.db")
    (workspace / ".llmwiki").mkdir(parents=True, exist_ok=True)
    seed_workspace_row(db_path, "eval_packet")

    client = OpenAI(base_url=base_url, api_key=api_key)
    print(f"  re-ingesting {len(pdfs)} source(s) with {model} …")
    batch_ingest(
        [sources_dir / p.name for p in pdfs],
        db_path,
        workspace,
        client,
        model,
        progress_cb=lambda m: print(f"    {m}"),
    )
    return db_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wiki",
        type=Path,
        default=None,
        help="Target an existing wiki workspace instead of the golden corpus. "
        "Reads its pages as-is (no re-ingestion).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_PROJECT_ROOT / "eval_reports",
        help="Output directory (default: eval_reports/).",
    )
    parser.add_argument("--skip-chat", action="store_true", help="Omit Part 1.")
    parser.add_argument(
        "--skip-ingestion", action="store_true", help="Omit Part 2 (no re-ingest)."
    )
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()

    from config import require_llm_config, settings
    from domain.eval.packet import PacketMeta, corpus_hash, render_packet
    from domain.eval.reader import ingestion_items
    from domain.eval.rubric import JUDGE_INSTRUCTIONS, RUBRIC_MD, RUBRIC_VERSION

    chat_base = settings.LLM_BASE_URL
    chat_key = settings.LLM_API_KEY
    chat_model = settings.LLM_MODEL
    wiki_base = settings.WIKI_LLM_BASE_URL or settings.LLM_BASE_URL
    wiki_key = settings.WIKI_LLM_API_KEY or settings.LLM_API_KEY
    wiki_model = settings.WIKI_LLM_MODEL or settings.LLM_MODEL

    try:
        if not args.skip_chat:
            require_llm_config(chat_base, chat_key, chat_model, purpose="chat")
        if not args.skip_ingestion and args.wiki is None:
            require_llm_config(wiki_base, wiki_key, wiki_model, purpose="ingestion")
    except Exception as exc:  # noqa: BLE001 — surface config problems plainly
        print(f"✗ {exc}")
        return 2

    # ── Resolve the chat target + corpus identity ────────────────────────────
    if args.wiki is not None:
        workspace = args.wiki.resolve()
        chat_db = str(workspace / ".llmwiki" / "index.db")
        if not Path(chat_db).exists():
            print(f"✗ No wiki index at {chat_db}")
            return 2
        sources = sorted((workspace / "sources").glob("*")) if (workspace / "sources").exists() else []
        corpus_label = f"wiki at {workspace.name}"
        ingestion_db = chat_db
    else:
        from tests.helpers.golden import GOLDEN_DIR, golden_available, restore_golden

        if not golden_available():
            print("✗ Golden corpus not frozen. Build it once with:")
            print("    python scripts/build_golden_corpus.py build && "
                  "python scripts/build_golden_corpus.py freeze")
            return 2
        tmp = Path(tempfile.mkdtemp(prefix="eval-chat-"))
        chat_db, _ws = restore_golden(tmp)
        sources = sorted((GOLDEN_DIR / "sources").glob("*.pdf"))
        corpus_label = "golden corpus (4 fairy tales)"
        ingestion_db = None  # re-ingested below

    chash = corpus_hash(sources) if sources else "n/a"

    print(f"Chat model:      {chat_model}")
    print(f"Ingestion model: {wiki_model}")
    print(f"Corpus:          {corpus_label} (sha {chash})\n")

    chat_results: tuple = ()
    if not args.skip_chat:
        chat_results = tuple(_build_chat_results(chat_db, chat_base, chat_key, chat_model))

    ingestion: tuple = ()
    if not args.skip_ingestion:
        if ingestion_db is None:  # benchmark mode → re-ingest with current model
            ingestion_db = _fresh_ingest(sources, wiki_base, wiki_key, wiki_model)
        ingestion = tuple(ingestion_items(ingestion_db))

    meta = PacketMeta(
        chat_model=chat_model,
        wiki_model=wiki_model,
        corpus_label=corpus_label,
        corpus_hash=chash,
        rubric_version=RUBRIC_VERSION,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
    packet = render_packet(meta, JUDGE_INSTRUCTIONS, RUBRIC_MD, chat_results, ingestion)

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    out_path = args.out / f"eval_{_slug(chat_model)}_{_slug(wiki_model)}_{chash}_{stamp}.md"
    out_path.write_text(packet, encoding="utf-8")

    print(f"\n✓ Wrote eval packet → {out_path}")
    print("  Paste its contents into one or more chat models and ask them to "
          "fill in the SCORECARD.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
