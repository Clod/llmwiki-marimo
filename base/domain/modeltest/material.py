"""The material of the model tests, written in the code: no test reads a file of `tests/`.

Each content language has two concept pages, two questions, a source text for the
ingestion and the name of the pattern PDF of the vision test. The content is invented,
so no model can answer from memory: the code `ZK-4471` exists only in these pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# The pattern PDF of each language and its data (`<pattern>.pdf`, `<pattern>.json`),
# built by `scripts/build_model_test_pattern.py`.
ASSETS = Path(__file__).parent / "assets"


@dataclass(frozen=True)
class Page:
    """One concept page of the temporary wiki."""

    title: str
    slug: str
    content: str


@dataclass(frozen=True)
class Material:
    """The material of the tests in one language.

    pages:        the two concept pages. The first one holds `fact`.
    fact:         the datum the reading model must answer with.
    answerable:   a question the first page answers.
    unanswerable: a question about the same subject that no page answers.
    source_name:  the file name of the source the ingestion test ingests.
    source_text:  the text of that source.
    pattern:      the name of the pattern PDF and its data, without extension.
    """

    pages: tuple[Page, Page]
    fact: str
    answerable: str
    unanswerable: str
    source_name: str
    source_text: str
    pattern: str


_ES = Material(
    pages=(
        Page("Depósito Aldebarán", "deposito-aldebaran", """# Depósito Aldebarán

El depósito Aldebarán es un depósito de mercadería ubicado en la localidad de Tres Lomas.
El código interno del depósito Aldebarán es ZK-4471. Su encargada es Marisol Quintanilla
Erbes. El depósito tiene tres sectores: Norte, Sur y Anexo. El depósito recibe hasta
3.280 cajas por lote.
"""),
        Page("Lote de mercadería", "lote-de-mercaderia", """# Lote de mercadería

Un lote de mercadería es un conjunto de cajas que ingresa al depósito en una misma fecha.
Cada lote lleva un acta de recepción, que registra las cajas en buen estado y las cajas
con daños. Ver también [Depósito Aldebarán](deposito-aldebaran.md).
"""),
    ),
    fact="ZK-4471",
    answerable="¿Cuál es el código interno del depósito Aldebarán?",
    unanswerable="¿De qué color es el depósito Aldebarán?",
    source_name="control-de-temperatura.md",
    source_text="""# Control de temperatura en la cámara Berilo

La cámara Berilo es la cámara de frío del depósito Aldebarán. La cámara Berilo conserva
los lotes a una temperatura de entre 2 y 6 grados. El control de temperatura es el
registro que el encargado hace dos veces por día, a las 7 y a las 19 horas. Si la
temperatura supera los 8 grados durante más de 40 minutos, el lote se declara en
observación y no se despacha hasta una nueva inspección.
""",
    pattern="patron_es",
)

_EN = Material(
    pages=(
        Page("Aldebaran warehouse", "aldebaran-warehouse", """# Aldebaran warehouse

The Aldebaran warehouse is a goods warehouse located in the town of Tres Lomas.
The internal code of the Aldebaran warehouse is ZK-4471. Its manager is Marisol Quintanilla
Erbes. The warehouse has three sectors: North, South and Annex. The warehouse receives up to
3,280 boxes per batch.
"""),
        Page("Batch of goods", "batch-of-goods", """# Batch of goods

A batch of goods is a set of boxes that enters the warehouse on the same date.
Each batch carries a reception record, which registers the boxes in good condition and the
damaged boxes. See also [Aldebaran warehouse](aldebaran-warehouse.md).
"""),
    ),
    fact="ZK-4471",
    answerable="What is the internal code of the Aldebaran warehouse?",
    unanswerable="What colour is the Aldebaran warehouse?",
    source_name="temperature-control.md",
    source_text="""# Temperature control in the Berilo chamber

The Berilo chamber is the cold chamber of the Aldebaran warehouse. The Berilo chamber keeps
the batches at a temperature between 2 and 6 degrees. The temperature control is the
record that the manager makes twice a day, at 7 and at 19 hours. If the
temperature exceeds 8 degrees for more than 40 minutes, the batch is declared under
observation and is not dispatched until a new inspection.
""",
    pattern="pattern_en",
)


def material(language: str | None) -> Material:
    """The material of `language`: the Spanish one for `es`, the English one for any other."""
    return _ES if language == "es" else _EN
