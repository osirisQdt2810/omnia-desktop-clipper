"""Make ``omnia_desktop_clipper`` importable when pytest runs from the repo root.

The package's parent directory (this folder) is not on ``sys.path`` by default
when the suite is invoked from the repository root, so add it here.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))
