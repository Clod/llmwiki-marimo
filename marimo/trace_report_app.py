# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "marimo",
# ]
# ///
"""
Ingestion Trace Report
----------------------
Pick a directory, discover the ingestion ``trace.jsonl`` runs underneath it, and
inspect them two ways:

  * a human-readable per-document timeline (the same layout as
    ``scripts/render_trace.py``), and
  * an ``mo.tree`` of the raw JSON events grouped by document, for drilling in.

The trace format is produced by ``base/domain/ingestion/trace.py`` when an
ingestion run is executed with ``WIKI_TRACE=1``. A run directory contains
``trace.jsonl`` plus a ``payloads/`` sidecar folder; payload channels
(prompts, responses, extracted_text, chunks, markdown) can be inlined on demand.
"""

import marimo

__generated_with = "0.23.8"
app = marimo.App(width="full")


@app.cell
def imports():
    import html
    import json
    from pathlib import Path

    import marimo as mo

    return Path, html, json, mo


@app.cell
def intro(mo):
    mo.md("""
    # 🔎 Ingestion Trace Report

    Upload a `trace.jsonl` file below. Optionally set a **base directory** to
    inline payload sidecars (prompts, responses, extracted text, chunks, markdown).

    Trace files are produced by running the ingestion pipeline with `WIKI_TRACE=1`.
    """)
    return


@app.cell
def helpers(Path, html, json):
    """Pure functions: discovery, loading, rendering, tree shaping."""

    def discover_traces(base_dir: Path) -> list[Path]:
        """Return every trace.jsonl at or under ``base_dir`` (newest first)."""
        if not base_dir.exists():
            return []
        direct = base_dir / "trace.jsonl"
        if direct.exists():
            return [direct]
        found = sorted(base_dir.rglob("trace.jsonl"))
        # newest run first — run dirs are timestamp-prefixed
        return sorted(found, key=lambda p: p.parent.name, reverse=True)

    def load_events(trace_path: Path) -> list[dict]:
        events: list[dict] = []
        with trace_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def read_sidecar(run_dir: Path, ref: str | None) -> str:
        if not ref:
            return "(payload channel off — not captured)"
        path = run_dir / ref
        if not path.exists():
            return f"(missing sidecar: {ref})"
        return path.read_text(encoding="utf-8")

    def fmt_usage(usage: dict | None) -> str:
        if not usage:
            return ""
        parts = [f"{k.split('_')[0]}={v}" for k, v in usage.items() if v is not None]
        return f" tokens[{', '.join(parts)}]" if parts else ""

    def indent(text: str, prefix: str = "│        ") -> str:
        return "\n".join(prefix + line for line in text.splitlines())

    def render_report(events: list[dict], run_dir: Path, show: set[str]) -> str:
        """Mirror scripts/render_trace.py, returning the timeline as a string."""
        if not events:
            return "(empty trace)"
        out: list[str] = []
        meta = events[0]
        out.append("=" * 70)
        out.append(f"TRACE {meta.get('run_id')}  schema v{meta.get('schema_version')}")
        out.append(f"workspace: {meta.get('workspace')}")
        out.append(f"db_path:   {meta.get('db_path')}")
        out.append(f"captured channels: {meta.get('capture')}")
        out.append("=" * 70)

        for ev in events:
            etype = ev.get("event")
            if etype == "document_start":
                out.append(f"\n┌─ DOCUMENT {ev.get('filename')}  ({ev.get('document_id')})")
                out.append(f"│  relative_path: {ev.get('relative_path')}")
            elif etype == "document_end":
                out.append(f"└─ document end: status={ev.get('status')}\n")
            elif etype == "stage_start":
                out.append(f"│  ▸ stage: {ev.get('stage')}")
            elif etype == "stage_end":
                out.append(
                    f"│    ↳ {ev.get('stage')} done in {ev.get('elapsed_ms')}ms "
                    f"(status={ev.get('status')})"
                )
            elif etype == "llm_call":
                out.append(
                    f"│    🤖 llm_call [{ev.get('stage')}] model={ev.get('model')} "
                    f"{ev.get('latency_ms')}ms{fmt_usage(ev.get('usage'))}"
                )
                out.append(
                    f"│       prompt={ev.get('prompt_bytes')}B "
                    f"sha={ev.get('prompt_sha256', '')[:12]} | "
                    f"response={ev.get('response_bytes')}B "
                    f"sha={ev.get('response_sha256', '')[:12]}"
                )
                if "prompts" in show:
                    out.append(indent(read_sidecar(run_dir, ev.get("prompt_ref"))))
                if "responses" in show:
                    out.append(indent(read_sidecar(run_dir, ev.get("response_ref"))))
            elif etype == "artifact":
                ch = ev.get("channel")
                extra = f" → {ev.get('relative_path')}" if ev.get("relative_path") else ""
                out.append(
                    f"│    📦 artifact [{ev.get('stage')}] {ch}:{ev.get('name')} "
                    f"{ev.get('bytes')}B sha={ev.get('sha256', '')[:12]}{extra}"
                )
                if ch in show:
                    out.append(indent(read_sidecar(run_dir, ev.get("ref"))))
        return "\n".join(out)

    def group_by_document(events: list[dict]) -> dict:
        """Shape events into {meta, runtime, documents:{filename:[events]}} for mo.tree."""
        meta = events[0] if events else {}
        tree: dict = {
            "meta": meta,
            "runtime": [e for e in events if e.get("event") in ("run_start", "run_end")],
            "documents": {},
        }
        current = None
        for ev in events:
            etype = ev.get("event")
            if etype == "document_start":
                current = f"{ev.get('filename')}  ({ev.get('document_id')})"
                tree["documents"][current] = [ev]
            elif etype == "document_end" and current is not None:
                tree["documents"][current].append(ev)
                current = None
            elif current is not None and etype not in ("meta", "run_start", "run_end"):
                tree["documents"][current].append(ev)
        return tree

    def to_html_pre(text: str) -> str:
        escaped = html.escape(text)
        return (
            "<pre style='white-space:pre-wrap; font-size:12px; line-height:1.4; "
            "max-height:70vh; overflow:auto; background:#0d1117; color:#c9d1d9; "
            "padding:1rem; border-radius:8px;'>" + escaped + "</pre>"
        )

    return (
        group_by_document,
        render_report,
        to_html_pre,
    )


@app.cell
def file_selector(mo):
    """Upload the trace.jsonl file directly."""
    json_selector = mo.ui.file(
        filetypes=[".jsonl"],
        multiple=False,
        label="Upload trace.jsonl",
    )
    json_selector
    return (json_selector,)


@app.cell
def run_dir_input(mo):
    """Base directory for sidecar lookup — optional, read from WIKI_TRACE_DIR env or typed in."""
    import os
    _default = os.environ.get("WIKI_TRACE_DIR", "")
    base_dir_input = mo.ui.text(
        value=_default,
        label="Trace runs base directory (for sidecar lookup)",
        placeholder="/path/to/trace_runs",
        full_width=True,
    )
    base_dir_input
    return (base_dir_input,)


@app.cell
def payload_selector(mo):
    """Which payload channels to inline. Independent control → its own cell."""
    show_channels = mo.ui.multiselect(
        options=["prompts", "responses", "extracted_text", "chunks", "markdown"],
        value=[],
        label="Inline payload channels",
    )
    show_channels
    return (show_channels,)


@app.cell
def load_selected(Path, base_dir_input, json, json_selector):
    events = []
    run_dir = None
    contents = json_selector.contents(0)
    if contents:
        text = contents.decode("utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))

        if events and base_dir_input.value.strip():
            meta = events[0]
            base_dir = Path(base_dir_input.value.strip())
            if "run_id" in meta and base_dir.exists():
                run_dir = base_dir / meta["run_id"]
    return events, run_dir


@app.cell
def report_view(
    events,
    mo,
    render_report,
    run_dir,
    show_channels,
    to_html_pre,
):
    _channels = set(show_channels.value)
    _sidecar_channels = _channels & {"prompts", "responses", "extracted_text", "chunks", "markdown"}
    _warning = (
        mo.callout(
            mo.md("Set the **Trace runs base directory** above to inline payload channels."),
            kind="warn",
        )
        if _sidecar_channels and run_dir is None
        else mo.Html("")
    )
    report_text = (
        render_report(events, run_dir, _channels)
        if events
        else "Upload a trace.jsonl to see the report."
    )
    mo.vstack([_warning, mo.md("## 📜 Timeline report"), mo.Html(to_html_pre(report_text))])
    return


@app.cell
def tree_view(events, group_by_document, mo):
    tree_data = group_by_document(events) if events else {"(no trace loaded)": None}
    mo.vstack([mo.md("## 🌳 JSON tree"), mo.tree(tree_data)])
    return


if __name__ == "__main__":
    app.run()
