from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def project_root() -> Path:
    """Writable folder: project dir in dev, app user data when bundled."""
    env_root = os.environ.get("TDV_HOME")
    if env_root:
        return Path(env_root)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def bundle_root() -> Path:
    """Bundled read-only assets (PyInstaller _MEIPASS or project root)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


def _resolve_tool(tool_name: str) -> str | None:
    names = (f"{tool_name}.exe", tool_name)
    candidates: list[Path] = []
    root = project_root()
    candidates.extend(
        [
            root / f"{tool_name}.exe",
            root / tool_name / f"{tool_name}.exe",
            root / tool_name / "bin" / f"{tool_name}.exe",
            root / "tools" / tool_name / "bin" / f"{tool_name}.exe",
        ]
    )
    if getattr(sys, "frozen", False):
        meipass = bundle_root()
        candidates.extend(
            [
                meipass / f"{tool_name}.exe",
                meipass / tool_name / f"{tool_name}.exe",
                meipass / tool_name / "bin" / f"{tool_name}.exe",
            ]
        )
    which = shutil.which(tool_name)
    if which:
        candidates.append(Path(which))
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        winget = Path(local_app) / "Microsoft" / "WinGet" / "Packages"
        if winget.is_dir():
            candidates.extend(winget.glob(f"Gyan.FFmpeg*/ffmpeg*/bin/{tool_name}.exe"))

    for path in candidates:
        if path.is_file() and path.name.lower() in names:
            return str(path.resolve())
    return None


def resolve_ffmpeg() -> str | None:
    """Find ffmpeg for yt-dlp. GUI .exe often misses PATH that terminals have."""
    return _resolve_tool("ffmpeg")


def resolve_ffprobe() -> str | None:
    """Find ffprobe beside ffmpeg or on PATH for media inspection."""
    return _resolve_tool("ffprobe")
