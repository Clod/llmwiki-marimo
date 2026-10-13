"""Chat: one turn streamed over SSE, clear, save, and the thread of a conversation."""

from __future__ import annotations

import asyncio
import html
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from domain.chat.config import DEFAULT_VERIFY_THRESHOLD, load_config
from domain.datasets.frontmatter import split_frontmatter
from domain.verify import verify_answer
from services import chat as chat_service
from services.wiki import list_pages, source_names
from web.deps import BUSY, MODE_LABELS, get_state, get_templates, index_groups
from web.i18n import N_, _, format_number, ngettext
from web.render import page_url, render_markdown, render_verified
from web.state import AppState, Message, Turn

router = APIRouter()

MODES = (chat_service.PRE_RETRIEVAL, chat_service.STRICT, chat_service.STREAMING)
_SAVED_PAGE = re.compile(r"wiki/(\S+)\.md")
_PAGE_LINK = re.compile(r'href="/w/[^/"]+/pages/([^"#?]+)"')
MAX_CITED = 3
_EMPTY_CONVERSATION = N_("There is no answered question to save.")


def answer_meta(templates: Jinja2Templates, wiki_id: str, message: Message, rendered: str,
                conversation_tokens: int) -> str:
    """The muted line under an answer: the mode that produced it, the pages it cites, as
    links (the first `MAX_CITED`, in the order the answer names them), and the tokens of
    the turn and of the conversation up to this answer."""
    cited = list(dict.fromkeys(_PAGE_LINK.findall(rendered)))[:MAX_CITED]
    tokens = {"input": format_number(message.input_tokens), "output": format_number(message.output_tokens),
              "turn": format_number(message.tokens), "conversation": format_number(conversation_tokens)}
    return templates.get_template("_chat_meta.html").render(
        wiki_id=wiki_id, mode_label=MODE_LABELS.get(message.mode, ""), cited=cited, tokens=tokens).strip()


def conversation_tokens(messages) -> int:
    """The tokens of the answers of a conversation: input plus output, summed."""
    return sum(m.tokens for m in messages if m.role == "assistant")


def audit_buttons(templates: Jinja2Templates, wiki_id: str, conversation_id: str, messages) -> str:
    """The row of three buttons that open the audit page of the conversation at the last
    turn, as an out-of-band element: hidden while the conversation has no answer."""
    answers = [m for m in messages if m.role == "assistant"]
    return templates.get_template("_audit_buttons.html").render(
        wiki_id=wiki_id, conversation_id=conversation_id, oob=True, turn=len(answers),
        passages=len(answers[-1].context) if answers else 0).strip()


# The modes in which the sentence verification is valid: the third one shows the
# answer as it arrives and checks nothing.
VERIFIABLE_MODES = (chat_service.PRE_RETRIEVAL, chat_service.STRICT)


def _verify_note(text: str, failed: bool = False) -> str:
    return f'<p class="verify-note{" failed" if failed else ""}" role="status">{html.escape(text)}</p>'


def _summary(unsupported: int, checked: int) -> str:
    return _verify_note(ngettext("%(unsupported)s of %(checked)s sentence without support",
                                 "%(unsupported)s of %(checked)s sentences without support",
                                 checked, unsupported=unsupported, checked=checked))


def _error(text: str) -> str:
    return f'<p class="save-msg error">{html.escape(text)}</p>'


@router.post("/w/{wiki_id}/chat", response_class=HTMLResponse)
def chat_post(request: Request, wiki_id: str, message: str = Form(...), conversation_id: str = Form(...),
              mode: str = Form(chat_service.PRE_RETRIEVAL), open_page: str = Form(""),
              verify: str = Form(""),
              state: AppState = Depends(get_state), templates: Jinja2Templates = Depends(get_templates)):
    """Record the question and return the two bubbles; the answer bubble opens
    the SSE stream of this turn."""
    state.get_wiki(wiki_id)
    if mode not in MODES:
        raise HTTPException(status_code=422, detail=_("unknown mode: %(mode)s", mode=mode))
    state.conversation(wiki_id, conversation_id).append(Message("user", message))
    turn_id = state.new_id()
    verifying = bool(verify) and mode in VERIFIABLE_MODES and state.verifier_available()
    state.turns[turn_id] = Turn(conversation_id, mode, open_page or None, verifying)
    return templates.TemplateResponse(request, "chat_turn.html", {
        "wiki_id": wiki_id, "turn_id": turn_id, "question": message})


@router.get("/w/{wiki_id}/chat/{turn_id}/stream")
async def chat_stream(wiki_id: str, turn_id: str, state: AppState = Depends(get_state),
                      templates: Jinja2Templates = Depends(get_templates)):
    """Stream the answer: raw text chunks while it arrives (streaming mode), then
    the rendered answer, which replaces the chunks, then, when the turn asked for
    it, one `verification` event with the answer again and its unsupported
    sentences marked, then `done`."""
    turn = state.turns.pop(turn_id, None)
    if turn is None:
        raise HTTPException(status_code=404, detail=_("That question is no longer in progress."))
    entry = state.get_wiki(wiki_id)
    conversation = state.conversation(wiki_id, turn.conversation_id)
    pages = set(list_pages(entry.wiki))

    async def events():
        full = ""
        result = None
        raw_answer, path = None, ()
        input_tokens = output_tokens = 0
        try:
            agents = state.get_agents(entry)
            context: tuple = ()
            if turn.mode == chat_service.STREAMING:
                async for item in chat_service.chat_turn_stream_with_context(
                    entry.wiki, agents, conversation, conversation_id=turn.conversation_id,
                    open_page=turn.open_page,
                ):
                    if isinstance(item, str):
                        full += item
                        yield {"event": "chunk", "data": html.escape(item)}
                    else:
                        context, path = item.context, item.path
                        input_tokens, output_tokens = item.input_tokens, item.output_tokens
            else:
                # The context is kept for every answer: a page drafted from the
                # conversation is verified against it. The answer itself is
                # verified only when the checkbox asks for it.
                answered = await chat_service.chat_turn_with_context(
                    entry.wiki, agents, conversation, mode=turn.mode,
                    conversation_id=turn.conversation_id, open_page=turn.open_page)
                full, context = answered.answer, answered.context
                raw_answer, path = answered.raw_answer, answered.path
                input_tokens, output_tokens = answered.input_tokens, answered.output_tokens
                result = answered if turn.verify else None
        except Exception as exc:  # noqa: BLE001 — the user sees why there is no answer
            conversation.pop()
            message = html.escape(_("Could not answer: %(error)s", error=exc))
            yield {"event": "chunk",
                   "data": f'<div id="answer-{turn_id}" hx-swap-oob="innerHTML"><p class="error">{message}</p></div>'}
            yield {"event": "done", "data": ""}
            return
        answer = Message("assistant", full, turn.mode, tuple(context), raw_answer, tuple(path),
                         input_tokens, output_tokens)
        conversation.append(answer)
        sources = source_names(entry.wiki)
        rendered = render_markdown(wiki_id, full, None, pages, sources)
        meta = answer_meta(templates, wiki_id, answer, rendered, conversation_tokens(conversation))
        buttons = audit_buttons(templates, wiki_id, turn.conversation_id, conversation)
        pending = _verify_note(_("Verifying the sentences…")) if result is not None else ""
        yield {"event": "chunk",
               "data": f'<div id="answer-{turn_id}" hx-swap-oob="innerHTML">{rendered}{pending}</div>'
                       f'<div id="meta-{turn_id}" hx-swap-oob="innerHTML">{meta}</div>{buttons}'}
        if result is not None:
            try:
                threshold = getattr(agents.config, "verify_threshold", DEFAULT_VERIFY_THRESHOLD)
                report = await asyncio.to_thread(
                    verify_answer, full, list(result.context), state.get_verifier(),
                    threshold=threshold, generated=result.generated)
                marked = render_verified(
                    wiki_id, full, [(s.start, s.end) for s in report.unsupported_sentences()], pages, sources)
                body = marked + _summary(report.unsupported, report.checked)
            except Exception:  # noqa: BLE001 — the answer stays as it is
                body = rendered + _verify_note(_("Could not verify"), failed=True)
            yield {"event": "verification",
                   "data": f'<div id="answer-{turn_id}" hx-swap-oob="innerHTML">{body}</div>'}
        yield {"event": "done", "data": ""}

    return EventSourceResponse(events())


@router.get("/w/{wiki_id}/chat/{conversation_id}/thread", response_class=HTMLResponse)
def chat_thread(request: Request, wiki_id: str, conversation_id: str,
                state: AppState = Depends(get_state), templates: Jinja2Templates = Depends(get_templates)):
    """The messages of a conversation, for a page that loads while the
    conversation exists (the conversation survives navigation)."""
    entry = state.get_wiki(wiki_id)
    pages = set(list_pages(entry.wiki))
    sources = source_names(entry.wiki)
    messages = []
    conversation = state.conversations.get((wiki_id, conversation_id), [])
    running = 0
    for m in conversation:
        if m.role == "user":
            messages.append({"role": "user", "html": m.content, "meta": ""})
            continue
        rendered = render_markdown(wiki_id, m.content, None, pages, sources)
        running += m.tokens
        meta = answer_meta(templates, wiki_id, m, rendered, running) if m.mode else ""
        messages.append({"role": m.role, "html": rendered, "meta": meta})
    prompts = load_config(entry.wiki.path).suggested_prompts
    thread = templates.get_template("_chat_thread.html").render(
        request=request, messages=messages, prompts=prompts)
    return thread + audit_buttons(templates, wiki_id, conversation_id, conversation)


@router.post("/w/{wiki_id}/chat/clear", response_class=HTMLResponse)
def chat_clear(request: Request, wiki_id: str, conversation_id: str = Form(...),
               state: AppState = Depends(get_state), templates: Jinja2Templates = Depends(get_templates)):
    """Forget the conversation's messages; return the empty chat thread."""
    entry = state.get_wiki(wiki_id)
    state.conversations.pop((wiki_id, conversation_id), None)
    prompts = load_config(entry.wiki.path).suggested_prompts
    empty = templates.get_template("_chat_empty.html").render(request=request, prompts=prompts)
    return empty + audit_buttons(templates, wiki_id, conversation_id, [])


@router.post("/w/{wiki_id}/chat/draft", response_class=HTMLResponse)
def chat_draft(request: Request, wiki_id: str, conversation_id: str = Form(...), title: str = Form(...),
               open_page: str = Form(""), state: AppState = Depends(get_state),
               templates: Jinja2Templates = Depends(get_templates)):
    """Draft the concept page of the whole conversation and return the review dialog.

    Step 1 of saving: the model drafts the page; nothing is written. The user edits
    the draft in the dialog, and `chat_save` writes what the user approved.
    """
    title = title.strip()
    if not title:
        return _error(_("Enter a title for the page."))
    entry = state.get_wiki(wiki_id)
    s = state.settings
    try:
        draft = chat_service.draft_conversation(
            entry.wiki, state.get_agents(entry).config, title,
            state.conversations.get((wiki_id, conversation_id), []),
            base_url=s.llm_base_url, api_key=s.llm_api_key, model=s.llm_model)
    except chat_service.EmptyConversation:
        return _error(_(_EMPTY_CONVERSATION))
    except Exception as exc:  # noqa: BLE001 — the user sees why the draft failed
        return _error(_("Could not prepare the draft: %(error)s", error=exc))
    return templates.TemplateResponse(request, "_review_dialog.html", {
        "wiki_id": wiki_id, "conversation_id": conversation_id, "open_page": open_page,
        "title": title, "draft": draft, "body": split_frontmatter(draft.markdown)[1].strip(),
        "verifier_available": state.verifier_available()})


@router.post("/w/{wiki_id}/chat/preview", response_class=HTMLResponse)
def chat_preview(wiki_id: str, markdown: str = Form(""), page: str = Form(""),
                 state: AppState = Depends(get_state)):
    """The draft rendered as the page will look, with `web/render.py`."""
    entry = state.get_wiki(wiki_id)
    return render_markdown(wiki_id, markdown, page or None, list_pages(entry.wiki), source_names(entry.wiki))


@router.post("/w/{wiki_id}/chat/verify-draft", response_class=HTMLResponse)
async def chat_verify_draft(wiki_id: str, conversation_id: str = Form(...), markdown: str = Form(""),
                            page: str = Form(""), state: AppState = Depends(get_state)):
    """The draft of the review dialog rendered with the sentences that no passage the
    model read during the conversation supports, and a summary note. Nothing is
    written; the draft is verified as it is in the editor."""
    entry = state.get_wiki(wiki_id)
    pages, sources = list_pages(entry.wiki), source_names(entry.wiki)
    rendered = render_markdown(wiki_id, markdown, page or None, pages, sources)
    if not state.verifier_available():
        return rendered + _verify_note(_("The local verifier is not installed."), failed=True)
    passages = tuple(p for m in state.conversations.get((wiki_id, conversation_id), [])
                     if m.role == "assistant" for p in m.context)
    if not passages:
        return rendered + _verify_note(_("The conversation kept no passage to verify the draft against."))
    try:
        agents = state.get_agents(entry)
        threshold = getattr(agents.config, "verify_threshold", DEFAULT_VERIFY_THRESHOLD)
        report = await asyncio.to_thread(verify_answer, markdown, list(passages), state.get_verifier(),
                                         threshold=threshold, language=entry.wiki.language)
    except Exception:  # noqa: BLE001 — the draft stays as it is
        return rendered + _verify_note(_("Could not verify"), failed=True)
    marked = render_verified(wiki_id, markdown, [(s.start, s.end) for s in report.unsupported_sentences()],
                             pages, sources)
    return marked + _summary(report.unsupported, report.checked)


@router.post("/w/{wiki_id}/chat/save", response_class=HTMLResponse)
def chat_save(request: Request, wiki_id: str, response: Response, title: str = Form(...),
              markdown: str = Form(...), open_page: str = Form(""),
              state: AppState = Depends(get_state), templates: Jinja2Templates = Depends(get_templates)):
    """Write the page the user approved in the review dialog, exactly as edited.

    On success return the notice, and the index column again, out of band, so the
    new page is listed at once; the notice replaces the dialog (`HX-Retarget`). On
    failure the message stays inside the dialog, which keeps the user's edits.
    """
    title = title.strip()
    if not title or not markdown.strip():
        return _error(_("The page cannot be empty."))
    entry = state.get_wiki(wiki_id)
    if not entry.busy.acquire(blocking=False):
        return _error(_(BUSY))
    try:
        result = chat_service.save_reviewed_page(entry.wiki, title, markdown)
    except Exception as exc:  # noqa: BLE001 — the user sees why the save failed
        return _error(_("Could not save: %(error)s", error=exc))
    finally:
        entry.busy.release()
    response.headers["HX-Retarget"] = "#save-result"
    response.headers["HX-Reswap"] = "innerHTML"
    saved = _SAVED_PAGE.search(result)
    updated = result.startswith("Updated")
    text = (_("Updated the page “%(title)s”.", title=title) if updated
            else _("Created the page “%(title)s”.", title=title))
    link = f' <a href="{page_url(wiki_id, saved.group(1))}">{html.escape(_("Open the page"))}</a>' if saved else ""
    notice = f'<p class="save-msg ok">{html.escape(text)}{link}</p>'
    index = templates.get_template("_index_groups.html").render(
        wiki_id=wiki_id, page=open_page, groups=index_groups(entry, list_pages(entry.wiki)))
    return notice + f'<div id="index-groups" hx-swap-oob="innerHTML">{index}</div>'
