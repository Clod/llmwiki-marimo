"""Finance extension for Argentina: advisory table and current-value data tools.

Domain interface (`domain/extensions/__init__.py`): `NAME`, `ROLES`, `activate`.
"""

import re
from pathlib import Path

from domain.datasets.source import LocalMarkdownSource
from domain.extensions import DomainTools, ToolRoles
from domain.extensions.description import (
    CATEGORY_EXCLUDED,
    NO_CATEGORY,
    DomainDescription,
    RequirementDescription,
    ToolDescription,
    calculation,
    describe_tool,
    role_of,
)

from .advisory import AdvisoryResult, RankedOption, VariableOption, estimate_alternatives, render_markdown
from .agent_tool import activate as _activate_tools
from .agent_tool import estimar_alternativas
from .data_tools import DATA_TOOL_NAMES, TOOL_SPECS, allowed_values, make_data_tools
from .formulae import projected_gain, tea
from .instrument_attrs import InstrumentAttributes, attributes_from_meta
from .requirements import (
    default_manifest_path,
    load_default_requirements,
    CategoryRequirements,
    FinanceRequirements,
    load_requirements_file,
    parse_requirements_markdown,
)
from .validator import CategoryValidation, ValidationReport, validate_workspace

NAME = "finance_argentina"

# The advisory table is appended to the answer verbatim (answer_with_table). The
# data tools return the `format_rows_as_table` table, so their returns are
# citable evidence (ensure_citation).
ROLES = ToolRoles(evidence=DATA_TOOL_NAMES, verbatim=frozenset({"estimar_alternativas"}))


# The shapes of an advisory question, per language: a money amount or a time horizon.
# `scope.advisory_intent` needs one of them together with an expression of the
# advisory list (`intent.es.toml`), and matches them on the normalized question
# (lowercase, no accents).
ADVISORY_SHAPES: dict[str, tuple[re.Pattern, ...]] = {
    "es": (
        re.compile(r"\$\s*\d|\b\d[\d.,]*\s*(?:mil|millon|millones|palos?|lucas?|pesos?|dolares?|usd)\b"),
        re.compile(r"\b\d+\s*(?:dia|dias|mes|meses|ano|anos|semana|semanas|trimestre|trimestres)\b"),
    ),
}


# The trace node of each tool (docs/query_nodes.md, the K nodes).
NODES = {
    "estimar_alternativas": "K5", "tasa_plazo_fijo": "K6", "tasa_caucion": "K7",
    "cotizacion_dolar": "K8", "precio_accion": "K9", "rendimiento_fci": "K10",
}


def activate(workspace: Path) -> DomainTools | None:
    """The finance tools and prompt when the data of `workspace` satisfies the
    finance manifest; None otherwise."""
    tools, prompt = _activate_tools(workspace)
    if not tools:
        return None
    return DomainTools(
        tools=tools, prompt=prompt,
        replaces_dataset_tool=any(getattr(t, "name", None) in DATA_TOOL_NAMES for t in tools),
    )


def _declared(value: object) -> str:
    """A front-matter value as the Tools tab shows it ("" when absent)."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def describe(workspace: Path) -> DomainDescription:
    """The tools of the domain, active or not in `workspace`, with the reason of
    each inactive one, the calculation functions of the advisory, and what each
    category requires (`requirements.md`) next to what its dataset declares."""
    source = LocalMarkdownSource(workspace / "datasets")
    requirements = load_default_requirements()
    report = validate_workspace(requirements, source)
    passing = set(report.passing)
    tools: list[ToolDescription] = [describe_tool(
        estimar_alternativas, NODES["estimar_alternativas"],
        role_of("estimar_alternativas", ROLES.evidence, ROLES.verbatim),
        active=bool(passing), reason="" if passing else NO_CATEGORY,
    )]
    categories = tuple(categoria for categoria, _, _ in TOOL_SPECS.values())
    for tool in make_data_tools(source, categories):
        categoria, key_arg, with_plazo = TOOL_SPECS[tool.name]
        ok = categoria in passing
        tools.append(describe_tool(
            tool, NODES[tool.name], role_of(tool.name, ROLES.evidence, ROLES.verbatim), active=ok,
            reason="" if ok else CATEGORY_EXCLUDED, detail="" if ok else report.reason_for(categoria),
            allowed=allowed_values(source, categoria, key_arg, with_plazo) if ok else None,
        ))
    required = tuple(
        RequirementDescription(
            category=categoria, metrics=spec.metricas,
            attributes=tuple((name, _declared(source.attributes(categoria).get(name))) for name in spec.attributes),
            ok=categoria in passing,
        )
        for categoria, spec in requirements.categorias.items()
    )
    return DomainDescription(
        name=NAME, tools=tuple(tools), calculations=(calculation(tea), calculation(projected_gain)),
        requirements=required, requirements_file=default_manifest_path().read_text(encoding="utf-8"),
        passing=report.passing, excluded=tuple((r.categoria, "; ".join(r.reasons)) for r in report.failing),
    )


__all__ = [
    "NAME",
    "describe",
    "ROLES",
    "activate",
    "AdvisoryResult",
    "RankedOption",
    "VariableOption",
    "estimate_alternatives",
    "render_markdown",
    "InstrumentAttributes",
    "attributes_from_meta",
    "projected_gain",
    "tea",
    "CategoryRequirements",
    "FinanceRequirements",
    "load_requirements_file",
    "parse_requirements_markdown",
    "CategoryValidation",
    "ValidationReport",
    "validate_workspace",
]
