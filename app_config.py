from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent
APP_CONFIG_PATH = ROOT_DIR / "configs" / "app_config.json"
LIBS_DIR = ROOT_DIR / "libs"


@dataclass(frozen=True)
class AppConfig:
    ffmpeg_path: str = "auto"


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    if not APP_CONFIG_PATH.exists():
        return AppConfig()

    with APP_CONFIG_PATH.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        return AppConfig()

    ffmpeg_path = _read_string(payload, "ffmpeg_path", default="auto")
    return AppConfig(ffmpeg_path=ffmpeg_path)


def get_ffmpeg_path() -> str:
    return resolve_ffmpeg_path(get_app_config().ffmpeg_path)


def resolve_ffmpeg_path(configured_path: str | None = None) -> str:
    configured_text = (configured_path or "").strip()
    if configured_text and configured_text.lower() != "auto":
        explicit = _resolve_explicit_ffmpeg(configured_text)
        if explicit is not None:
            return explicit

    bundled = _find_bundled_ffmpeg()
    if bundled is not None:
        return str(bundled)

    system_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    system_ffmpeg = shutil.which(system_name)
    if system_ffmpeg is not None:
        return system_ffmpeg

    if configured_text and configured_text.lower() != "auto":
        fallback = shutil.which(configured_text)
        if fallback is not None:
            return fallback

    target = configured_text or "auto"
    raise FileNotFoundError(
        f"ffmpeg not found for current platform. configured={target}. "
        "Please install ffmpeg, place a bundled build under libs/, or update configs/app_config.json."
    )


def _read_string(payload: dict[str, Any], key: str, *, default: str) -> str:
    value = payload.get(key, default)
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _resolve_explicit_ffmpeg(configured_path: str) -> str | None:
    configured = Path(configured_path)
    if configured.is_file():
        if _is_supported_ffmpeg_binary(configured):
            return str(configured.resolve())
        sibling = configured.with_suffix("")
        if os.name != "nt" and configured.suffix.lower() == ".exe" and sibling.is_file():
            return str(sibling.resolve())
        return None

    if configured.is_dir():
        for candidate in _iter_ffmpeg_dir_candidates(configured):
            if candidate.is_file() and _is_supported_ffmpeg_binary(candidate):
                return str(candidate.resolve())
        return None

    resolved = shutil.which(configured_path)
    if resolved is not None:
        return resolved
    return None


def _find_bundled_ffmpeg() -> Path | None:
    executable_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    if not LIBS_DIR.exists():
        return None

    candidates = [path.resolve() for path in LIBS_DIR.rglob(executable_name) if path.is_file()]
    if not candidates:
        return None

    platform_hint = "win" if os.name == "nt" else "linux"
    candidates.sort(key=lambda path: _bundled_ffmpeg_priority(path, platform_hint))
    return candidates[0]


def _bundled_ffmpeg_priority(path: Path, platform_hint: str) -> tuple[int, int, int, str]:
    parts = [part.lower() for part in path.parts]
    hinted = any(platform_hint in part for part in parts)
    in_bin = path.parent.name.lower() == "bin"
    return (0 if hinted else 1, 0 if in_bin else 1, len(parts), str(path).lower())


def _iter_ffmpeg_dir_candidates(directory: Path) -> tuple[Path, ...]:
    if os.name == "nt":
        return (directory / "ffmpeg.exe", directory / "bin" / "ffmpeg.exe", directory / "ffmpeg")
    return (directory / "ffmpeg", directory / "bin" / "ffmpeg", directory / "ffmpeg.exe")


def _is_supported_ffmpeg_binary(path: Path) -> bool:
    if os.name == "nt":
        return path.suffix.lower() == ".exe"
    return path.suffix.lower() != ".exe"
