import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from targetpointer.runtime import host_logic as _module

sys.modules[__name__] = _module
