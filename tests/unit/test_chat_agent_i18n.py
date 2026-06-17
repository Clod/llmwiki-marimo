"""Tests for domain/chat/agent.py — chat answer-language directive wiring."""

from domain.chat.agent import create_agent
from domain.i18n import apply_chat_directive, get_locale


def test_apply_chat_directive_spanish_appends_directive() -> None:
    base = "You are a helpful assistant."
    result = apply_chat_directive(base, "es")
    assert result != base
    assert len(result) > len(base)
    assert get_locale("es").chat_directive in result


def test_apply_chat_directive_english_is_noop() -> None:
    base = "You are a helpful assistant."
    assert apply_chat_directive(base, "en") == base


def test_create_agent_spanish_system_prompt_contains_chat_directive() -> None:
    """create_agent(..., language="es") must feed Agent the prompt with the
    Spanish chat directive appended — no network call is made by construction."""
    base_prompt = "Base system prompt for testing."
    agent = create_agent(
        base_url="http://localhost:1234/v1",
        api_key="fake-key",
        model="fake-model",
        system_prompt=base_prompt,
        language="es",
    )
    # pydantic_ai.Agent stores a plain-string system_prompt as a 1-tuple on
    # _system_prompts; introspect it directly rather than calling .run().
    (effective_prompt,) = agent._system_prompts
    assert effective_prompt == apply_chat_directive(base_prompt, "es")
    assert get_locale("es").chat_directive in effective_prompt
    assert effective_prompt != base_prompt


def test_create_agent_english_system_prompt_unchanged() -> None:
    """The default language="en" must leave the system prompt byte-identical —
    this is the backward-compat guarantee for existing English wikis."""
    base_prompt = "Base system prompt for testing."
    agent = create_agent(
        base_url="http://localhost:1234/v1",
        api_key="fake-key",
        model="fake-model",
        system_prompt=base_prompt,
    )
    (effective_prompt,) = agent._system_prompts
    assert effective_prompt == base_prompt


def test_create_agent_calls_apply_chat_directive_with_given_language(monkeypatch) -> None:
    """Spy on apply_chat_directive to prove create_agent forwards `language` to it
    and feeds the result to Agent — independent of pydantic_ai's internal storage."""
    calls = []

    def spy(system_prompt: str, language: str) -> str:
        calls.append((system_prompt, language))
        return "SPIED_PROMPT"

    monkeypatch.setattr("domain.chat.agent.apply_chat_directive", spy)

    base_prompt = "Base system prompt for testing."
    agent = create_agent(
        base_url="http://localhost:1234/v1",
        api_key="fake-key",
        model="fake-model",
        system_prompt=base_prompt,
        language="es",
    )

    assert calls == [(base_prompt, "es")]
    (effective_prompt,) = agent._system_prompts
    assert effective_prompt == "SPIED_PROMPT"
