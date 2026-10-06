"""The in-memory state of one application: open wikis, conversations, turns and
running operations.

One `AppState` belongs to one application (`web.app.create_app`), so a test
builds an application with an empty state. The state is lost on restart; the
wikis on disk are not affected.
"""

from __future__ import annotations

import contextvars
import queue
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from domain.wiki_registry import discover_wikis, is_wiki_dir, load_recent, merge_options
from services import chat as chat_service
from services.wiki import Wiki, open_wiki
from web.i18n import _
from web.settings import WebSettings

AgentsFactory = Callable[[Wiki], chat_service.ChatAgents]


@dataclass
class Message:
    role: str
    content: str
    mode: str = ""   # of an assistant message: the chat mode that produced it


@dataclass
class OpenWiki:
    """A wiki opened by the application, with its agents and its lock.

    `busy` serializes the operations that write to the wiki: one runs at a time
    per wiki, as in the ingest app.
    """

    wiki: Wiki
    agents: chat_service.ChatAgents | None = None
    busy: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class Turn:
    """A chat question recorded and not yet answered."""

    conversation_id: str
    mode: str
    open_page: str | None


@dataclass
class Operation:
    """A running operation: the thread pushes its lines, the SSE route pops them.
    `None` closes the stream."""

    lines: queue.Queue[str | None] = field(default_factory=queue.Queue)


class LLMNotConfigured(RuntimeError):
    """A model setting is blank. The text is shown to the user, in the interface language."""


def _require(prefix: str, **fields: str) -> None:
    missing = [name for name, value in fields.items() if not (value or "").strip()]
    if missing:
        raise LLMNotConfigured(
            _("The model is not configured: define %(names)s in the .env file "
              "(for ingestion the WIKI_LLM_* variables also work).",
              names=", ".join(f"{prefix}_{n}" for n in missing)))


class UnknownWiki(KeyError):
    """The URL names a wiki that is not among the picker's options."""


class AppState:
    def __init__(self, settings: WebSettings, agents_factory: AgentsFactory | None = None) -> None:
        self.settings = settings
        self._agents_factory = agents_factory or self._build_agents
        self._open: dict[str, OpenWiki] = {}
        # Keyed by (wiki id, conversation id): a conversation belongs to one wiki.
        self.conversations: dict[tuple[str, str], list[Message]] = {}
        self.turns: dict[str, Turn] = {}
        self.operations: dict[str, Operation] = {}

    # ── Wikis ────────────────────────────────────────────────────────────────

    def wiki_options(self) -> dict[str, str]:
        """URL id → wiki path, for every discovered and recent wiki.

        The id is the folder name, with a numeric suffix when two folders share it.
        A recent folder that is no longer a wiki (`is_wiki_dir`) is left out.
        """
        options: dict[str, str] = {}
        recent = [p for p in load_recent(self.settings.recent_file) if is_wiki_dir(Path(p))]
        for path in merge_options(self.settings.wiki_home, recent, self.settings.wiki_path):
            base = Path(path).name or "wiki"
            wiki_id, n = base, 2
            while wiki_id in options:
                wiki_id, n = f"{base}-{n}", n + 1
            options[wiki_id] = path
        return options

    def recent_only(self) -> set[str]:
        """The listed paths that come only from the recent list: the ones the owner can
        take out of it. Wikis found under WIKI_HOME and the default wiki come back on
        the next listing, so they are not here."""
        fixed = set(discover_wikis(self.settings.wiki_home)) | ({self.settings.wiki_path} if self.settings.wiki_path else set())
        return {p for p in load_recent(self.settings.recent_file) if p not in fixed}

    def get_wiki(self, wiki_id: str) -> OpenWiki:
        """The wiki of a URL id, opened on first use. Raises `UnknownWiki`.

        A folder that stopped being a wiki is refused, not recreated, even when it was
        open; the default wiki of WIKI_PATH is the exception, created on first use."""
        path = self.wiki_options().get(wiki_id)
        if path is None or (path != self.settings.wiki_path and not is_wiki_dir(Path(path))):
            self._open.pop(wiki_id, None)
            raise UnknownWiki(wiki_id)
        if wiki_id not in self._open:
            self._open[wiki_id] = OpenWiki(wiki=open_wiki(path))
        return self._open[wiki_id]

    def get_agents(self, entry: OpenWiki) -> chat_service.ChatAgents:
        """The wiki's agents, built on first use and kept."""
        if entry.agents is None:
            entry.agents = self._agents_factory(entry.wiki)
        return entry.agents

    def _build_agents(self, wiki: Wiki) -> chat_service.ChatAgents:
        s = self.settings
        _require("LLM", BASE_URL=s.llm_base_url, API_KEY=s.llm_api_key, MODEL=s.llm_model)
        return chat_service.build_agents(wiki, s.llm_base_url, s.llm_api_key, s.llm_model)

    def ingest_model(self) -> tuple[str, str]:
        """The model and the base URL ingestion uses; no client is built."""
        return self.settings.ingest_model, self.settings.ingest_base_url

    def ingest_llm(self):
        """The client, the model and the base URL ingestion uses.
        Raises `LLMNotConfigured` when a setting is blank."""
        from openai import OpenAI

        s = self.settings
        _require("LLM", BASE_URL=s.ingest_base_url, API_KEY=s.ingest_api_key, MODEL=s.ingest_model)
        return OpenAI(base_url=s.ingest_base_url, api_key=s.ingest_api_key), s.ingest_model, s.ingest_base_url

    # ── Conversations ────────────────────────────────────────────────────────

    def conversation(self, wiki_id: str, conversation_id: str) -> list[Message]:
        return self.conversations.setdefault((wiki_id, conversation_id), [])

    def new_id(self) -> str:
        return uuid.uuid4().hex

    # ── Operations ───────────────────────────────────────────────────────────

    def start_operation(self, entry: OpenWiki, work: Callable[[Callable[[str], None]], str]) -> str | None:
        """Run `work(progress)` in a thread under the wiki's lock; return the
        operation id whose events stream the progress, or None when the wiki is
        busy. `work` returns its closing line."""
        if not entry.busy.acquire(blocking=False):
            return None
        operation = Operation()
        op_id = self.new_id()
        self.operations[op_id] = operation

        def run() -> None:
            try:
                operation.lines.put(work(operation.lines.put))
            except Exception as exc:  # noqa: BLE001 — reported to the page
                operation.lines.put(f"❌ {exc}")
            finally:
                operation.lines.put(None)
                entry.busy.release()

        # The thread runs in the context of the request, so `_()` writes the lines in its language.
        threading.Thread(target=contextvars.copy_context().run, args=(run,), daemon=True).start()
        return op_id
