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


def answered_off_corpus(answer: str) -> bool:
    """True if the model leaked a world-knowledge answer it should have refused.

    The off-corpus probe asks for the capital of France (not in the wiki). A
    grounded model declines; a weak one says "Paris".
    """
    return "paris" in answer.lower()


def has_citation(answer: str) -> bool:
    """True if the answer carries at least one page/source citation."""
    return _CITATION.search(answer) is not None


def citation_count(answer: str) -> int:
    """Number of distinct citations in the answer."""
    return len({m.group("ref").lower() for m in _CITATION.finditer(answer)})


def extract_citations(answer: str) -> list[str]:
    """Distinct citation references in the answer, in first-seen order.

    Returns the inner reference text (no surrounding parentheses), e.g.
    ``"wiki/summaries/cinderella.md"`` or ``"Cinderella.pdf, p. 3"``. Used to
    resolve and inline the cited evidence into the eval packet.
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in _CITATION.finditer(answer):
        ref = m.group("ref").strip()
        key = ref.lower()
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return out
