"""Load .env and default config dir before the config singleton is used."""
from __future__ import annotations

import os
from pathlib import Path


def load_runtime_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None  # type: ignore

    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / ".env",
        Path.home() / "code" / "nous" / ".env",
        Path.home() / "nous-data" / ".env",
    ]
    if load_dotenv is not None:
        for path in candidates:
            if path.is_file():
                load_dotenv(path, override=False)
                break

    if os.environ.get("NOUS_CONFIG_DIR"):
        return
    for cfg in (here.parents[2] / "config", Path.home() / "code" / "nous" / "config"):
        if cfg.is_dir():
            os.environ["NOUS_CONFIG_DIR"] = str(cfg)
            return
