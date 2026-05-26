import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "base"))

pytest_plugins = ["tests.helpers.workspace"]
