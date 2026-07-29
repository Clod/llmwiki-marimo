"""Pure, network-free graders for chat answers.

These are the cheap deterministic signals shared by the chat-model smoke test
(``scripts/eval_chat_model.py``) and the eval packet's embedded auto-check
pre-screen (``scripts/build_eval_packet.py``). They catch *gross* failures a
regex can see — a leaked off-corpus answer, a missing citation — and nothing
more; semantic quality is left to the LLM judge that grades the packet.
"""

from __future__ import annotations

import re

# A citation is "(wiki/…/something.md)" or "(Something.pdf, p. 3)". The ``ref``
# group captures the inner reference (without the surrounding parentheses) so it
# can be resolved back to a wiki page or source document.
_CITATION = re.compile(r"\((?P<ref>wiki/[^)]*\.md|[^)]*\.pdf[^)]*)\)", re.IGNORECASE)

# The prompt-specified / app-emitted citation format is a trailing line
# "Referencia: <wiki page or source file>" or "Fuente: <external origin>"
# (ensure_citation writes exactly these), so the grader must recognise them
# alongside the inline parenthesized "(…​.md)" form — otherwise a correctly cited
# answer reads as uncited.
_CITATION_LINE = re.compile(
    r"(?:Referencia|Fuente)\s*:\s*(?P<ref>\S.*\S|\S)", re.IGNORECASE
)


def _line_refs(answer: str) -> list[str]:
    """References from any 'Referencia:'/'Fuente:' lines (comma-split, trimmed)."""
    refs: list[str] = []
    for m in _CITATION_LINE.finditer(answer or ""):
        for part in m.group("ref").split(","):
            part = part.strip()
            if part:
                refs.append(part)
    return refs


def answered_off_corpus(answer: str) -> bool:
    """True if the model leaked a world-knowledge answer it should have refused.

    The off-corpus probe asks for the capital of France (not in the wiki). A
    grounded model declines; a weak one says "Paris".
    """
    return "paris" in answer.lower()


def has_citation(answer: str) -> bool:
    """True if the answer carries at least one page/source citation.

    Recognises both the inline parenthesized form ``(wiki/…​.md)`` / ``(…​.pdf)``
    and the trailing ``Referencia:`` / ``Fuente:`` line the system prompt asks for.
    """
    return _CITATION.search(answer) is not None or bool(_line_refs(answer))


def citation_count(answer: str) -> int:
    """Number of distinct citations in the answer."""
    refs = {m.group("ref").lower() for m in _CITATION.finditer(answer)}
    refs.update(r.lower() for r in _line_refs(answer))
    return len(refs)


def extract_citations(answer: str) -> list[str]:
    """Distinct citation references in the answer, in first-seen order.

    Returns the inner reference text (no surrounding parentheses), e.g.
    ``"wiki/summaries/cinderella.md"`` or ``"Cinderella.pdf, p. 3"``. Used to
    resolve and inline the cited evidence into the eval packet.
    """
    out: list[str] = []
    seen: set[str] = set()
    for ref in [m.group("ref").strip() for m in _CITATION.finditer(answer)] + _line_refs(answer):
        key = ref.lower()
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return out
