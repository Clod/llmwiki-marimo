#!/usr/bin/env python3
"""Ask the questions of `docs/query_walkthrough.md` to the example wikis, and write
what each turn did to `docs/query_walkthrough_appendix.md`.

    uv run python scripts/capture_query_walkthrough.py            # write the appendix
    uv run python scripts/capture_query_walkthrough.py --out X.md

Each case is one conversation: a wiki of `examples/`, a chat mode, the check of the
pages (pre-retrieval mode only) and one or more questions. The script copies each
wiki to a temporary folder, so the examples do not change, and asks the questions
through the chat service of the web interface (`services.chat`), with the model of
`.env`. For each turn the appendix records the nodes of the turn in order
(`ChatResult.path`, the identifiers of `docs/query_nodes.md`), the tools the model
called, the tokens, and the answer as the user reads it.

The nodes up to the call to the model depend only on the code and the wiki: the same
question gives the same nodes on every run. The answer and the nodes after the call
depend on the model, and change from one run to the next.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "base"))

OUT = _PROJECT_ROOT / "docs" / "query_walkthrough_appendix.md"


@dataclass(frozen=True)
class Case:
    """One conversation of the appendix."""

    id: str
    wiki: str
    mode: str                       # "pre-retrieval", "agent" or "streaming"
    questions: tuple[str, ...]
    relevance: str = "none"         # "none", "evidence" or "judge"
    open_page: str = ""             # the page the user reads, e.g. "concepts/plazo-fijo-uva"


TALES = ("What tales are in this wiki?",
         "What characters and themes do the tales share?",
         "Compare how each story ends.")
CAUCION = "¿Qué es una caución bursátil y por qué se la considera de bajo riesgo?"

CASES: tuple[Case, ...] = (
    # Part 1: the model searches.
    *(Case(f"tales-streaming-{n}", "fairy-tales", "streaming", (q,)) for n, q in enumerate(TALES, 1)),
    *(Case(f"tales-agent-{n}", "fairy-tales", "agent", (q,)) for n, q in enumerate(TALES, 1)),
    Case("finance-agent-off-topic", "finanzas-argentinas", "agent", ("¿Cuál es la capital de Francia?",)),
    # Part 1 → Part 2: the same questions about the collection, with the code searching.
    *(Case(f"tales-pre-{n}", "fairy-tales", "pre-retrieval", (q,)) for n, q in enumerate(TALES, 1)),
    # Part 2: the code searches.
    Case("finance-page", "finanzas-argentinas", "pre-retrieval", (CAUCION,)),
    Case("finance-not-covered", "finanzas-argentinas", "pre-retrieval", ("¿Qué es un ETF?",)),
    Case("finance-off-topic", "finanzas-argentinas", "pre-retrieval", ("¿Cuál es la capital de Francia?",)),
    Case("finance-blacklist", "finanzas-argentinas", "pre-retrieval", ("¿Qué son los CEDEARs y conviene comprarlos?",)),
    Case("finance-datum", "finanzas-argentinas", "pre-retrieval", ("¿A cuánto está el dólar MEP?",)),
    Case("finance-alias", "finanzas-argentinas", "pre-retrieval", ("¿A cuánto está el billete verde?",)),
    Case("finance-advisory", "finanzas-argentinas", "pre-retrieval",
         ("Tengo $1.000.000 que no necesito por 3 meses, ¿qué alternativas tengo y cuánto ganaría?",)),
    Case("finance-limit", "finanzas-argentinas", "pre-retrieval", ("¿Cuánto ganaría con acciones de YPF?",)),
    Case("finance-follow-up", "finanzas-argentinas", "pre-retrieval",
         ("¿Qué es una caución bursátil?", "¿Y qué riesgos tiene?")),
    Case("finance-open-page", "finanzas-argentinas", "pre-retrieval", ("¿Cuál es el plazo mínimo?",),
         open_page="concepts/plazo-fijo-uva"),
    Case("tales-retry-sources", "cuentos-de-hadas", "pre-retrieval",
         ("¿En qué se convertían los lacayos de Cenicienta a la medianoche?",)),
    Case("finance-evidence", "finanzas-argentinas", "pre-retrieval", (CAUCION,), relevance="evidence"),
    Case("finance-judge", "finanzas-argentinas", "pre-retrieval", (CAUCION,), relevance="judge"),
)


@dataclass
class Message:
    """A conversation message in the shape the chat service reads."""

    role: str
    content: str
    context: tuple = ()


@dataclass
class Turn:
    question: str
    path: list[str]
    tools: list[str]
    end: str
    input_tokens: int
    output_tokens: int
    answer: str
    attributes: dict = field(default_factory=dict)


REFUSALS = ("R_BLACKLIST", "R_NOT_IN_ROSTER", "R_NOTHING_FOUND", "R_UNSUPPORTED", "R_NO_EVIDENCE", "R_MODEL")
# The nodes whose attributes the appendix prints: the decisions a reader checks.
SHOWN_ATTRIBUTES = ("Q_IN_ROSTER", "Q_SEARCH_WIKI", "Q_SEARCH_SOURCES", "Q_CONDENSED", "Q_IS_GROUNDED",
                    "Q_EVIDENCE_QUOTE", "Q_JUDGE_PAGES", "Q_RETRY_SOURCES", "C_FIGURES", "T_TOOL_EVIDENCE")


def _tools(path: list[dict]) -> list[str]:
    """The tool calls of the turn, in order: each tool call writes one K_ node."""
    return [entry["node"] for entry in path if entry["node"].startswith("K_")]


async def _ask(wiki, agents, history: list[Message], case: Case, conversation_id: str):
    from services import chat

    if case.mode == chat.STREAMING:
        result = None
        async for item in chat.chat_turn_stream_with_context(wiki, agents, history,
                                                             conversation_id=conversation_id,
                                                             open_page=case.open_page or None):
            if not isinstance(item, str):
                result = item
        return result
    return await chat.chat_turn_with_context(wiki, agents, history, mode=case.mode,
                                             conversation_id=conversation_id,
                                             open_page=case.open_page or None,
                                             relevance_check=case.relevance)


def _short(value) -> str:
    text = str(value)
    return text if len(text) <= 160 else text[:157] + "…"


async def run_case(case: Case, workdir: Path, settings) -> list[Turn]:
    from services.chat import build_agents
    from services.wiki import open_wiki

    copy = workdir / case.id
    shutil.copytree(_PROJECT_ROOT / "examples" / case.wiki, copy, ignore=shutil.ignore_patterns(".git"))
    wiki = open_wiki(copy)
    agents = build_agents(wiki, settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL)
    history: list[Message] = []
    turns: list[Turn] = []
    for question in case.questions:
        history.append(Message("user", question))
        result = await _ask(wiki, agents, history, case, f"walkthrough-{case.id}")
        history.append(Message("assistant", result.answer, tuple(result.context)))
        # The decision nodes of the graphs ("after_Q_IN_BLACKLIST", "tier") are not nodes of
        # docs/query_nodes.md: the path keeps the identifiers, which start with a capital.
        path = [entry["node"] for entry in result.path if entry["node"][:1].isupper()]
        refusals = [node for node in path if node in REFUSALS]
        attributes = {entry["node"]: {k: _short(v) for k, v in entry["attributes"].items()}
                      for entry in result.path if entry["node"] in SHOWN_ATTRIBUTES}
        turns.append(Turn(question, path, _tools(list(result.path)), (refusals or path or ["—"])[-1],
                          result.input_tokens, result.output_tokens, result.answer, attributes))
    return turns


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=_PROJECT_ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def render(results: list[tuple[Case, list[Turn]]], model: str) -> str:
    lines = [
        "# Query walkthrough — appendix",
        "",
        "<!-- Generated by scripts/capture_query_walkthrough.py. Do not edit: run the script again. -->",
        "",
        f"Captured on {dt.date.today().isoformat()}, commit `{_commit()}`, model `{model}`.",
        "",
        "Each section is one conversation. **Path** lists the nodes of each turn in order, with the",
        "identifiers of [`query_nodes.md`](query_nodes.md). **Tools** lists the tools the model called.",
        "The answer is quoted as the user reads it.",
        "",
    ]
    for case, turns in results:
        check = f", check of the pages `{case.relevance}`" if case.mode == "pre-retrieval" else ""
        page = f", open page `{case.open_page}`" if case.open_page else ""
        lines += [f"## {case.id}", "", f"Wiki `{case.wiki}`, mode `{case.mode}`{check}{page}.", ""]
        for number, turn in enumerate(turns, 1):
            lines += [
                f"### Turn {number}: {turn.question}",
                "",
                f"- **Path**: {' → '.join(turn.path)}",
                f"- **End**: `{turn.end}`",
                f"- **Tools**: {', '.join(f'`{t}`' for t in turn.tools) or 'none'}",
                f"- **Tokens**: input {turn.input_tokens}, output {turn.output_tokens}",
            ]
            for node, values in turn.attributes.items():
                shown = "; ".join(f"`{k}` = {v}" for k, v in values.items())
                lines.append(f"- **{node}**: {shown}")
            # A fenced block: the answer is quoted as text, so its links to wiki pages are not
            # links of this document.
            lines += ["", "Answer:", "", "```text", *turn.answer.splitlines(), "```", ""]
    return "\n".join(lines).rstrip() + "\n"


async def main_async(out: Path, only: set[str]) -> None:
    from dotenv import load_dotenv

    load_dotenv(_PROJECT_ROOT / ".env")
    from config import require_llm_config, settings

    require_llm_config(settings.LLM_BASE_URL, settings.LLM_API_KEY, settings.LLM_MODEL, purpose="chat")
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for case in CASES:
            if only and case.id not in only:
                continue
            print(f"… {case.id}", flush=True)
            results.append((case, await run_case(case, Path(tmp), settings)))
    out.write_text(render(results, settings.LLM_MODEL), encoding="utf-8")
    print(f"wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--out", type=Path, default=OUT, help="the appendix to write")
    parser.add_argument("--only", nargs="*", default=[], help="the ids of the cases to run (all by default)")
    args = parser.parse_args()
    asyncio.run(main_async(args.out, set(args.only)))


if __name__ == "__main__":
    main()
