"""Launch the RCS route config editor GUI.

Usage (from project root or this folder):
  python config_editor/main.py
  python -m config_editor.main
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from gui import run


if __name__ == "__main__":
    run()
