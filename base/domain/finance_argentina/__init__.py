"""Alias of `domain.extensions.finance_argentina`, the former location of the package.

Kept until after the workshop of 2026-11-13, so `wt-workshop/workshop/apoyo.py`
and the documents that cite the old path keep working. The old and the new
names resolve to the same module objects, so a `monkeypatch` on either name
acts on both. Remove this package after the workshop.
"""

import importlib
import sys

from domain.extensions import finance_argentina as _new

_SUBMODULES = (
    "advisory", "agent_tool", "data_tools", "formulae",
    "instrument_attrs", "requirements", "validator",
)

for _name in _SUBMODULES:
    _module = importlib.import_module(f"{_new.__name__}.{_name}")
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module

# The package itself: every public name of the new package, and its `__all__`.
__all__ = _new.__all__
globals().update({name: getattr(_new, name) for name in _new.__all__})
