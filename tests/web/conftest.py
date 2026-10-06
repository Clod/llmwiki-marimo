"""Fixtures of the web tests: a small wiki, simulated agents, no model call.

The wiki is built with `services.wiki.open_wiki` and
`domain.tools.wiki_fs.create_page`. The agents are simulated as in
`tests/unit/test_services.py`; the OpenAI client is never reached because every
function that would call it is replaced by the test.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from domain.tools.wiki_fs import create_page
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from services import chat
from services.wiki import open_wiki, save_page
from web import i18n
from web.app import create_app
from web.settings import WebSettings

WIKI_ID = "tales"
SPANISH = {i18n.COOKIE: "es"}   # the cookie of the Spanish interface


class FakeAgent:
    """Answers with `output`; in the streaming mode, with `output` in two chunks.

    `output` is a string, or a function of the prompt that returns one.
    """

    delay = 0.0        # seconds between the two chunks of a streamed answer
    grounded = False   # True: the run holds a tool result, so the strict mode keeps the answer

    def __init__(self, output) -> None:
        self.output = output
        self.prompts: list[str] = []

    def _messages(self) -> list:
        if not self.grounded:
            return []
        part = ToolReturnPart(tool_name="read_wiki_page", content="Texto de la página de la wiki.",
                              tool_call_id="call-1")
        return [ModelRequest(parts=[part])]

    def _text(self, prompt: str) -> str:
        return self.output(prompt) if callable(self.output) else self.output

    async def run(self, prompt, deps, message_history):
        self.prompts.append(prompt)
        return SimpleNamespace(output=self._text(prompt), all_messages=self._messages)

    @asynccontextmanager
    async def run_stream(self, prompt, deps, message_history):
        self.prompts.append(prompt)
        text = self._text(prompt)
        half = len(text) // 2

        async def stream_text(delta=True):
            yield text[:half]
            await asyncio.sleep(self.delay)
            yield text[half:]

        yield SimpleNamespace(stream_text=stream_text)


def fake_agents(output, language: str = "en") -> chat.ChatAgents:
    config = SimpleNamespace(language=language, off_limits=[], data_aliases={}, system_prompt="",
                             suggested_prompts=["¿Quién perdió el zapato?"])
    return chat.ChatAgents(config=config, agent=FakeAgent(output), agent_pre_retrieval=FakeAgent(output))


@pytest.fixture(autouse=True)
def no_libreoffice_probe(monkeypatch):
    """The ingestion screen probes LibreOffice; no web test runs it for real."""
    from domain.ingestion import extractor

    monkeypatch.setattr(extractor, "_probe_results", {})
    monkeypatch.setattr(extractor, "_probe_conversion", lambda lo: False)


@pytest.fixture
def home(tmp_path):
    """A folder with one wiki, `tales`: three fixed pages, two concepts and a summary."""
    root = tmp_path / "home" / WIKI_ID
    wiki = open_wiki(root)
    (wiki.wiki_dir / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    (wiki.wiki_dir / "overview.md").write_text("# Overview\n\nTales about slippers.\n", encoding="utf-8")
    (wiki.wiki_dir / "log.md").write_text("# Ingest Log\n", encoding="utf-8")
    body = "Cinderella lost a glass slipper at the royal ball and the prince searched the kingdom. " * 3
    create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "cinderella", "Cinderella", body, [])
    create_page(wiki.db_path, wiki.path, "/wiki/concepts/", "prince", "Prince", body, [])
    create_page(wiki.db_path, wiki.path, "/wiki/summaries/", "tale", "Tale", body, [])
    # `save_page` rebuilds the page's references, as the editor does.
    save_page(wiki, "concepts/cinderella",
              body + "\n\nSee [the prince](prince.md) and [the tale](../summaries/tale.md).", None)
    return root.parent


@pytest.fixture
def settings(home, tmp_path):
    return WebSettings(
        wiki_path=None, wiki_home=home, recent_file=tmp_path / "recent_wikis.json",
        llm_base_url="http://llm.invalid/v1", llm_api_key="key", llm_model="model",
        ingest_base_url="http://llm.invalid/v1", ingest_api_key="key", ingest_model="model",
    )


@pytest.fixture
def agents():
    return fake_agents("Cinderella lost a glass slipper.")


@pytest.fixture
def app(settings, agents):
    return create_app(settings, agents_factory=lambda wiki: agents)


@pytest.fixture(autouse=True)
def spanish_interface():
    """The tests of the screens assert Spanish texts: they run with the Spanish interface, which
    is also the language a module tested alone (`web/render.py`) writes in."""
    with i18n.use_language("es"):
        yield


@pytest.fixture
def client(app):
    """A browser with the Spanish interface: the `ui_lang` cookie is set."""
    return TestClient(app, follow_redirects=False, cookies=SPANISH)


@pytest.fixture
def en_client(app):
    """A browser with the English interface."""
    return TestClient(app, follow_redirects=False, cookies={i18n.COOKIE: "en"})


@pytest.fixture
def wiki(app):
    """The `tales` wiki as the application opened it."""
    return app.state.web.get_wiki(WIKI_ID).wiki


def sse_events(body: str) -> list[tuple[str, str]]:
    """The (event, data) pairs of a finished SSE response body."""
    events = []
    for block in re.split(r"\r?\n\r?\n", body.strip()):
        name, data = "message", []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
        if data or name != "message":
            events.append((name, "\n".join(data)))
    return events
