"""Pure assembly of the eval packet markdown — no network, no DB.

Everything here is a deterministic function of its inputs, so it is unit-tested
in the fast gate. The live chat/ingestion calls and DB reads live in
``scripts/build_eval_packet.py`` and ``domain.eval.reader``; this module only
formats what it is handed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

# Default per-document excerpt cap. The golden corpus is small enough to inline
# whole; the cap only bites when targeting a large real wiki.
DEFAULT_EXCERPT_CHARS = 8000


@dataclass(frozen=True)
class Evidence:
    """A resolved citation or generated page: a label plus its text content."""

    label: str
    content: str


@dataclass(frozen=True)
class AutoCheck:
    """A cheap deterministic pre-screen result shown alongside a chat answer."""

    name: str
    detail: str


@dataclass(frozen=True)
class ChatProbeResult:
    """One chat probe: the question, the verbatim answer, and what backs it."""

    label: str
    question: str
    intent: str  # "answerable" | "unanswerable"
    answer: str
    auto_checks: tuple[AutoCheck, ...] = ()
    cited_evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class IngestionItem:
    """One source document and the pages the engine generated from it."""

    source_name: str
    source_text: str
    summary: str  # generated summary page content ("" if none)
    concepts: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class PacketMeta:
    """Provenance header — makes any two packets comparable."""

    chat_model: str
    wiki_model: str
    corpus_label: str
    corpus_hash: str
    rubric_version: str
    generated_at: str


def truncate(text: str, max_chars: int = DEFAULT_EXCERPT_CHARS) -> str:
    """Cap ``text`` length, appending an explicit marker with the omitted count."""
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    head = text[:max_chars].rstrip()
    return f"{head}\n\n…[excerpt truncated — {omitted} characters omitted]"


def corpus_hash(paths: list[Path]) -> str:
    """Stable short sha256 over the bytes of the given files.

    Order-independent (paths are sorted by name), so the same fileset always
    hashes the same — that is what lets two packets claim the same corpus.
    """
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda x: Path(x).name):
        h.update(Path(p).name.encode("utf-8"))
        h.update(b"\0")
        h.update(Path(p).read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:12]


# ── Markdown rendering (all pure) ─────────────────────────────────────────────

def _fence(content: str) -> str:
    """Wrap content in a markdown code fence, dodging any inner triple-backticks."""
    fence = "```"
    while fence in content:
        fence += "`"
    return f"{fence}\n{content.rstrip()}\n{fence}"


def _render_chat_probe(n: int, probe: ChatProbeResult) -> str:
    lines = [
        f"### Q{n}. {probe.label} ({probe.intent})",
        "",
        f"**Question:** {probe.question}",
        "",
        "**Assistant answer:**",
        "",
        _fence(probe.answer or "(empty answer)"),
        "",
    ]
    if probe.auto_checks:
        checks = " · ".join(f"{c.name}: {c.detail}" for c in probe.auto_checks)
        lines += [f"**Auto-checks (regex pre-screen, not the rubric):** {checks}", ""]
    lines.append("**Cited evidence:**")
    lines.append("")
    if probe.cited_evidence:
        for ev in probe.cited_evidence:
            lines += [f"- `{ev.label}`:", "", _fence(ev.content), ""]
    else:
        lines += ["- (none — the answer cited no wiki page or source)", ""]
    return "\n".join(lines)


def _render_ingestion_item(n: int, item: IngestionItem) -> str:
    lines = [
        f"### S{n}. {item.source_name}",
        "",
        "**Source text the model was given:**",
        "",
        _fence(item.source_text or "(no extracted text)"),
        "",
        "**Generated summary page:**",
        "",
        _fence(item.summary or "(no summary page was generated)"),
        "",
        "**Generated concept pages:**",
        "",
    ]
    if item.concepts:
        for ev in item.concepts:
            lines += [f"- `{ev.label}`:", "", _fence(ev.content), ""]
    else:
        lines += ["- (no concept pages cite this source)", ""]
    return "\n".join(lines)


def _render_scorecard(
    chat_results: tuple[ChatProbeResult, ...],
    ingestion_items: tuple[IngestionItem, ...],
) -> str:
    lines = ["## SCORECARD (judge: fill this in)", ""]
    if chat_results:
        lines += [
            "### Chat (1–5 each)",
            "",
            "| # | Probe | Groundedness | Citation correctness | Appropriate response | Completeness | Notes |",
            "|---|-------|--------------|----------------------|----------------------|--------------|-------|",
        ]
        for i, p in enumerate(chat_results, 1):
            lines.append(f"| Q{i} | {p.label} | _ | _ | _ | _ | |")
        lines += ["", "**Chat overall (1–5):** ___", ""]
    if ingestion_items:
        lines += [
            "### Ingestion (1–5 each)",
            "",
            "| # | Source | Faithfulness | Coverage | Concept quality | Structure | Notes |",
            "|---|--------|--------------|----------|-----------------|-----------|-------|",
        ]
        for i, item in enumerate(ingestion_items, 1):
            lines.append(f"| S{i} | {item.source_name} | _ | _ | _ | _ | |")
        lines += ["", "**Ingestion overall (1–5):** ___", ""]
    return "\n".join(lines)


def render_packet(
    meta: PacketMeta,
    instructions: str,
    rubric_md: str,
    chat_results: tuple[ChatProbeResult, ...] = (),
    ingestion_items: tuple[IngestionItem, ...] = (),
) -> str:
    """Assemble the full, self-contained eval packet as a markdown string."""
    parts = [
        "# Wiki Quality Eval Packet",
        "",
        f"- **Chat model (system under test):** `{meta.chat_model}`",
        f"- **Ingestion model (system under test):** `{meta.wiki_model}`",
        f"- **Corpus:** {meta.corpus_label} (sha256 `{meta.corpus_hash}`)",
        f"- **Rubric version:** {meta.rubric_version}",
        f"- **Generated:** {meta.generated_at}",
        "",
        "> This packet is self-contained. Paste it into any capable chat model and",
        "> ask it to follow the instructions and fill in the SCORECARD. For a more",
        "> robust result, run it past two or three different judges and average.",
        "",
        "## Instructions for the judge",
        "",
        instructions.rstrip(),
        "",
        "## Rubric",
        "",
        rubric_md.rstrip(),
        "",
    ]

    if chat_results:
        parts += ["## Part 1 — Chat grounding & citations", ""]
        for i, probe in enumerate(chat_results, 1):
            parts += [_render_chat_probe(i, probe), ""]

    if ingestion_items:
        parts += ["## Part 2 — Ingestion faithfulness & coverage", ""]
        for i, item in enumerate(ingestion_items, 1):
            parts += [_render_ingestion_item(i, item), ""]

    parts += [_render_scorecard(chat_results, ingestion_items)]
    return "\n".join(parts).rstrip() + "\n"
