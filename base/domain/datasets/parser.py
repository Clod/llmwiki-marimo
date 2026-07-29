"""Dataset markdown parser: file -> normalized DatasetRow list.

Implements the file contract in .trellis/spec/backend/datasets-format.md
exactly — §2 (signatures), §3 (front-matter contracts), §5 (Validation &
Error Matrix). Reject-file vs skip-row vs warn-and-continue are all logged
via the project's `wiki` logger (never silently swallowed) per
logging-guidelines.md.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from domain.datasets.frontmatter import parse_frontmatter, split_frontmatter
from domain.datasets.models import DatasetRow

logger = logging.getLogger("wiki.domain.datasets.parser")

_REQUIRED_KEYS = ("categoria", "formato", "as_of", "fuente")
_MATRIZ_KEYS = ("clave", "columnas_dim", "metrica", "unidad")
_LARGO_REQUIRED_KEYS = ("clave", "metricas")


def parse_dataset_markdown(text: str, *, filename_slug: str) -> list[DatasetRow]:
    """Parse one dataset markdown file's text into normalized rows.

    Args:
        text: Full file content (front-matter + one markdown table).
        filename_slug: The filename's slug (without extension) — authoritative
            for `categoria` (the join key to a concept page) per §5.

    Returns:
        A list of DatasetRow. Returns [] when the file is not a dataset
        (missing `type: dataset`) or is rejected for a validation error
        (logged at ERROR). Individual bad cells/rows are skipped with a
        WARNING, never aborting the rest of the file.
    """
    frontmatter_block, body = split_frontmatter(text)
    if frontmatter_block is None:
        return []

    try:
        meta = parse_frontmatter(frontmatter_block)
    except ValueError as exc:
        logger.error("Dataset file %s: unparseable front-matter: %s", filename_slug, exc)
        return []

    if meta.get("type") != "dataset":
        return []  # not a dataset file — concept pages never match (§5)

    missing = [k for k in _REQUIRED_KEYS if k not in meta]
    if missing:
        logger.error("Dataset file %s: missing required key(s) %s — file rejected", filename_slug, missing)
        return []

    formato = meta["formato"]
    if formato not in ("matriz", "largo"):
        logger.error("Dataset file %s: unknown formato %r — file rejected", filename_slug, formato)
        return []

    categoria = str(meta["categoria"])
    if categoria != filename_slug:
        logger.warning(
            "Dataset file %s: categoria %r != filename slug — filename is authoritative for the join",
            filename_slug,
            categoria,
        )

    as_of = _parse_date(meta["as_of"], filename_slug)
    if as_of is None:
        return []
    fuente = str(meta["fuente"])

    header, data_rows = _parse_markdown_table(body)
    if header is None:
        logger.error("Dataset file %s: no markdown table found — file rejected", filename_slug)
        return []

    if formato == "matriz":
        rows = _parse_matriz(meta, header, data_rows, filename_slug, as_of, fuente)
    else:
        rows = _parse_largo(meta, header, data_rows, filename_slug, as_of, fuente)

    return _dedupe_last_wins(rows, filename_slug)


# ── matriz ─────────────────────────────────────────────────────────────────────


def _parse_matriz(
    meta: dict[str, object],
    header: list[str],
    data_rows: list[list[str]],
    filename_slug: str,
    as_of: date,
    fuente: str,
) -> list[DatasetRow]:
    missing = [k for k in _MATRIZ_KEYS if k not in meta]
    if missing:
        logger.error("Dataset file %s: matriz missing key(s) %s — file rejected", filename_slug, missing)
        return []

    clave_col = str(meta["clave"])
    columnas_dim = str(meta["columnas_dim"])
    metrica = str(meta["metrica"])
    unidad = str(meta["unidad"])

    if clave_col not in header:
        logger.error(
            "Dataset file %s: clave column %r absent from table header — file rejected",
            filename_slug,
            clave_col,
        )
        return []

    clave_idx = header.index(clave_col)
    dim_columns = [h for i, h in enumerate(header) if i != clave_idx]

    rows: list[DatasetRow] = []
    for raw_row in data_rows:
        clave_value = raw_row[clave_idx]
        for col_name in dim_columns:
            col_idx = header.index(col_name)
            cell = raw_row[col_idx]
            valor = _parse_numeric_cell(cell, filename_slug, clave_value, col_name)
            if valor is None:
                continue
            rows.append(
                DatasetRow(
                    categoria=filename_slug,
                    clave=clave_value,
                    metrica=metrica,
                    valor=valor,
                    unidad=unidad,
                    dims={columnas_dim: col_name},
                    as_of=as_of,
                    fuente=fuente,
                )
            )
    return rows


# ── largo ──────────────────────────────────────────────────────────────────────


def _parse_largo(
    meta: dict[str, object],
    header: list[str],
    data_rows: list[list[str]],
    filename_slug: str,
    as_of: date,
    fuente: str,
) -> list[DatasetRow]:
    missing = [k for k in _LARGO_REQUIRED_KEYS if k not in meta]
    if missing:
        logger.error("Dataset file %s: largo missing key(s) %s — file rejected", filename_slug, missing)
        return []

    clave_col = str(meta["clave"])
    metricas = meta["metricas"]
    if not isinstance(metricas, dict) or not metricas:
        logger.error("Dataset file %s: largo 'metricas' must be a non-empty mapping — file rejected", filename_slug)
        return []

    dimensiones = meta.get("dimensiones", [])
    if not isinstance(dimensiones, list):
        dimensiones = [dimensiones]

    if clave_col not in header:
        logger.error(
            "Dataset file %s: clave column %r absent from table header — file rejected",
            filename_slug,
            clave_col,
        )
        return []
    for metric_col in metricas:
        if metric_col not in header:
            logger.error(
                "Dataset file %s: declared metric column %r absent from table header — file rejected",
                filename_slug,
                metric_col,
            )
            return []

    clave_idx = header.index(clave_col)
    has_as_of_col = "as_of" in header
    has_fuente_col = "fuente" in header

    rows: list[DatasetRow] = []
    for raw_row in data_rows:
        clave_value = raw_row[clave_idx]

        dims: dict[str, str] = {}
        for dim_col in dimensiones:
            if dim_col in header:
                dims[dim_col] = raw_row[header.index(dim_col)]

        row_as_of = as_of
        if has_as_of_col:
            parsed = _parse_date(raw_row[header.index("as_of")], filename_slug, warn_only=True)
            if parsed is not None:
                row_as_of = parsed
        row_fuente = raw_row[header.index("fuente")] if has_fuente_col else fuente

        for metric_col, unidad in metricas.items():
            cell = raw_row[header.index(metric_col)]
            valor = _parse_numeric_cell(cell, filename_slug, clave_value, metric_col)
            if valor is None:
                continue
            rows.append(
                DatasetRow(
                    categoria=filename_slug,
                    clave=clave_value,
                    metrica=metric_col,
                    valor=valor,
                    unidad=str(unidad),
                    dims=dims,
                    as_of=row_as_of,
                    fuente=str(row_fuente),
                )
            )
    return rows


# ── shared helpers ─────────────────────────────────────────────────────────────


def _parse_numeric_cell(cell: str, filename_slug: str, clave_value: str, col_name: str) -> float | None:
    cell = cell.strip()
    try:
        return float(cell)
    except ValueError:
        logger.warning(
            "Dataset file %s: non-numeric cell %r for clave=%r column=%r — row/cell skipped",
            filename_slug,
            cell,
            clave_value,
            col_name,
        )
        return None


def _parse_date(raw: object, filename_slug: str, *, warn_only: bool = False) -> date | None:
    raw_str = str(raw).strip()
    try:
        return datetime.strptime(raw_str, "%Y-%m-%d").date()
    except ValueError:
        level = logger.warning if warn_only else logger.error
        level("Dataset file %s: invalid date %r (expected YYYY-MM-DD)", filename_slug, raw_str)
        return None


def _dedupe_last_wins(rows: list[DatasetRow], filename_slug: str) -> list[DatasetRow]:
    """Duplicate (clave, dims, metrica) within a file -> last wins, log warning (§5)."""
    seen: dict[tuple[str, tuple[tuple[str, str], ...], str], int] = {}
    deduped: list[DatasetRow] = []
    for row in rows:
        key = (row.clave, tuple(sorted(row.dims.items())), row.metrica)
        if key in seen:
            logger.warning(
                "Dataset file %s: duplicate (clave=%r, dims=%r, metrica=%r) — last wins",
                filename_slug,
                row.clave,
                row.dims,
                row.metrica,
            )
            deduped[seen[key]] = row
        else:
            seen[key] = len(deduped)
            deduped.append(row)
    return deduped


# ── markdown table parsing ──────────────────────────────────────────────────────


def _parse_markdown_table(body: str) -> tuple[list[str] | None, list[list[str]]]:
    """Parse the single markdown pipe-table in `body`.

    Returns (header_cells, data_rows) or (None, []) if no table is found.
    The separator row (e.g. |---|---|) is detected and skipped.
    """
    table_lines = [line for line in body.splitlines() if line.strip().startswith("|")]
    if len(table_lines) < 2:
        return None, []

    header = _split_table_row(table_lines[0])
    data_rows = [
        _split_table_row(line) for line in table_lines[1:] if not _is_separator_row(line)
    ]
    return header, data_rows


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _is_separator_row(line: str) -> bool:
    stripped = line.strip().strip("|")
    cells = [c.strip() for c in stripped.split("|")]
    return all(c and set(c) <= set("-: ") for c in cells)
