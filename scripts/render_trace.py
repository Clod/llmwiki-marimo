#!/usr/bin/env python3
"""Render a span file (spans.jsonl) into a readable per-trace timeline.

The span file (see base/domain/tracing.py) is machine-first JSONL, one
OpenTelemetry span per line. This script turns it into something a human can
skim during manual testing: one timeline per trace, spans indented by depth
under their parent, and optionally inlines sidecar payload files.

Usage:
    python scripts/render_trace.py <spans.jsonl_or_workspace_dir>
    python scripts/render_trace.py <path> --trace <trace_id>
    python scripts/render_trace.py <path> --conversation <conversation_id>
    python scripts/render_trace.py <path> --doc <document_id>
    python scripts/render_trace.py <path> --show prompts,responses   # inline payloads
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

SPAN_FILE = "spans.jsonl"
_OMIT = {"llmwiki.workspace", "llmwiki.node", "llmwiki.diagram"}


def _locate(path: Path) -> Path:
    """A spans.jsonl file, or the workspace/traces directory holding one."""
    if path.is_file():
        return path
    for candidate in (path / ".llmwiki" / "traces" / SPAN_FILE, path / SPAN_FILE):
        if candidate.exists():
            return candidate
    sys.exit(f"No {SPAN_FILE} under {path}")


def _load(span_path: Path) -> list[dict]:
    spans = []
    with span_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                spans.append(json.loads(line))
    return spans


def _duration_ms(span: dict) -> float:
    start = datetime.fromisoformat(span["start_time"])
    end = datetime.fromisoformat(span["end_time"])
    return round((end - start).total_seconds() * 1000, 1)


def _attrs(span: dict) -> dict:
    return {
        k: v for k, v in (span.get("attributes") or {}).items()
        if k not in _OMIT and (k.startswith("llmwiki.") or k.startswith("gen_ai."))
        # A payload is shown by its size; its hash and file path only lengthen the line.
        and not k.endswith((".sha256", ".ref"))
    }


def _matches_show(name: str, show: set[str]) -> bool:
    return name in show or f"{name}s" in show or name.rstrip("s") in show


def _payload_lines(span: dict, base_dir: Path, show: set[str], indent: str) -> list[str]:
    lines = []
    for key, ref in (span.get("attributes") or {}).items():
        if not (key.startswith("llmwiki.") and key.endswith(".ref")):
            continue
        name = key[len("llmwiki."):-len(".ref")]
        if not _matches_show(name, show):
            continue
        payload_path = base_dir / str(ref)
        content = (payload_path.read_text(encoding="utf-8") if payload_path.exists()
                   else f"(missing payload: {ref})")
        lines.append(f"{indent}--- {name} ---")
        lines.extend(f"{indent}{line}" for line in content.splitlines())
    return lines


def _tree(spans: list[dict]) -> dict:
    """{(trace_id, parent_span_id_or_None): [child spans, sorted by start_time]}."""
    children: dict = {}
    for span in spans:
        ctx = span["context"]
        parent = span.get("parent_id")
        key = (ctx["trace_id"], parent) if parent else None
        children.setdefault(key, []).append(span)
    for group in children.values():
        group.sort(key=lambda s: s["start_time"])
    return children


def _render(span: dict, children: dict, base_dir: Path, show: set[str],
            out: list[str], depth: int = 0) -> None:
    indent = "  " * depth
    line = f"{indent}{span['name']} ({_duration_ms(span)}ms)"
    attrs = _attrs(span)
    if attrs:
        line += " " + " ".join(f"{k}={v}" for k, v in attrs.items())
    status = span.get("status") or {}
    if status.get("status_code") not in (None, "UNSET"):
        line += f" [{status['status_code']}: {status.get('description')}]"
    out.append(line)
    for event in span.get("events") or []:
        exc_type = (event.get("attributes") or {}).get("exception.type")
        if exc_type:
            exc_msg = event["attributes"].get("exception.message")
            out.append(f"{indent}  ! {exc_type}: {exc_msg}")
    out.extend(_payload_lines(span, base_dir, show, indent + "  "))
    key = (span["context"]["trace_id"], span["context"]["span_id"])
    for child in children.get(key, []):
        _render(child, children, base_dir, show, out, depth + 1)


def _norm(value: str) -> str:
    value = value.lower()
    return value[2:] if value.startswith("0x") else value


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="spans.jsonl, or the workspace directory that holds one")
    ap.add_argument("--trace", help="only this trace_id")
    ap.add_argument("--conversation", help="every turn of this conversation_id, in turn order")
    ap.add_argument("--doc", help="only the spans under the document span of this document_id")
    ap.add_argument("--show", default="", help="comma list: prompts,responses,extracted_text,chunks,markdown")
    args = ap.parse_args()

    span_path = _locate(Path(args.path))
    base_dir = span_path.parent
    spans = _load(span_path)
    if not spans:
        sys.exit("Empty span file.")
    show = {s.strip() for s in args.show.split(",") if s.strip()}
    children = _tree(spans)

    if args.doc:
        targets = [s for s in spans if s["name"] == "document"
                   and (s.get("attributes") or {}).get("llmwiki.document_id") == args.doc]
        if not targets:
            sys.exit(f"No document span for document_id={args.doc}")
        for span in targets:
            out: list[str] = []
            _render(span, children, base_dir, show, out)
            print("\n".join(out))
        return

    roots = children.get(None, [])
    if args.trace:
        wanted = _norm(args.trace)
        roots = [r for r in roots if _norm(r["context"]["trace_id"]) == wanted]
        if not roots:
            sys.exit(f"No trace {args.trace}")
    if args.conversation:
        roots = [r for r in roots
                 if (r.get("attributes") or {}).get("llmwiki.conversation_id") == args.conversation]
        if not roots:
            sys.exit(f"No conversation {args.conversation}")
        roots.sort(key=lambda r: (r.get("attributes") or {}).get("llmwiki.turn", 0))

    for root in roots:
        print("=" * 70)
        print(f"TRACE {root['context']['trace_id']}  root: {root['name']}")
        print("=" * 70)
        out = []
        _render(root, children, base_dir, show, out)
        print("\n".join(out))
        print()


if __name__ == "__main__":
    main()
