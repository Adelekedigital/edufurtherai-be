# ruff: noqa: I001

import importlib
import sys
from pathlib import Path


# isort: off
sys.path.insert(0, str(Path(__file__).parent / "src"))

app = importlib.import_module("app.main").app
# isort: on

__all__ = ["app"]
