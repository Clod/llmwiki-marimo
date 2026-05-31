"""Tests for config.require_llm_config — the fail-fast LLM-config guard."""

import pytest

from config import require_llm_config


def test_passes_when_fully_configured() -> None:
    # Should not raise.
    require_llm_config("http://localhost:11434/v1", "key", "model", purpose="chat")


def test_raises_when_all_missing() -> None:
    with pytest.raises(RuntimeError) as exc:
        require_llm_config("", "", "", purpose="chat")
    msg = str(exc.value)
    assert "LLM_BASE_URL" in msg
    assert "LLM_API_KEY" in msg
    assert "LLM_MODEL" in msg


def test_raises_on_single_missing_field() -> None:
    with pytest.raises(RuntimeError) as exc:
        require_llm_config("http://x", "key", "", purpose="ingestion")
    # The "Missing:" line lists only the actually-missing var (the "What:"
    # guidance below it always names all three, so check the Missing line only).
    missing_line = next(ln for ln in str(exc.value).splitlines() if "Missing:" in ln)
    assert "LLM_MODEL" in missing_line
    assert "LLM_BASE_URL" not in missing_line
    assert "LLM_API_KEY" not in missing_line


def test_whitespace_only_counts_as_missing() -> None:
    with pytest.raises(RuntimeError):
        require_llm_config("   ", "key", "model", purpose="chat")


def test_message_states_where_and_what() -> None:
    with pytest.raises(RuntimeError) as exc:
        require_llm_config("", "key", "model", purpose="chat")
    msg = str(exc.value)
    assert ".env" in msg          # where
    assert "set LLM_" in msg      # what
    assert ".env.example" in msg  # how to start from scratch
    assert "chat" in msg          # the purpose is named
