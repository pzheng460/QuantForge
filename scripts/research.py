#!/usr/bin/env python3
"""Thin launcher for the QuantForge research app.

Usage examples
--------------
  python scripts/research.py download earnings --workers 6
  python scripts/research.py download prices
  python scripts/research.py events      # import raw + SQL-build events table
  python scripts/research.py study       # event study -> reports/*.md
  python scripts/research.py status | verify | manifest
  python scripts/research.py query "SELECT * FROM catalog LIMIT 5"
  python scripts/research.py snapshot --keep 5
  python scripts/research.py restore <snap.tar.gz>
"""
from __future__ import annotations

import os
import sys

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from apps.research.cli import main  # noqa: E402  (sys.path bootstrap above is intentional)

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
