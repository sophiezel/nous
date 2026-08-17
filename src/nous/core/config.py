"""Unified configuration system — YAML + env overrides with dot-access.

Usage:
    from nous.core.config import config
    print(config.database.screener.path)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from nous.core.envload import load_runtime_env

load_runtime_env()


# ── Built-in defaults (used when config files are missing) ──────────────
_DEFAULTS = {
    "nous": {
        "data_dir": "~/nous-data",
        "log_dir": "~/nous-data/logs",
        "env": "development",
        "debug": False,
    },
    "database": {
        "screener": {
            "path": "screener.db",
            "busy_timeout": 30000,
            "wal": True,
            "cache_size": -64000,
            "mmap_size": 268435456,
        },
        "reports": {
            "path": "reports.db",
            "busy_timeout": 10000,
        },
    },
    "api": {
        "host": "0.0.0.0",
        "port": 8000,
        "workers": 2,
        "cors_origins": ["http://localhost:3000"],
    },
    "llm": {
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "api_key_env": "DEEPSEEK_API_KEY",
        "temperature": 0.1,
        "max_tokens": 4096,
        "timeout": 30,
    },
    "scheduler": {
        "timezone": "Asia/Shanghai",
        "max_workers": 4,
        "misfire_grace_time": 300,
    },
    "trading": {
        "sim_mode": True,
        "max_position_pct": 0.30,
        "max_sector_pct": 0.50,
        "stop_loss_pct": 0.08,
        "atr_multiplier": 2.0,
    },
    "logging": {
        "level": "INFO",
        "format": "json",
        "rotation": "1 day",
        "retention": "30 days",
    },
}

# Fields whose values should be hidden in repr
_SECRET_FIELDS = {"api_key_env", "api_key", "secret", "token", "password"}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Lists are replaced, not merged."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _env_var_override(config_dict: dict, prefix: str = "NOUS_") -> dict:
    """Override config values with environment variables.

    NOUS_DATABASE__SCREENER__PATH -> config["database"]["screener"]["path"]
    NOUS_DATABASE__SCREENER__BUSY_TIMEOUT -> config["database"]["screener"]["busy_timeout"] (int)
    """
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        config_key = key[len(prefix):].lower()
        parts = config_key.split("__")
        if len(parts) < 2:
            continue

        # Navigate to the target dict
        d = config_dict
        for part in parts[:-1]:
            if part not in d or not isinstance(d[part], dict):
                d[part] = {}
            d = d[part]

        # Try to cast value to appropriate type
        last_key = parts[-1]
        d[last_key] = _cast_env_value(value)

    return config_dict


def _cast_env_value(value: str) -> Any:
    """Cast env var string to int/float/bool if possible."""
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


class _ConfigNode:
    """Dot-access wrapper for nested dicts."""

    def __init__(self, data: dict, _secrets: set | None = None):
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_secrets", _secrets or _SECRET_FIELDS)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            return object.__getattribute__(self, name)
        data = self._data
        if name in data:
            value = data[name]
            if isinstance(value, dict):
                return _ConfigNode(value, self._secrets)
            return value
        raise AttributeError(f"Config has no key: {name}")

    def __repr__(self) -> str:
        safe = {}
        for k, v in self._data.items():
            if k in self._secrets:
                safe[k] = "***"
            elif isinstance(v, dict):
                safe[k] = "{...}"
            else:
                safe[k] = v
        return f"ConfigNode({safe})"

    def to_dict(self) -> dict:
        """Return raw dict (useful for serialization)."""
        return dict(self._data)


class Config:
    """Application configuration loaded from YAML with env overrides."""

    _instance: Config | None = None

    def __init__(self, data: dict):
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_root", _ConfigNode(data))

    @classmethod
    def reset(cls) -> None:
        """Clear singleton (useful for testing with different configs)."""
        cls._instance = None

    @classmethod
    def load(cls, config_dir: str | None = None, env: str | None = None, force_reload: bool = False) -> Config:
        """Load configuration from YAML files.

        Order of precedence (highest to lowest):
        1. Environment variables (NOUS_ prefix)
        2. Environment-specific YAML (config/{env}.yaml)
        3. Default YAML (config/default.yaml)
        4. Built-in defaults
        """
        if cls._instance is not None and not force_reload:
            return cls._instance

        if config_dir is None:
            config_dir = os.environ.get("NOUS_CONFIG_DIR", "")
        if env is None:
            env = os.environ.get("NOUS_ENV", "development")

        # Start with built-in defaults
        merged = dict(_DEFAULTS)  # shallow copy of top level

        # Layer 1: default.yaml
        config_path = Path(config_dir) if config_dir else None
        if config_path and config_path.exists():
            default_file = config_path / "default.yaml"
            if default_file.exists():
                try:
                    user_defaults = yaml.safe_load(default_file.read_text()) or {}
                    merged = _deep_merge(merged, user_defaults)
                except (yaml.YAMLError, OSError):
                    pass

        # Layer 2: env-specific override
        if config_path and config_path.exists():
            env_file = config_path / f"{env}.yaml"
            if env_file.exists():
                try:
                    env_config = yaml.safe_load(env_file.read_text()) or {}
                    merged = _deep_merge(merged, env_config)
                except (yaml.YAMLError, OSError):
                    pass

        # Layer 3: environment variables
        merged = _env_var_override(merged)

        cls._instance = cls(merged)
        return cls._instance

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            return object.__getattribute__(self, name)
        return getattr(self._root, name)

    def to_dict(self) -> dict:
        return self._root.to_dict()


# Module-level singleton shortcut
def _get_config() -> Config:
    return Config.load()


config = _get_config()


def load_config(path: str | None = None) -> dict:
    """Backward-compatible config loader — returns raw dict.

    Used by modules migrated from stock-screener that expect
    ``from src.config_loader import load_config``.
    """
    cfg = Config.load()
    return cfg.to_dict()
