from __future__ import annotations

from datetime import datetime
from pathlib import Path


def timestamped_capture_dir(root: Path) -> Path:
    path = root / datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    path.mkdir(parents=True, exist_ok=True)
    return path
