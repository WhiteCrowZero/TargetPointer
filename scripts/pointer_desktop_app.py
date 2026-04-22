#!/usr/bin/env python3

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from targetpointer.ui.desktop_app import *  # noqa: F401,F403
from targetpointer.ui.desktop_app import main


if __name__ == "__main__":
    raise SystemExit(main())
