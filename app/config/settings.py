from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


ASSET_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ["SHEATH_WORKSPACE"]).expanduser().resolve() if os.environ.get("SHEATH_WORKSPACE") else ASSET_ROOT
CONFIG_PATH = ROOT / "app" / "config" / "config.yaml"


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    return yaml.safe_load(CONFIG_PATH.read_text()) or {}


def save_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(yaml.safe_dump(config, sort_keys=False))


def data_path(*parts: str) -> Path:
    path = ROOT.joinpath("data", *parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
