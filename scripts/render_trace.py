#!/usr/bin/env python3
"""Render an ingestion trace.jsonl into a readable per-document timeline.

The trace (see base/domain/ingestion/trace.py) is machine-first JSONL. This
script turns it into something a human can skim during manual testing, and
optionally inlines sidecar payloads.

Usage:
    python scripts/render_trace.py <run_dir_or_trace.jsonl>
    python scripts/render_trace.py <path> --show prompts,responses   # inline payloads
    python scripts/render_trace.py <path> --doc <document_id>        # one document only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load(trace_path: Path) -> list[dict]:
    events = []
    with trace_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _read_sidecar(run_dir: Path, ref: str | None) -> str:
    if not ref:
        return "(payload channel off — not captured)"
    path = run_dir / ref
    if not path.exists():
        return f"(missing sidecar: {ref})"
    return path.read_text(encoding="utf-8")


def _fmt_usage(usage: dict | None) -> str:
    if not usage:
        return ""
    parts = [f"{k.split('_')[0]}={v}" for k, v in usage.items() if v is not None]
    return f" tokens[{', '.join(parts)}]" if parts else ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="run directory or trace.jsonl")
    ap.add_argument("--doc", help="only render this document_id")
    ap.add_argument("--show", default="", help="comma list: prompts,responses,extracted_text,chunks,markdown")
    args = ap.parse_args()

    target = Path(args.path)
    trace_path = target / "trace.jsonl" if target.is_dir() else target
    if not trace_path.exists():
        sys.exit(f"No trace.jsonl at {trace_path}")
    run_dir = trace_path.parent
    show = {s.strip() for s in args.show.split(",") if s.strip()}

    events = _load(trace_path)
    if not events:
        sys.exit("Empty trace.")

    meta = events[0]
    print("=" * 70)
    print(f"TRACE {meta.get('run_id')}  schema v{meta.get('schema_version')}")
    print(f"workspace: {meta.get('workspace')}")
    print(f"db_path:   {meta.get('db_path')}")
    print(f"captured channels: {meta.get('capture')}")
    print("=" * 70)

    for ev in events:
        etype = ev.get("event")
        doc = ev.get("document_id")
        if args.doc and doc and doc != args.doc:
            continue

        if etype == "document_start":
            print(f"\n┌─ DOCUMENT {ev.get('filename')}  ({doc})")
            print(f"│  relative_path: {ev.get('relative_path')}")
        elif etype == "document_end":
            print(f"└─ document end: status={ev.get('status')}\n")
        elif etype == "stage_start":
            print(f"│  ▸ stage: {ev.get('stage')}")
        elif etype == "stage_end":
            print(f"│    ↳ {ev.get('stage')} done in {ev.get('elapsed_ms')}ms (status={ev.get('status')})")
        elif etype == "llm_call":
            print(f"│    🤖 llm_call [{ev.get('stage')}] model={ev.get('model')} "
                  f"{ev.get('latency_ms')}ms{_fmt_usage(ev.get('usage'))}")
            print(f"│       prompt={ev.get('prompt_bytes')}B sha={ev.get('prompt_sha256', '')[:12]} | "
                  f"response={ev.get('response_bytes')}B sha={ev.get('response_sha256', '')[:12]}")
            if "prompts" in show:
                print(_indent(_read_sidecar(run_dir, ev.get("prompt_ref"))))
            if "responses" in show:
                print(_indent(_read_sidecar(run_dir, ev.get("response_ref"))))
        elif etype == "artifact":
            ch = ev.get("channel")
            extra = ""
            if ev.get("relative_path"):
                extra = f" → {ev.get('relative_path')}"
            print(f"│    📦 artifact [{ev.get('stage')}] {ch}:{ev.get('name')} "
                  f"{ev.get('bytes')}B sha={ev.get('sha256', '')[:12]}{extra}")
            if ch in show:
                print(_indent(_read_sidecar(run_dir, ev.get("ref"))))


def _indent(text: str, prefix: str = "│        ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


if __name__ == "__main__":
    main()
