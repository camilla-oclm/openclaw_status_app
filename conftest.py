"""Pytest bootstrap: ensure the repo root is importable as `openclaw_status`."""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
