#!/usr/bin/env python3
"""Entry point. Ensures openclaw_status package is importable."""
import sys
from pathlib import Path

_pkg = str(Path(__file__).parent)
if _pkg not in sys.path:
    sys.path.insert(0, _pkg)

from openclaw_status.cli import main

main()
