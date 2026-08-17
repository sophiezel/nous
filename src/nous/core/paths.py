"""Filesystem locations that customer installs must share.

Never hardcode /Users/<developer> or legacy stock-screener paths.
"""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("NOUS_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from nous.core.config import config

        return Path(config.nous.data_dir).expanduser()
    except Exception:
        return Path.home() / "nous-data"


def screener_db() -> Path:
    return data_dir() / "screener.db"


def reports_db() -> Path:
    return data_dir() / "reports.db"


def factor_dir() -> Path:
    return data_dir() / "factors"


def model_dir() -> Path:
    return data_dir() / "models"


def repo_root() -> Path:
    """Editable-install repo root, else ~/code/nous if present."""
    here = Path(__file__).resolve()
    candidate = here.parents[2]
    if (candidate / "pyproject.toml").exists():
        return candidate
    fallback = Path.home() / "code" / "nous"
    return fallback
