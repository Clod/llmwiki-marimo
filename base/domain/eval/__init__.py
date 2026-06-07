"""Evaluation helpers: graders, rubric, packet assembly, and DB readers.

This package holds the *pure, network-free* pieces of the half-automated UAT:

- ``graders``  — cheap deterministic checks on a chat answer (regex/string).
- ``rubric``   — the frozen judge instructions, scoring rubric and probe set.
- ``packet``   — dataclasses + pure markdown assembly of the eval packet.
- ``reader``   — read-only DB queries that gather evidence from a wiki.

The live LLM calls (chat probes, re-ingestion) live in
``scripts/build_eval_packet.py``; everything here is deterministic so it can be
unit-tested in the fast gate.
"""
