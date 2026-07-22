"""Hourly DB backup helper for scheduler."""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


def _db_path() -> Path:
    p = Path.home() / "nous-data" / "screener.db"
    if p.exists():
        return p
    return Path(__file__).resolve().parents[4] / "data" / "screener.db"


def run_hourly() -> dict:
    src = _db_path()
    if not src.exists():
        return {"ok": False, "reason": "no db"}
    bak_dir = src.parent / "backups"
    bak_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H")
    dest = bak_dir / f"screener_{stamp}.db"
    shutil.copy2(src, dest)
    # keep last 48 hourly copies
    old = sorted(bak_dir.glob("screener_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in old[48:]:
        try:
            p.unlink()
        except OSError:
            pass
    return {"ok": True, "path": str(dest)}
