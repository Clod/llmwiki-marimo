"""The passages a chat run read, taken from the tool returns of its messages.

The strict and streaming modes give the model tools. Each `ToolReturnPart` of
`result.all_messages()` whose call succeeded (`guardrail.is_tool_result`) is
text the model read. This module turns those parts into `ContextPassage` values
with the page each comes from. It does not read the trace.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from domain.chat.guardrail import _is_substantive, is_tool_result
from domain.verify.models import ContextPassage

_WIKI_PAGE = re.compile(r"\A\[wiki page:\s*([^\]]+)\]\s*", re.IGNORECASE)
_DATASET_CATEGORY = re.compile(r"categoria='([^']+)'")
# A search hit: `**path** p.3 › crumb`, then the snippet in a code fence.
_SEARCH_HIT = re.compile(r"^\*\*(?P<path>[^*\n]+)\*\*[^\n]*\n```\n(?P<text>.*?)\n```", re.MULTILINE | re.DOTALL)


def _wiki_page(rel: str) -> str:
    rel = rel.strip()
    return rel if rel.startswith("/") else f"/{rel}"


def passages_from_tool_return(tool_name: str, content: object) -> list[ContextPassage]:
    """The passages of one tool return. An unrecognised return is one passage
    named after the tool."""
    text = str(content).strip()
    if not _is_substantive(text):
        return []
    page = _WIKI_PAGE.match(text)
    if page:
        return [ContextPassage(_wiki_page(page.group(1)), text[page.end():], tool_name)]
    hits = list(_SEARCH_HIT.finditer(text))
    if hits:
        return [ContextPassage(hit["path"].strip(), hit["text"].strip(), tool_name) for hit in hits]
    category = _DATASET_CATEGORY.search(text)
    if category:
        return [ContextPassage(f"datasets/{category.group(1)}.md", text, tool_name)]
    return [ContextPassage(tool_name, text, tool_name)]


def context_from_messages(messages: Iterable) -> list[ContextPassage]:
    """The passages of every substantive tool return in `messages`, in order."""
    passages: list[ContextPassage] = []
    for message in messages or []:
        for part in getattr(message, "parts", []):
            if is_tool_result(part):
                passages.extend(passages_from_tool_return(part.tool_name, part.content))
    return passages
