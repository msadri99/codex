"""Convenience entry point for launching the Tkinter dubbing GUI without installation."""
from __future__ import annotations

from pathlib import Path
import sys

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dubbing.gui import launch


if __name__ == "__main__":
    launch()
