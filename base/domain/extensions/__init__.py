"""Domain tools: one subpackage per domain, behind one interface.

A domain subpackage (`domain/extensions/<domain>/`) defines in its `__init__.py`:

  - `NAME`: the domain name;
  - `ROLES`: a `ToolRoles` value, static by tool name;
  - `activate(workspace)`: a `DomainTools` value, or None when the data of the
    workspace does not satisfy the domain;
  - `describe(workspace)`: a `DomainDescription` value for the Tools tab
    (`domain/extensions/description.py`), for an active and for an inactive domain.

The registry (`domains`, `activate_all`, `roles`) discovers the subpackages, so
the chat code (`services/chat.py`, `domain/chat/postprocess.py`) imports no
domain by name. Adding a domain is adding a subpackage.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import ModuleType


@dataclass(frozen=True)
class DomainTools:
    """What an active domain adds to a chat agent."""

    tools: list                    # pydantic-ai Tool objects or functions
    prompt: str | None             # appended to the system prompt
    replaces_dataset_tool: bool    # True: create_agent does not register query_dataset


@dataclass(frozen=True)
class ToolRoles:
    """What the post-processing does with the return of a tool, by tool name."""

    evidence: frozenset[str] = field(default_factory=frozenset)  # citable sources (ensure_citation)
    verbatim: frozenset[str] = field(default_factory=frozenset)  # appended to the answer (answer_with_table)


# The generic tools (`domain/chat/*_tools.py`) that return citable evidence.
GENERIC_ROLES = ToolRoles(evidence=frozenset({"read_wiki_page", "query_dataset"}))


@lru_cache(maxsize=1)
def domains() -> dict[str, ModuleType]:
    """The domain subpackages by name, each imported once.

    Raises ValueError when two domains declare the same tool name in their roles.
    """
    found: dict[str, ModuleType] = {}
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if not info.ispkg:
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        found[module.NAME] = module
    _check_unique_tool_names(found)
    return found


def _check_unique_tool_names(found: dict[str, ModuleType]) -> None:
    owner: dict[str, str] = {name: "generic" for name in GENERIC_ROLES.evidence | GENERIC_ROLES.verbatim}
    for name, module in found.items():
        for tool in module.ROLES.evidence | module.ROLES.verbatim:
            if tool in owner and owner[tool] != name:
                raise ValueError(f"tool name {tool!r} is declared by {owner[tool]!r} and by {name!r}")
            owner[tool] = name


def roles() -> ToolRoles:
    """The union of the generic roles and the roles of every domain."""
    evidence, verbatim = set(GENERIC_ROLES.evidence), set(GENERIC_ROLES.verbatim)
    for module in domains().values():
        evidence |= module.ROLES.evidence
        verbatim |= module.ROLES.verbatim
    return ToolRoles(evidence=frozenset(evidence), verbatim=frozenset(verbatim))


def activate_all(workspace: Path) -> DomainTools | None:
    """Merge the active domains of `workspace`: tools concatenated, prompts
    joined, `replaces_dataset_tool` true when any domain sets it. None when no
    domain is active."""
    active = [d for d in (m.activate(workspace) for m in domains().values()) if d is not None]
    if not active:
        return None
    prompts = [d.prompt for d in active if d.prompt]
    return DomainTools(
        tools=[tool for d in active for tool in d.tools],
        prompt="".join(prompts) if prompts else None,
        replaces_dataset_tool=any(d.replaces_dataset_tool for d in active),
    )


def describe_all(workspace: Path) -> tuple[tuple, list]:
    """`(generic tools, domain descriptions)` of `workspace`, for the Tools tab."""
    from domain.extensions.description import generic_tools

    active = activate_all(workspace)
    generic = generic_tools(workspace, replaced=bool(active and active.replaces_dataset_tool))
    return generic, [module.describe(workspace) for module in domains().values()]
