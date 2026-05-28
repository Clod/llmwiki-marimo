"""Opt-in ingestion trace: record every LLM exchange and the data-flow path.

This is a WRITE-ONLY observability layer for manual testing — NOT a replay or
regression mechanism (the record/replay "cassette" idea was rejected because a
prompt change would invalidate it). Nothing here ever asserts or replays.

What it produces (when ``WIKI_TRACE=1``):

    <workspace>/.llmwiki/traces/<run_id>/
        trace.jsonl          one self-describing JSON event per line; line 1 is a
                             `meta` header carrying a db_join_map so an LLM can
                             join the trace against the SQLite database.
        payloads/<sha>.<ext> content-addressed sidecar files for heavy payloads
                             (prompts, responses, extracted text, chunks, markdown).

Design:
  * Every ``client.chat.completions.create`` call is captured by a transparent
    proxy (``tracer.wrap(client)``) — zero changes to call sites, the real
    response object is returned untouched.
  * Pipeline stages emit ``stage_*``/``artifact`` events tagged with the current
    document_id + stage (via contextvars) so one PDF can be followed end to end.
  * Payload channels are independently "unpluggable" (``WIKI_TRACE_CAPTURE``):
    when a channel is off the event STILL records sha256 + byte size (so structure
    stays verifiable) but writes no sidecar.
  * Disabled is a true no-op: ``NULL`` tracer, ``wrap()`` returns the client
    unchanged, no files written.

Activation:
    WIKI_TRACE=1                      turn the trace on
    WIKI_TRACE_CAPTURE=all            (default) capture every payload channel
    WIKI_TRACE_CAPTURE=none           core trace only (metadata + hashes)
    WIKI_TRACE_CAPTURE=prompts,chunks comma list of any of:
                                      extracted_text, chunks, prompts, responses, markdown
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Independently switchable payload channels.
CHANNELS = frozenset(
    {"extracted_text", "chunks", "prompts", "responses", "markdown"}
)

# Maps trace event fields to the DB columns they correspond to, so an analyzing
# LLM can join trace.jsonl against the SQLite index without guessing.
DB_JOIN_MAP = {
    "document_id": "documents.id",
    "relative_path": "documents.relative_path",
    "filename": "documents.filename",
    "status": "documents.status",
    "page": "document_pages.page",
    "chunk_index": "document_chunks.chunk_index",
    "reference.source_document_id": "document_references.source_document_id",
    "reference.target_document_id": "document_references.target_document_id",
    "reference.reference_type": "document_references.reference_type",
}

# Active run + correlation context. The client proxy reads these at call time so
# llm_call events are tagged without threading the tracer through signatures.
_active: ContextVar["IngestionTracer | None"] = ContextVar("_active", default=None)
_current_doc: ContextVar[dict | None] = ContextVar("_current_doc", default=None)
_current_stage: ContextVar[str | None] = ContextVar("_current_stage", default=None)


# ── Environment ────────────────────────────────────────────────────────────────

def _enabled() -> bool:
    return os.environ.get("WIKI_TRACE", "").strip() not in ("", "0", "false", "False")


def _channels() -> frozenset[str]:
    """Resolve WIKI_TRACE_CAPTURE into a set of enabled payload channels."""
    raw = os.environ.get("WIKI_TRACE_CAPTURE")
    if raw is None:
        return CHANNELS  # default: capture everything
    raw = raw.strip().lower()
    if raw in ("", "none"):
        return frozenset()
    if raw == "all":
        return CHANNELS
    requested = {c.strip() for c in raw.split(",") if c.strip()}
    unknown = requested - CHANNELS
    if unknown:
        logger.warning("WIKI_TRACE_CAPTURE: ignoring unknown channel(s): %s", sorted(unknown))
    return frozenset(requested & CHANNELS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _jsonable(value: Any) -> Any:
    """Best-effort coerce a value to something json.dumps can serialize."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _usage(resp: Any) -> dict | None:
    u = getattr(resp, "usage", None)
    if u is None:
        return None
    return {
        "prompt_tokens": getattr(u, "prompt_tokens", None),
        "completion_tokens": getattr(u, "completion_tokens", None),
        "total_tokens": getattr(u, "total_tokens", None),
    }


# ── Null tracer (disabled path: every method a no-op) ───────────────────────────

class NullTracer:
    enabled = False

    def wrap(self, client: Any) -> Any:
        return client

    @contextlib.contextmanager
    def stage(self, name: str, **meta: Any) -> Iterator[None]:
        yield

    def document_start(self, *args: Any, **kwargs: Any) -> None:
        pass

    def document_end(self, *args: Any, **kwargs: Any) -> None:
        pass

    def artifact(self, *args: Any, **kwargs: Any) -> None:
        pass

    def emit(self, *args: Any, **kwargs: Any) -> None:
        pass

    def finish(self) -> None:
        pass


NULL = NullTracer()


# ── Real tracer ─────────────────────────────────────────────────────────────────

class IngestionTracer:
    """Writes a JSONL event stream + content-addressed sidecars for one run."""

    enabled = True

    def __init__(self, workspace: Path, db_path: str | None) -> None:
        self.run_id = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        )
        self.workspace = Path(workspace)
        self.db_path = db_path
        self.channels = _channels()
        self.run_dir = self.workspace / ".llmwiki" / "traces" / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.payload_dir = self.run_dir / "payloads"
        self._seq = 0
        self._doc_token = None
        self._fh = (self.run_dir / "trace.jsonl").open("a", encoding="utf-8")
        self._emit_meta()
        self.emit("run_start", workspace=str(self.workspace), db_path=db_path)

    # -- low-level writing --------------------------------------------------------

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _write(self, record: dict) -> None:
        try:
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fh.flush()  # crash-safe during long manual runs
        except Exception:  # never let tracing break ingestion
            logger.debug("trace write failed", exc_info=True)

    def _emit_meta(self) -> None:
        self._write({
            "seq": self._next_seq(),
            "ts": _now_iso(),
            "event": "meta",
            "run_id": self.run_id,
            "schema_version": SCHEMA_VERSION,
            "workspace": str(self.workspace),
            "db_path": self.db_path,
            "capture": sorted(self.channels),
            "channels_available": sorted(CHANNELS),
            "db_join_map": DB_JOIN_MAP,
        })

    def emit(self, event: str, **fields: Any) -> None:
        record: dict[str, Any] = {
            "seq": self._next_seq(),
            "ts": _now_iso(),
            "event": event,
            "run_id": self.run_id,
        }
        doc = _current_doc.get()
        if doc:
            record["document_id"] = doc.get("document_id")
            record["relative_path"] = doc.get("relative_path")
        stage = _current_stage.get()
        if stage:
            record["stage"] = stage
        record.update(fields)
        self._write(record)

    def _store(self, content: str, ext: str, enabled: bool) -> tuple[str, int, str | None]:
        """Return (sha256, byte_size, ref). Writes a sidecar only when enabled."""
        data = content.encode("utf-8")
        sha = hashlib.sha256(data).hexdigest()
        nbytes = len(data)
        ref: str | None = None
        if enabled:
            try:
                self.payload_dir.mkdir(parents=True, exist_ok=True)
                path = self.payload_dir / f"{sha}.{ext}"
                if not path.exists():
                    path.write_bytes(data)
                ref = f"payloads/{sha}.{ext}"
            except Exception:
                logger.debug("trace sidecar write failed", exc_info=True)
        return sha, nbytes, ref

    # -- public API ---------------------------------------------------------------

    def wrap(self, client: Any) -> Any:
        if client is None:
            return None
        if getattr(client, "_wiki_traced_by", None) is self:
            return client  # idempotent: already wrapped for this run (batch path)
        return TracingClient(self, client)

    def document_start(self, document_id: str, filename: str, relative_path: str) -> None:
        self._doc_token = _current_doc.set(
            {"document_id": document_id, "filename": filename, "relative_path": relative_path}
        )
        self.emit("document_start", document_id=document_id,
                  filename=filename, relative_path=relative_path)

    def document_end(self, status: str = "ok") -> None:
        self.emit("document_end", status=status)
        if self._doc_token is not None:
            _current_doc.reset(self._doc_token)
            self._doc_token = None

    @contextlib.contextmanager
    def stage(self, name: str, **meta: Any) -> Iterator[None]:
        token = _current_stage.set(name)
        t0 = perf_counter()
        self.emit("stage_start", **meta)
        status = "ok"
        try:
            yield
        except Exception:
            status = "error"
            raise
        finally:
            self.emit(
                "stage_end", status=status,
                elapsed_ms=round((perf_counter() - t0) * 1000, 1),
            )
            _current_stage.reset(token)

    def artifact(self, channel: str, name: str, content: str,
                 ext: str = "txt", **meta: Any) -> None:
        sha, nbytes, ref = self._store(content, ext, channel in self.channels)
        self.emit("artifact", channel=channel, name=name,
                  sha256=sha, bytes=nbytes, ref=ref, **meta)

    def record_llm_call(self, kwargs: dict, resp: Any, dt: float) -> None:
        messages = kwargs.get("messages")
        prompt_str = (
            json.dumps(messages, ensure_ascii=False, indent=2) if messages is not None else ""
        )
        psha, pbytes, pref = self._store(prompt_str, "json", "prompts" in self.channels)
        content = ""
        try:
            content = resp.choices[0].message.content or ""
        except Exception:
            content = ""
        rsha, rbytes, rref = self._store(content, "txt", "responses" in self.channels)
        params = {k: _jsonable(v) for k, v in kwargs.items() if k not in ("messages", "model")}
        self.emit(
            "llm_call",
            model=kwargs.get("model"),
            params=params,
            latency_ms=round(dt * 1000, 1),
            usage=_usage(resp),
            prompt_sha256=psha, prompt_bytes=pbytes, prompt_ref=pref,
            response_sha256=rsha, response_bytes=rbytes, response_ref=rref,
        )

    def finish(self) -> None:
        self.emit("run_end")
        try:
            self._fh.close()
        except Exception:
            pass
        _active.set(None)


# ── Transparent client proxy ────────────────────────────────────────────────────

class _TracingCompletions:
    def __init__(self, tracer: IngestionTracer, real: Any) -> None:
        self._tracer = tracer
        self._real = real

    def create(self, *args: Any, **kwargs: Any) -> Any:
        t0 = perf_counter()
        resp = self._real.create(*args, **kwargs)
        try:
            self._tracer.record_llm_call(kwargs, resp, perf_counter() - t0)
        except Exception:
            logger.debug("trace llm_call record failed", exc_info=True)
        return resp

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _TracingChat:
    def __init__(self, tracer: IngestionTracer, real_client: Any) -> None:
        self._real = real_client
        self.completions = _TracingCompletions(tracer, real_client.chat.completions)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real.chat, name)


class TracingClient:
    """Wraps an OpenAI-compatible client; observes chat.completions.create.

    Delegates every other attribute to the real client, so it is a drop-in
    replacement. The real response object is returned untouched.
    """

    def __init__(self, tracer: IngestionTracer, real: Any) -> None:
        object.__setattr__(self, "_wiki_traced_by", tracer)
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "chat", _TracingChat(tracer, real))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


# ── Run lifecycle ────────────────────────────────────────────────────────────────

def active_or_start(workspace: Path, db_path: str | None = None) -> tuple[Any, bool]:
    """Return (tracer, created).

    If a run is already active (e.g. a batch already opened one), reuse it and
    return created=False so the caller won't close it. Otherwise create a new
    tracer (or the NULL no-op tracer when disabled) and return created=True.
    """
    current = _active.get()
    if current is not None:
        return current, False
    if not _enabled():
        return NULL, False
    try:
        tracer = IngestionTracer(workspace, db_path)
    except Exception:
        logger.debug("trace init failed; continuing untraced", exc_info=True)
        return NULL, False
    _active.set(tracer)
    return tracer, True


@contextlib.contextmanager
def run_scope(workspace: Path, db_path: str | None = None) -> Iterator[Any]:
    """Context-manager form of active_or_start; closes only what it created."""
    tracer, created = active_or_start(workspace, db_path)
    try:
        yield tracer
    finally:
        if created:
            tracer.finish()
