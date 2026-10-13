"""The audit page of a conversation: for each answer, the path of the turn, the passages the wiki gave the model
and the text of the model when the code replaced it."""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from domain.datasets.source import LocalMarkdownSource
from domain.tools.wiki_fs import concept_page_names
from web import audit_diagram, audit_nodes
from web.deps import MODE_LABELS, get_state, get_templates, page_context
from web.i18n import _, format_number
from web.render import page_url
from web.state import AppState

router = APIRouter()

SUMMARY_CHARS = 160


def _display(value) -> str:
    """An attribute value as text: lists joined, anything else as it is."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    if isinstance(value, (dict,)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def roster_topics(wiki) -> list[str]:
    """The topics of the coverage roster that a user can name: the titles of the concept
    pages and the categories of the datasets."""
    categories = LocalMarkdownSource(wiki.path / "datasets").categories()
    return sorted({*concept_page_names(wiki.db_path), *categories}, key=str.lower)


def criterion_nodes(path, topics) -> list[dict]:
    """One entry per node of the path, for the template: identifier, short text, the
    attributes as (name, text) pairs and, for Q5, the stemmed question and the roster."""
    stemmed = next((s["attributes"].get("stemmed") for s in path if s["node"] == "Q4"), None)
    nodes = []
    for step in path:
        attributes = step["attributes"]
        entry = {"id": step["node"], "text": audit_nodes.text(step["node"]),
                 "attributes": [(k, _display(v)) for k, v in attributes.items()],
                 "stemmed": None, "topics": None}
        if step["node"] == "Q5":
            entry["stemmed"] = stemmed
            if not attributes.get("matched"):
                entry["topics"] = topics()
        nodes.append(entry)
    return nodes


def passages_view(wiki_id: str, context) -> list[dict]:
    """The passages the model received: page (with its wiki link when it is a wiki page),
    the first characters in one line, and the full text."""
    passages = []
    for p in context:
        page = p.page.lstrip("/")
        link = page_url(wiki_id, page.removeprefix("wiki/").removesuffix(".md")) \
            if page.startswith("wiki/") and page.endswith(".md") else None
        summary = re.sub(r"\s+", " ", p.text).strip()
        if len(summary) > SUMMARY_CHARS:
            summary = summary[:SUMMARY_CHARS].rstrip() + "…"
        passages.append({"page": p.page, "link": link, "summary": summary, "text": p.text})
    return passages


def conversation_turns(state: AppState, wiki, wiki_id: str, messages) -> list[dict]:
    """One entry per answer of the conversation, in order: its number (from 1), the question
    that precedes it, the answer shown and the three sections of the audit."""
    turns = []
    for index, message in enumerate(messages):
        if message.role != "assistant":
            continue
        question = messages[index - 1].content if index > 0 and messages[index - 1].role == "user" else ""
        turns.append({
            "n": len(turns) + 1, "question": question, "answer": message.content,
            "tokens": {"input": format_number(message.input_tokens), "output": format_number(message.output_tokens),
                       "total": format_number(message.tokens)},
            "path": " → ".join(step["node"] for step in message.path),
            "nodes": criterion_nodes(message.path, lambda: roster_topics(wiki)),
            "passages": passages_view(wiki_id, message.context), "raw_answer": message.raw_answer})
    return turns


def turn_diagrams(message) -> list[str]:
    """The diagrams of one answer: the overview and the one of its mode, with its path marked."""
    return audit_diagram.diagrams_for(message.mode, [step["node"] for step in message.path])


def _answers(state: AppState, wiki_id: str, conversation_id: str) -> list:
    return [m for m in state.conversations.get((wiki_id, conversation_id), []) if m.role == "assistant"]


@router.get("/w/{wiki_id}/audit/{conversation_id}/diagram/{n}", response_class=HTMLResponse)
def audit_diagram_of_turn(request: Request, wiki_id: str, conversation_id: str, n: int,
                          state: AppState = Depends(get_state),
                          templates: Jinja2Templates = Depends(get_templates)):
    """The right column for answer `n`: its diagrams, so that selecting a turn replaces them."""
    state.get_wiki(wiki_id)
    answers = _answers(state, wiki_id, conversation_id)
    if not 1 <= n <= len(answers):
        raise HTTPException(status_code=404, detail=_("That answer does not exist."))
    return templates.TemplateResponse(request, "_audit_diagrams.html", {"n": n, "diagrams": turn_diagrams(answers[n - 1])})


@router.get("/w/{wiki_id}/audit/{conversation_id}/{n}")
def audit_turn(wiki_id: str, conversation_id: str, n: int, state: AppState = Depends(get_state)):
    """The route of one answer, as before: it leads to the turn in the page of the conversation."""
    state.get_wiki(wiki_id)
    answers = _answers(state, wiki_id, conversation_id)
    if not 1 <= n <= len(answers):
        raise HTTPException(status_code=404, detail=_("That answer does not exist."))
    return RedirectResponse(f"/w/{wiki_id}/audit/{conversation_id}#t{n}-criterio", status_code=303)


@router.get("/w/{wiki_id}/audit/{conversation_id}", response_class=HTMLResponse)
def audit(request: Request, wiki_id: str, conversation_id: str,
          state: AppState = Depends(get_state), templates: Jinja2Templates = Depends(get_templates)):
    """The audit page of a conversation: its index, the summary and, for each answer, its
    criterion, the passages the wiki gave the model and the text of the model."""
    entry = state.get_wiki(wiki_id)
    messages = state.conversations.get((wiki_id, conversation_id), [])
    turns = conversation_turns(state, entry.wiki, wiki_id, messages)
    if not turns:
        raise HTTPException(status_code=404, detail=_("That conversation has no answer."))
    modes = list(dict.fromkeys(MODE_LABELS[m.mode] for m in messages if m.role == "assistant" and m.mode in MODE_LABELS))
    context = page_context(state, wiki_id, "", section="audit")
    last = [m for m in messages if m.role == "assistant"][-1]   # the turn the buttons of the chat point to
    context.update(turns=turns, mode_labels=modes, conversation_id=conversation_id, selected=len(turns),
                   n=len(turns), diagrams=turn_diagrams(last))
    return templates.TemplateResponse(request, "audit.html", context)
