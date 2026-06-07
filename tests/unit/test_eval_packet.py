"""Tests for the pure eval-packet assembly (domain.eval.packet).

No network, no DB — these lock the deterministic behaviour of truncation,
corpus hashing, and markdown rendering, which is the part of the eval tool that
runs in the fast gate.
"""

from domain.eval.packet import (
    AutoCheck,
    ChatProbeResult,
    Evidence,
    IngestionItem,
    PacketMeta,
    corpus_hash,
    render_packet,
    truncate,
)
from domain.eval.rubric import JUDGE_INSTRUCTIONS, RUBRIC_MD


def test_truncate_passes_short_text_through() -> None:
    assert truncate("hello", 100) == "hello"


def test_truncate_caps_and_marks_omitted_count() -> None:
    text = "x" * 50
    out = truncate(text, 20)
    assert out.startswith("x" * 20)
    assert "30 characters omitted" in out


def test_corpus_hash_is_stable_and_order_independent(tmp_path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_bytes(b"alpha")
    b.write_bytes(b"beta")
    h1 = corpus_hash([a, b])
    h2 = corpus_hash([b, a])  # different order, same set
    assert h1 == h2
    assert len(h1) == 12


def test_corpus_hash_changes_with_content(tmp_path) -> None:
    a = tmp_path / "a.txt"
    a.write_bytes(b"alpha")
    h1 = corpus_hash([a])
    a.write_bytes(b"alpha!")
    h2 = corpus_hash([a])
    assert h1 != h2


def _meta() -> PacketMeta:
    return PacketMeta(
        chat_model="chat-x",
        wiki_model="wiki-y",
        corpus_label="golden corpus (4 fairy tales)",
        corpus_hash="abc123def456",
        rubric_version="1.0",
        generated_at="2026-06-06 12:00 UTC",
    )


def test_render_packet_has_header_instructions_and_rubric() -> None:
    out = render_packet(_meta(), JUDGE_INSTRUCTIONS, RUBRIC_MD)
    assert "# Wiki Quality Eval Packet" in out
    assert "`chat-x`" in out and "`wiki-y`" in out
    assert "abc123def456" in out
    assert "Instructions for the judge" in out
    assert "Groundedness" in out and "Faithfulness to source" in out


def test_render_packet_includes_chat_probe_answer_checks_and_evidence() -> None:
    probe = ChatProbeResult(
        label="Single-source recall",
        question="Who is Cinderella?",
        intent="answerable",
        answer="Cinderella is a kind girl (wiki/summaries/cinderella.md).",
        auto_checks=(AutoCheck("has citation", "yes"),),
        cited_evidence=(Evidence("wiki/summaries/cinderella.md", "# Cinderella\nA summary."),),
    )
    out = render_packet(_meta(), JUDGE_INSTRUCTIONS, RUBRIC_MD, chat_results=(probe,))
    assert "Part 1 — Chat grounding" in out
    assert "Who is Cinderella?" in out
    assert "Cinderella is a kind girl" in out
    assert "has citation: yes" in out
    assert "wiki/summaries/cinderella.md" in out
    assert "A summary." in out
    # scorecard has a row for the probe
    assert "| Q1 | Single-source recall |" in out


def test_render_packet_marks_uncited_answer() -> None:
    probe = ChatProbeResult(
        label="Off-corpus refusal",
        question="What is the capital of France?",
        intent="unanswerable",
        answer="I couldn't find that in the wiki.",
    )
    out = render_packet(_meta(), JUDGE_INSTRUCTIONS, RUBRIC_MD, chat_results=(probe,))
    assert "(none — the answer cited no wiki page or source)" in out


def test_render_packet_includes_ingestion_item_and_scorecard_row() -> None:
    item = IngestionItem(
        source_name="Cinderella.pdf",
        source_text="Once upon a time…",
        summary="# Cinderella\nGenerated summary.",
        concepts=(Evidence("wiki/concepts/glass-slipper.md", "# Glass Slipper\nbody"),),
    )
    out = render_packet(_meta(), JUDGE_INSTRUCTIONS, RUBRIC_MD, ingestion_items=(item,))
    assert "Part 2 — Ingestion faithfulness" in out
    assert "Cinderella.pdf" in out
    assert "Once upon a time" in out
    assert "Generated summary." in out
    assert "wiki/concepts/glass-slipper.md" in out
    assert "| S1 | Cinderella.pdf |" in out


def test_render_packet_fence_escapes_inner_backticks() -> None:
    probe = ChatProbeResult(
        label="x",
        question="q",
        intent="answerable",
        answer="here is ```code``` inline",
    )
    out = render_packet(_meta(), JUDGE_INSTRUCTIONS, RUBRIC_MD, chat_results=(probe,))
    # the answer's triple-backticks must not break out of the fence
    assert "here is ```code``` inline" in out
    assert "````" in out  # a longer fence was chosen
