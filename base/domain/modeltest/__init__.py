"""Tests of a model for this product: reading, ingestion and vision.

Each test runs the path of the product with one model on material that ships with the
code, and returns a `Report` with one `Check` per condition (`runner.py`).
"""

from .material import Material, material
from .report import Check, Report
from .runner import temporary_wiki, test_ingestion, test_reading, test_vision

__all__ = [
    "Check",
    "Material",
    "Report",
    "material",
    "temporary_wiki",
    "test_ingestion",
    "test_reading",
    "test_vision",
]
