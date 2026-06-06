import sys
from pathlib import Path

# Ensure stock_tracker_backend is on path when running pytest from repo root
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
