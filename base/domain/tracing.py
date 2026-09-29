"""Opt-in tracing by diagram node, written as OpenTelemetry spans.

One trace records one run from beginning to end: one chat turn, or one
ingestion (a single file or a batch). Each step of the code writes one span
named by the identifier of its node in the design diagrams — ``Q5`` for the
coverage-roster check, ``I4`` for text extraction, ``W1`` for chunking — so the
span names of one trace are the path the run took through the diagram.

Enabled with ``WIKI_TRACE=1``; a no-op otherwise. Spans are appended to
``<workspace>/.llmwiki/traces/spans.jsonl``, one JSON object per line, in the
format of ``ReadableSpan.to_json``. Heavy payloads (extracted text, fragments,
prompts, responses, markdown) go to content-addressed files under
``<workspace>/.llmwiki/traces/payloads/``; the span carries their sha256, size
and relative path. ``WIKI_TRACE_CAPTURE`` selects the payload channels:
``all`` (default), ``none``, or a comma list of the names in ``CHANNELS``.

The model calls of the chat agent are written by Pydantic AI's own
instrumentation (``Agent.instrument_all``), as children of the node that ran
the agent. The model calls of ingestion go through an OpenAI client, which
``wrap_openai`` wraps so that each call is a span with the ``gen_ai.*``
attribute names of the OpenTelemetry GenAI conventions.

Tracing is best-effort: a failure to write a span never breaks a chat turn or
an ingestion.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import threading
from contextvars import ContextVar
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider

logger = logging.getLogger(__name__)

ENV_VAR = "WIKI_TRACE"
CAPTURE_ENV_VAR = "WIKI_TRACE_CAPTURE"
SPAN_FILE = "spans.jsonl"
CHANNELS = frozenset({"extracted_text", "chunks", "prompts", "responses", "markdown"})

READING = "reading"
WRITING = "writing"

_PREFIX = "llmwiki."
_WORKSPACE_ATTR = _PREFIX + "workspace"

# The workspace of the run in progress, for payload files.
_workspace: ContextVar[Path | None] = ContextVar("_tracing_workspace", default=None)

_provider: TracerProvider | None = None
_provider_lock = threading.Lock()


# ── Activation ────────────────────────────────────────────────────────────────

_OFF = {"", "0", "false", "no", "off"}


def enabled() -> bool:
    """True when ``WIKI_TRACE`` is set, and not to 0, false, no or off."""
    return os.environ.get(ENV_VAR, "").strip().lower() not in _OFF


def _channels() -> frozenset[str]:
    raw = os.environ.get(CAPTURE_ENV_VAR)
    if raw is None:
        return CHANNELS
    raw = raw.strip().lower()
    if raw in ("", "none"):
        return frozenset()
    if raw == "all":
        return CHANNELS
    requested = {c.strip() for c in raw.split(",") if c.strip()}
    unknown = requested - CHANNELS
    if unknown:
        logger.warning("%s: ignoring unknown channel(s): %s", CAPTURE_ENV_VAR, sorted(unknown))
    return frozenset(requested & CHANNELS)


# ── Span file ─────────────────────────────────────────────────────────────────

class _WorkspaceFileProcessor(SpanProcessor):
    """Appends every span of a trace to the span file of that trace's workspace.

    The root span of a trace carries ``llmwiki.workspace``; the spans below it,
    including Pydantic AI's, do not. The processor maps each trace id to its
    workspace when the root span starts, and forgets it when the root ends.
    """

    def __init__(self) -> None:
        self._files: dict[int, Path] = {}
        self._lock = threading.Lock()

    def on_start(self, span: Span, parent_context: Any = None) -> None:
        workspace = (span.attributes or {}).get(_WORKSPACE_ATTR)
        if workspace:
            path = Path(str(workspace)) / ".llmwiki" / "traces" / SPAN_FILE
            with self._lock:
                self._files.setdefault(span.context.trace_id, path)

    def on_end(self, span: ReadableSpan) -> None:
        trace_id = span.context.trace_id
        with self._lock:
            path = self._files.get(trace_id)
            if span.parent is None:
                self._files.pop(trace_id, None)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(span.to_json(indent=None) + "\n")
        except Exception:  # noqa: BLE001 — tracing is best-effort
            logger.debug("span write failed", exc_info=True)

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _get_provider() -> TracerProvider:
    """The process provider, built once; also instruments every Pydantic AI agent."""
    global _provider
    with _provider_lock:
        if _provider is None:
            provider = TracerProvider()
            provider.add_span_processor(_WorkspaceFileProcessor())
            try:
                from pydantic_ai import Agent
                from pydantic_ai.models.instrumented import InstrumentationSettings

                Agent.instrument_all(
                    InstrumentationSettings(tracer_provider=provider, include_content=True)
                )
            except Exception:  # noqa: BLE001 — model spans are optional
                logger.debug("pydantic-ai instrumentation failed", exc_info=True)
            _provider = provider
        return _provider


def _tracer() -> trace.Tracer:
    if not enabled():
        return trace.NoOpTracer()
    return _get_provider().get_tracer("llmwiki")


# ── Attributes ────────────────────────────────────────────────────────────────

def _attribute_value(value: Any) -> Any:
    """Coerce a value to a type an OpenTelemetry attribute accepts."""
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        return list(value)
    if isinstance(value, (list, tuple)) and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
    ):
        return list(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _set(span: trace.Span, attributes: dict[str, Any], prefix: str = _PREFIX) -> None:
    try:
        for key, value in attributes.items():
            if value is None:
                continue
            span.set_attribute(prefix + key, _attribute_value(value))
    except Exception:  # noqa: BLE001 — tracing is best-effort
        logger.debug("span attribute failed", exc_info=True)


class Node:
    """The open span of one diagram node."""

    def __init__(self, span: trace.Span) -> None:
        self.span = span

    def set(self, **attributes: Any) -> None:
        """Record values the node computed, under ``llmwiki.<name>``."""
        _set(self.span, attributes)

    def payload(self, name: str, content: str, *, channel: str, ext: str = "txt") -> None:
        """Store heavy content in a payload file and record its sha256, size and path."""
        if not self.span.is_recording():
            return
        try:
            data = content.encode("utf-8")
            sha = hashlib.sha256(data).hexdigest()
            fields: dict[str, Any] = {f"{name}.sha256": sha, f"{name}.bytes": len(data)}
            workspace = _workspace.get()
            if workspace is not None and channel in _channels():
                payload_dir = workspace / ".llmwiki" / "traces" / "payloads"
                payload_dir.mkdir(parents=True, exist_ok=True)
                path = payload_dir / f"{sha}.{ext}"
                if not path.exists():
                    path.write_bytes(data)
                fields[f"{name}.ref"] = f"payloads/{sha}.{ext}"
            _set(self.span, fields)
        except Exception:  # noqa: BLE001 — tracing is best-effort
            logger.debug("span payload failed", exc_info=True)


# ── Spans ─────────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def _span(name: str, attributes: dict[str, Any]) -> Iterator[Node]:
    # The attributes go in at start: the file processor reads the root's
    # workspace in on_start, before any later set_attribute.
    start_attrs = {
        _PREFIX + key: _attribute_value(value)
        for key, value in attributes.items() if value is not None
    }
    tracer = _tracer()
    with tracer.start_as_current_span(
        name, attributes=start_attrs, record_exception=True,
    ) as span:
        yield Node(span)


@contextlib.contextmanager
def root(name: str, workspace: Path | str | None, **attributes: Any) -> Iterator[Node]:
    """Open the root span of a run (``turn`` or ``ingest``), unless one is open.

    Inside a run already in progress — a batch that ingests several files, or an
    app that opened the turn before calling the engine — the current span is
    reused and receives the attributes, so the caller's spans stay in one trace.
    """
    current = trace.get_current_span()
    if current.get_span_context().is_valid:
        node_obj = Node(current)
        node_obj.set(**attributes)
        yield node_obj
        return
    if workspace is None:
        with _span(name, attributes) as node_obj:
            yield node_obj
        return
    workspace_path = Path(workspace)
    token = _workspace.set(workspace_path)
    try:
        attrs = {"workspace": str(workspace_path), **attributes}
        with _span(name, attrs) as node_obj:
            yield node_obj
    finally:
        _workspace.reset(token)


@contextlib.contextmanager
def node(node_id: str, diagram: str, **attributes: Any) -> Iterator[Node]:
    """Open the span of one diagram node, named by its identifier."""
    with _span(node_id, {"node": node_id, "diagram": diagram, **attributes}) as node_obj:
        yield node_obj


def mark(node_id: str, diagram: str, **attributes: Any) -> None:
    """Record a node that is a decision or a value, with no work of its own."""
    with node(node_id, diagram, **attributes):
        pass


# ── OpenAI client proxy (ingestion model calls) ───────────────────────────────

class _TracedCompletions:
    def __init__(self, real: Any) -> None:
        self._real = real

    def create(self, *args: Any, **kwargs: Any) -> Any:
        model = kwargs.get("model")
        attrs = {"gen_ai.operation.name": "chat", "gen_ai.system": "openai"}
        if model:
            attrs["gen_ai.request.model"] = str(model)
        with _tracer().start_as_current_span(
            f"chat {model or ''}".strip(), attributes=attrs, record_exception=True,
        ) as span:
            node_obj = Node(span)
            t0 = perf_counter()
            resp = self._real.create(*args, **kwargs)
            try:
                usage = getattr(resp, "usage", None)
                if usage is not None:
                    _set(span, {
                        "gen_ai.usage.input_tokens": getattr(usage, "prompt_tokens", None),
                        "gen_ai.usage.output_tokens": getattr(usage, "completion_tokens", None),
                    }, prefix="")
                resp_model = getattr(resp, "model", None)
                if resp_model:
                    span.set_attribute("gen_ai.response.model", str(resp_model))
                node_obj.set(latency_ms=round((perf_counter() - t0) * 1000, 1))
                messages = kwargs.get("messages")
                if messages is not None:
                    node_obj.payload(
                        "prompt", json.dumps(messages, ensure_ascii=False, indent=2),
                        channel="prompts", ext="json",
                    )
                content = resp.choices[0].message.content or ""
                node_obj.payload("response", content, channel="responses")
            except Exception:  # noqa: BLE001 — tracing is best-effort
                logger.debug("model call span failed", exc_info=True)
            return resp

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _TracedChat:
    def __init__(self, real_client: Any) -> None:
        self._real = real_client
        self.completions = _TracedCompletions(real_client.chat.completions)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real.chat, name)


class _TracedClient:
    """Wraps an OpenAI-compatible client; every ``chat.completions.create`` is a span.

    Every other attribute is delegated to the real client, and the real response
    object is returned untouched.
    """

    _llmwiki_traced = True

    def __init__(self, real: Any) -> None:
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "chat", _TracedChat(real))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def wrap_openai(client: Any) -> Any:
    """Return ``client`` wrapped so its model calls are spans; idempotent.

    With tracing off the client is returned unchanged.
    """
    if client is None or not enabled() or getattr(client, "_llmwiki_traced", False):
        return client
    return _TracedClient(client)
