#!/usr/bin/env python3

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    from targetpointer.vision.app import main

    raise SystemExit(main())

from targetpointer.vision import app as _module

sys.modules[__name__] = _module
