"""What the Tools tab shows: the description of the tools and of the domains.

Read-only values built from the tool objects themselves (name, docstring, JSON
schema), so the tab and the model see the same text. The reasons that a tool is
inactive are codes plus a detail: the web layer words the codes in the interface
language (`web/routes/tools.py`); this module imports nothing from `web/`.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic_ai import Tool

from domain.chat.dataset_tools import query_dataset
from domain.chat.tools import search_source_chunks
from domain.chat.wiki_tools import read_wiki_page, search_wiki_fts
from domain.datasets.source import has_active_datasets

# Reason codes of an inactive tool.
NO_DATASETS = "no_datasets"                  # the wiki has no data files
REPLACED = "replaced"                        # the data tools of a domain replace the tool
NO_CATEGORY = "no_category"                  # no category of the domain passes
CATEGORY_EXCLUDED = "category_excluded"      # the category of the tool fails (detail: the reasons)

# Roles of a tool (`ToolRoles`).
EVIDENCE = "evidence"
VERBATIM = "verbatim"


@dataclass(frozen=True)
class ArgumentDescription:
    name: str
    type: str                                # e.g. "string", "integer | string"
    description: str
    required: bool
    allowed: tuple[str, ...] = ()            # the allowed values, when the tool restricts them


@dataclass(frozen=True)
class ToolDescription:
    name: str
    node: str                                # the trace node, K1 to K10
    description: str
    arguments: tuple[ArgumentDescription, ...]
    role: str                                # EVIDENCE, VERBATIM or "" (neither)
    active: bool
    reason: str = ""                         # a reason code, when inactive
    detail: str = ""                         # the reason text of the validator, when it has one


@dataclass(frozen=True)
class CalculationDescription:
    name: str
    source: str                              # the source code of the function


@dataclass(frozen=True)
class RequirementDescription:
    """What one data category must provide, and what its dataset in the wiki declares."""

    category: str
    metrics: tuple[str, ...]                 # the metrics its rows must have
    attributes: tuple[tuple[str, str], ...]  # (attribute, value the dataset declares; "" when absent)
    ok: bool                                 # True when the category meets its requirements


@dataclass(frozen=True)
class DomainDescription:
    name: str
    tools: tuple[ToolDescription, ...]
    calculations: tuple[CalculationDescription, ...] = ()
    requirements: tuple[RequirementDescription, ...] = ()
    requirements_file: str = ""              # the text of the file that declares the requirements
    passing: tuple[str, ...] = ()            # the categories that pass the requirements
    excluded: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (category, reasons)

    @property
    def active_count(self) -> int:
        return sum(1 for tool in self.tools if tool.active)


def calculation(function) -> CalculationDescription:
    """The source of a calculation function, read from the code."""
    return CalculationDescription(name=function.__name__, source=inspect.getsource(function))


def _type_of(prop: dict) -> str:
    branches = prop.get("anyOf") or [prop]
    names = [b.get("type", "any") for b in branches if b.get("type") != "null"]
    return " | ".join(names) or "any"


_SUMMARY = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)


def _summary(description: str | None) -> str:
    """The text of a tool the model reads, without the Args and Returns blocks."""
    text = description or ""
    match = _SUMMARY.search(text)
    return (match.group(1) if match else text).strip()


def describe_tool(
    function, node: str, role: str, active: bool, *, reason: str = "", detail: str = "",
    allowed: dict[str, list] | None = None,
) -> ToolDescription:
    """The description of one tool, from its pydantic-ai definition.

    `allowed` maps an argument to the values the tool restricts it to; the
    argument entries come from the JSON schema of the tool.
    """
    tool = function if isinstance(function, Tool) else Tool(function)
    schema = tool.tool_def.parameters_json_schema
    required = set(schema.get("required", []))
    arguments = tuple(
        ArgumentDescription(
            name=name, type=_type_of(prop), description=prop.get("description", ""),
            required=name in required, allowed=tuple(str(v) for v in (allowed or {}).get(name, ())),
        )
        for name, prop in schema.get("properties", {}).items()
    )
    return ToolDescription(
        name=tool.name, node=node, description=_summary(tool.tool_def.description), arguments=arguments,
        role=role, active=active, reason=reason, detail=detail,
    )


def role_of(name: str, evidence: frozenset[str], verbatim: frozenset[str]) -> str:
    return EVIDENCE if name in evidence else VERBATIM if name in verbatim else ""


def generic_tools(workspace: Path, replaced: bool) -> tuple[ToolDescription, ...]:
    """The generic tools of the chat (`domain/chat/*_tools.py`).

    `replaced` is True when the data tools of an active domain replace
    `query_dataset` (`DomainTools.replaces_dataset_tool`).
    """
    from domain.extensions import GENERIC_ROLES

    def role(name: str) -> str:
        return role_of(name, GENERIC_ROLES.evidence, GENERIC_ROLES.verbatim)

    if not has_active_datasets(workspace):
        dataset_state = dict(active=False, reason=NO_DATASETS)
    elif replaced:
        dataset_state = dict(active=False, reason=REPLACED)
    else:
        dataset_state = dict(active=True)
    return (
        describe_tool(read_wiki_page, "K1", role("read_wiki_page"), True),
        describe_tool(search_wiki_fts, "K2", role("search_wiki_fts"), True),
        describe_tool(search_source_chunks, "K3", role("search_source_chunks"), True),
        describe_tool(query_dataset, "K4", role("query_dataset"), **dataset_state),
    )
