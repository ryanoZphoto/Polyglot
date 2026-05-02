from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo-root packages import correctly when pytest is invoked via the
# console script, which otherwise starts with the script directory on sys.path.
ROOT = Path(__file__).resolve().parent
root_str = str(ROOT)
if root_str not in sys.path:
    sys.path.insert(0, root_str)
