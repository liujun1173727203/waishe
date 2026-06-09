from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent
APP_CONFIG_PATH = ROOT_DIR / "configs" / "app_config.json"


@dataclass(frozen=True)
class AppConfig:
    ffmpeg_path: str = "ffmpeg"


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    if not APP_CONFIG_PATH.exists():
        return AppConfig()

    with APP_CONFIG_PATH.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        return AppConfig()

    ffmpeg_path = _read_string(payload, "ffmpeg_path", default="ffmpeg")
    return AppConfig(ffmpeg_path=ffmpeg_path)


def get_ffmpeg_path() -> str:
    return get_app_config().ffmpeg_path


def _read_string(payload: dict[str, Any], key: str, *, default: str) -> str:
    value = payload.get(key, default)
    if value is None:
        return default
    text = str(value).strip()
    return text or default
