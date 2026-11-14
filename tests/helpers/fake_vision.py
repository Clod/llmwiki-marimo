"""Fake vision client for the image descriptions: no model, no network.

It follows `tests/helpers/fake_llm.py` (the `client.chat.completions.create` interface)
and adds the `usage` of a response. Every call is recorded in `calls`; `responses` are
consumed in order and the last one repeats.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

ANSWER = {
    "type": "bar chart",
    "decorative": False,
    "description": "Annual nominal rate of five banks at 30 days.",
    "visible_text": ["TNA 30 days", "Banco Nación", "35.0 %"],
    "values": ["Banco Nación 35.0 %", "Banco Galicia 34.5 %"],
}
DECORATIVE_ANSWER = {"type": "frame", "decorative": True, "description": "A frame with the title.",
                     "visible_text": [], "values": []}


def answer(**changes) -> str:
    return json.dumps({**ANSWER, **changes}, ensure_ascii=False)


class FakeVisionClient:
    def __init__(self, responses: list | None = None, tokens: tuple[int, int] = (812, 146)) -> None:
        self.responses = responses if responses is not None else [answer()]
        self.tokens = tokens
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model: str, messages: list, **kwargs):
        self.calls.append({"model": model, "messages": messages, **kwargs})
        item = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        usage = SimpleNamespace(prompt_tokens=self.tokens[0], completion_tokens=self.tokens[1])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=item))],
                               usage=usage, model=model)
