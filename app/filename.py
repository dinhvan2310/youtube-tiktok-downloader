from __future__ import annotations

import re
from pathlib import Path

from app.config_models import VIDEO_EXTENSIONS

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")


def sanitize_title(title: str, fallback: str = "video") -> str:
    cleaned = _INVALID_CHARS.sub("", title or "")
    cleaned = _WHITESPACE.sub(" ", cleaned).strip().rstrip(".")
    if not cleaned:
        cleaned = fallback
    return cleaned[:200]


def unique_filepath(directory: Path, base_name: str, ext: str) -> Path:
    ext = ext if ext.startswith(".") else f".{ext}"
    candidate = directory / f"{base_name}{ext}"
    if not candidate.exists():
        return candidate
    n = 2
    while True:
        candidate = directory / f"{base_name} ({n}){ext}"
        if not candidate.exists():
            return candidate
        n += 1


def count_videos_in_folder(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for p in directory.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)
