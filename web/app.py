"""The application factory and the module uvicorn loads.

    uv run --group web uvicorn web.app:app --port 8765
"""

from __future__ import annotations

import html
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "base") not in sys.path:
    sys.path.insert(0, str(ROOT / "base"))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse, PlainTextResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.datastructures import MutableHeaders  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

from domain.tools.db import SchemaMismatchError  # noqa: E402
from web import i18n  # noqa: E402
from web.deps import status_label  # noqa: E402
from web.i18n import _  # noqa: E402
from web.routes import chat, history, ingest, lang, pages, picker, relations, vocabulary  # noqa: E402
from web.settings import WebSettings  # noqa: E402
from web.state import AgentsFactory, AppState, UnknownWiki  # noqa: E402

_HERE = Path(__file__).parent


class InterfaceLanguage:
    """Set the interface language of each request before the route runs, in the context the route, its
    thread-pool call and its templates inherit, and mark the response as varying with what chose it."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        i18n.set_language(i18n.negotiate(request.cookies.get(i18n.COOKIE), request.headers.get("accept-language")))

        async def send_with_vary(message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append("Vary", "Cookie, Accept-Language")
            await send(message)

        await self.app(scope, receive, send_with_vary)


def current_url(request: Request) -> str:
    """The URL of the page being shown, for the language switch to return to. A
    one-time `notice` is left out: it was written in the language of the action."""
    query = urllib.parse.urlencode([(k, v) for k, v in request.query_params.multi_items() if k != "notice"])
    return request.url.path + (f"?{query}" if query else "")


def make_templates() -> Jinja2Templates:
    """The Jinja2 templates with the translation callables installed. They read the
    language of the running request (`web.i18n`), so one environment serves every language."""
    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    templates.env.add_extension("jinja2.ext.i18n")
    templates.env.policies["ext.i18n.trimmed"] = True   # a `{% trans %}` block collapses its whitespace, as the extraction does
    templates.env.install_gettext_callables(i18n.raw_gettext, i18n.raw_ngettext, newstyle=True,
                                            pgettext=i18n.raw_pgettext, npgettext=i18n.raw_npgettext)
    templates.env.globals.update(languages=i18n.LANGUAGES, status_label=status_label, get_language=i18n.get_language, current_url=current_url)
    return templates


class RevalidatedStaticFiles(StaticFiles):
    """The stylesheets and scripts, sent with ``Cache-Control: no-cache``.

    Without that header a browser reuses a cached file without asking, so a changed
    stylesheet is not seen until the cache expires. With it, the browser revalidates
    each file with its ETag and receives ``304 Not Modified`` when nothing changed.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(settings: WebSettings | None = None, *, agents_factory: AgentsFactory | None = None) -> FastAPI:
    """Build an application with its own empty state.

    `agents_factory` builds the chat agents of a wiki; the default builds the real
    ones from the LLM settings. A test passes simulated agents.
    """
    app = FastAPI(title="llmwiki")
    app.state.web = AppState(settings or WebSettings.from_env(), agents_factory)
    app.state.templates = make_templates()
    app.mount("/static", RevalidatedStaticFiles(directory=str(_HERE / "static")), name="static")
    for module in (picker, pages, chat, ingest, history, relations, vocabulary, lang):
        app.include_router(module.router)

    app.add_middleware(InterfaceLanguage)

    @app.exception_handler(UnknownWiki)
    async def unknown_wiki(request: Request, exc: UnknownWiki) -> HTMLResponse:
        # A wiki that is not listed, or a folder that stopped being a wiki: never recreated.
        text = html.escape(_("That wiki does not exist: %(wiki)s", wiki=exc.args[0]))
        back = html.escape(_("Back to the list of wikis"))
        return HTMLResponse(f'<!doctype html><meta charset="utf-8"><p>{text}</p><p><a href="/">{back}</a></p>',
                            status_code=404)

    @app.exception_handler(SchemaMismatchError)
    async def schema_mismatch(request: Request, exc: SchemaMismatchError) -> PlainTextResponse:
        # The index of a wiki is older than the schema: say so, not a traceback.
        return PlainTextResponse(_("Cannot open the index of this wiki.") + f"\n\n{exc}", status_code=500)

    return app


app = create_app()
