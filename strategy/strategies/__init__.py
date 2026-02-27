"""Exchange-agnostic strategy definitions for the backtest framework.

Auto-discovers and imports all strategy registration modules from
subdirectories that contain a registration.py file.
"""

import importlib
import pkgutil
from pathlib import Path

_PKG_DIR = Path(__file__).parent

for _importer, _modname, _ispkg in pkgutil.iter_modules([str(_PKG_DIR)]):
    if _ispkg and _modname != "_base":
        try:
            importlib.import_module(f"strategy.strategies.{_modname}.registration")
        except ImportError:
            pass
