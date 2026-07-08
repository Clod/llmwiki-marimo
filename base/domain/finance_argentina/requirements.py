"""Finance requirements manifest — model + parser.

PURPOSE FOR BEGINNERS:
The manifest (`requirements.md`) is the single source of truth for "what data
does each advisory category need". Its YAML front-matter is machine-readable
(read by the validator below and, in the future, a producer); its markdown
body is human documentation. This module only parses the front-matter into a
typed `FinanceRequirements` — see design_finance_module.md §2.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from domain.datasets.frontmatter import parse_frontmatter, split_frontmatter

logger = logging.getLogger("wiki.domain.finance_argentina.requirements")


@dataclass(frozen=True)
class CategoryRequirements:
    """What one advisory category's dataset must provide: rows + attributes."""

    categoria: str
    metricas: tuple[str, ...]
    attributes: tuple[str, ...]


@dataclass(frozen=True)
class FinanceRequirements:
    """The full manifest: one `CategoryRequirements` per advisory category."""

    categorias: dict[str, CategoryRequirements] = field(default_factory=dict)

    def category_names(self) -> tuple[str, ...]:
        return tuple(self.categorias.keys())


def parse_requirements_markdown(text: str) -> FinanceRequirements:
    """Parse a `requirements.md`-shaped string into a `FinanceRequirements`.

    Malformed entries (a category missing `metricas` or `attributes`) are
    skipped with a logged warning rather than raising — a single bad category
    should not block the rest of the manifest from loading.
    """
    frontmatter_block, _body = split_frontmatter(text)
    if frontmatter_block is None:
        logger.error("Finance requirements manifest has no front-matter — treating as empty")
        return FinanceRequirements()

    try:
        meta = parse_frontmatter(frontmatter_block)
    except ValueError as exc:
        logger.error("Finance requirements manifest: unparseable front-matter: %s", exc)
        return FinanceRequirements()

    raw_categorias = meta.get("categorias")
    if not isinstance(raw_categorias, dict):
        logger.error("Finance requirements manifest: missing or invalid 'categorias' mapping")
        return FinanceRequirements()

    categorias: dict[str, CategoryRequirements] = {}
    for slug, raw_spec in raw_categorias.items():
        parsed = _parse_category(str(slug), raw_spec)
        if parsed is not None:
            categorias[str(slug)] = parsed

    return FinanceRequirements(categorias=categorias)


def load_requirements_file(path: Path) -> FinanceRequirements:
    """Load + parse the manifest file at `path`. Missing file -> empty manifest."""
    if not path.is_file():
        logger.warning("Finance requirements manifest not found: %s", path)
        return FinanceRequirements()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("Could not read finance requirements manifest %s: %s", path, exc)
        return FinanceRequirements()
    return parse_requirements_markdown(text)


def default_manifest_path() -> Path:
    """Path to the manifest shipped with this module (`requirements.md`)."""
    return Path(__file__).parent / "requirements.md"


def load_default_requirements() -> FinanceRequirements:
    """Load the manifest shipped with the module (the v1 seed categories)."""
    return load_requirements_file(default_manifest_path())


def _parse_category(slug: str, raw_spec: object) -> CategoryRequirements | None:
    if not isinstance(raw_spec, dict):
        logger.warning("Finance requirements: category %r is not a mapping — skipped", slug)
        return None

    metricas = raw_spec.get("metricas")
    attributes = raw_spec.get("attributes")
    if not isinstance(metricas, list) or not isinstance(attributes, list):
        logger.warning(
            "Finance requirements: category %r missing metricas/attributes list — skipped", slug
        )
        return None

    return CategoryRequirements(
        categoria=slug,
        metricas=tuple(str(m) for m in metricas),
        attributes=tuple(str(a) for a in attributes),
    )
