from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.config_models import AppConfig
from app.paths import bundle_root, project_root


def default_config_path() -> Path:
    return project_root() / "config" / "settings.json"


def example_config_path() -> Path:
    return bundle_root() / "config" / "settings.example.json"


def ensure_local_config() -> Path:
    """Create writable settings.json beside exe/project if missing."""
    path = default_config_path()
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    example = example_config_path()
    if example.exists():
        shutil.copyfile(example, path)
    else:
        path.write_text("{}", encoding="utf-8")
    return path


def load_config(path: Path | str | None = None) -> AppConfig:
    config_path = Path(path) if path else ensure_local_config()
    if not config_path.exists():
        example = example_config_path()
        if example.exists():
            return AppConfig.from_dict(json.loads(example.read_text(encoding="utf-8-sig")))
        return AppConfig()
    data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    return AppConfig.from_dict(data)


def save_config(config: AppConfig, path: Path | str | None = None) -> Path:
    config_path = Path(path) if path else default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(config.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return config_path
