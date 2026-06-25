"""LocalMarkdownSource — parse-on-read DatasetSource backed by datasets/*.md.

PURPOSE FOR BEGINNERS:
This is the only DatasetSource implementation today. It re-parses the
workspace's `datasets/` directory on every query() call (no caching, no
SQLite) — files are small and infrequently read, so the simplicity is worth
the repeat parsing. A future `RemoteServiceSource` would implement the same
Protocol against a remote API instead.
"""

from __future__ import annotations

import logging
from pathlib import Path

from domain.datasets.models import DatasetRow
from domain.datasets.parser import parse_dataset_markdown

logger = logging.getLogger("wiki.domain.datasets.source")


class LocalMarkdownSource:
    """Reads normalized dataset rows from `datasets_dir/<categoria>.md` files.

    Implements the `DatasetSource` Protocol (domain.datasets.models). Current
    only — no date parameter, no history; each query reflects the latest
    on-disk content (replace-latest semantics).
    """

    def __init__(self, datasets_dir: Path) -> None:
        self._datasets_dir = Path(datasets_dir)

    def categories(self) -> list[str]:
        """Return filename slugs of files that parse as valid datasets."""
        if not self._datasets_dir.is_dir():
            return []
        slugs = []
        for path in sorted(self._datasets_dir.glob("*.md")):
            if self._rows_for_file(path):
                slugs.append(path.stem)
        return slugs

    def query(
        self,
        categoria: str,
        *,
        clave: str | None = None,
        metrica: str | None = None,
        dims: dict[str, str] | None = None,
    ) -> list[DatasetRow]:
        """Return rows for `categoria`, optionally filtered. Unknown -> []."""
        path = self._datasets_dir / f"{categoria}.md"
        rows = self._rows_for_file(path)
        if clave is not None:
            rows = [r for r in rows if r.clave == clave]
        if metrica is not None:
            rows = [r for r in rows if r.metrica == metrica]
        if dims is not None:
            rows = [r for r in rows if r.dims == dims]
        return rows

    def _rows_for_file(self, path: Path) -> list[DatasetRow]:
        if not path.is_file():
            return []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read dataset file %s: %s", path, exc)
            return []
        return parse_dataset_markdown(text, filename_slug=path.stem)


def has_active_datasets(workspace: Path) -> bool:
    """True when `workspace/datasets/` exists and contains >=1 valid dataset file.

    This is the activation trigger (§4): presence of a datasets/ directory
    alone is not enough — at least one file must actually parse as a valid
    dataset, otherwise the capability stays dormant.
    """
    datasets_dir = Path(workspace) / "datasets"
    if not datasets_dir.is_dir():
        return False
    return bool(LocalMarkdownSource(datasets_dir).categories())
