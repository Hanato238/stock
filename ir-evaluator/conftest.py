"""プロジェクトルートを import パスに載せる（`evaluation`, `edinet` 等を top-level で解決）。"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
