"""Tests for domain/chat/guardrail.py — the citation/grounding post-check.

A run is grounded iff some tool returned substantive content; otherwise the
answer is replaced with the refusal. Uses real pydantic_ai message/part classes.
"""

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)

from domain.chat.guardrail import (
    REFUSAL_ES,
    enforce_grounding,
    has_grounding,
    refusal_for,
)


def _tool_return(tool_name: str, content: str) -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart(tool_name=tool_name, content=content, tool_call_id="c1")])


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def test_substantive_tool_result_is_grounded() -> None:
    messages = [
        _user("¿A cuánto está el dólar MEP?"),
        _tool_return("query_dataset", "| MEP | compra | 1180 | ARS | 2026-06-25 | ambito.com |"),
        _assistant("El dólar MEP está a 1180 ARS al 25/06/2026."),
    ]
    assert has_grounding(messages) is True
    assert enforce_grounding("answer", messages, refusal=REFUSAL_ES) == "answer"


def test_only_negative_tool_results_not_grounded() -> None:
    """Searched, found nothing -> not grounded -> refusal (the 'quinoto' case)."""
    messages = [
        _user("¿qué es un quinoto?"),
        _tool_return("search_wiki_fts", "No wiki pages found for 'quinoto'."),
        _assistant("Un quinoto es un kumquat..."),  # leaked general knowledge
    ]
    assert has_grounding(messages) is False
    assert enforce_grounding("Un quinoto es un kumquat...", messages, refusal=REFUSAL_ES) == REFUSAL_ES


def test_no_tool_calls_not_grounded() -> None:
    """Answer with no tool calls at all -> not grounded -> refusal."""
    messages = [_user("¿qué es un etf?"), _assistant("Un ETF es un fondo cotizado...")]
    assert has_grounding(messages) is False
    assert enforce_grounding("Un ETF es...", messages, refusal=REFUSAL_ES) == REFUSAL_ES


def test_mixed_negative_and_substantive_is_grounded() -> None:
    """One failed search + one good read -> grounded (the good evidence counts)."""
    messages = [
        _user("¿Qué es un CEDEAR?"),
        _tool_return("search_wiki_fts", "No wiki pages found for 'ETF'."),
        _tool_return("read_wiki_page", "[wiki page: wiki/concepts/cedears.md]\n\nLos CEDEARs son..."),
        _assistant("Según la página de CEDEARs..."),
    ]
    assert has_grounding(messages) is True


def test_empty_tool_result_not_substantive() -> None:
    messages = [_user("x"), _tool_return("read_wiki_page", "   ")]
    assert has_grounding(messages) is False


def test_refusal_for_language() -> None:
    assert refusal_for("es") == REFUSAL_ES
    assert refusal_for("en") != REFUSAL_ES
    assert refusal_for(None) != REFUSAL_ES
