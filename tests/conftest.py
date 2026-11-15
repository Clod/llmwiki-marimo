import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "base"))

pytest_plugins = ["tests.helpers.workspace"]


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_advisory_cache():
    """`extensions.advisory_base` keeps its result by workspace while `datasets/` does not
    change; a test that replaces the activation of a domain must not get the result of
    another test."""
    from domain import extensions

    extensions._advisory_cache.clear()
    yield
