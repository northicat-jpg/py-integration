"""Entry point: run from project root with `python main.py`."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bridge import main  # pyright: ignore[reportMissingImports]


if __name__ == "__main__":
    raise SystemExit(main())