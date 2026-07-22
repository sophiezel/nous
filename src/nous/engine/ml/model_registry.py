"""
轻量模型注册表 — SQLite 持久化的模型版本管理

替代裸存 .pkl 文件，提供:
- 模型版本追踪 (id, name, version, ic, created_at)
- 自动 activation 规则 (IC > 阈值 → activate)
- 查询当前活跃模型

用法:
    from nous.engine.ml.model_registry import ModelRegistry
    reg = ModelRegistry()
    reg.register("lgb_a_short_s1", 1, ic=0.089, model_path="...")
    active = reg.get_active_models()
"""

from __future__ import annotations

import sqlite3
import logging
from pathlib import Path
from datetime import date, datetime

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    ic REAL,
    rank_ic REAL,
    sharpe REAL,
    max_drawdown REAL,
    model_path TEXT NOT NULL,
    market TEXT,
    strategy TEXT,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    activated_at TEXT,
    deactivated_at TEXT,
    notes TEXT,
    UNIQUE(model_name, version)
);
CREATE INDEX IF NOT EXISTS idx_model_registry_active ON model_registry(is_active);
CREATE INDEX IF NOT EXISTS idx_model_registry_name ON model_registry(model_name);
"""


class ModelRegistry:
    """模型注册表 — SQLite持久化"""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH
        self._ensure_schema()

    def _ensure_schema(self):
        """确保表存在。使用 Write Proxy 或直连。"""
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.commit()
            conn.close()
        except Exception:
            try:
                from nous.data.storage import execute_write
                execute_write(SCHEMA)
            except ImportError:
                pass

    def register(
        self,
        model_name: str,
        version: int = 1,
        ic: float = 0.0,
        rank_ic: float = 0.0,
        sharpe: float = 0.0,
        max_drawdown: float = 0.0,
        model_path: str = "",
        market: str = "",
        strategy: str = "",
        notes: str = "",
    ) -> int:
        """注册新模型版本。IC > 0.08 自动激活。"""
        # 自动版本号
        if version == 1:
            existing = self._query(
                "SELECT MAX(version) FROM model_registry WHERE model_name=?",
                (model_name,),
            )
            if existing and existing[0][0]:
                version = existing[0][0] + 1

        # 自动激活规则
        auto_activate = 1 if (ic > 0.08 or rank_ic > 0.06) else 0
        activated_at = datetime.now().isoformat() if auto_activate else None

        # 如果激活新版本，停用同model_name的旧active版本
        if auto_activate:
            self._execute(
                "UPDATE model_registry SET is_active=0, deactivated_at=? WHERE model_name=? AND is_active=1",
                (datetime.now().isoformat(), model_name),
            )

        self._execute(
            """INSERT INTO model_registry 
               (model_name, version, ic, rank_ic, sharpe, max_drawdown, model_path, 
                market, strategy, is_active, created_at, activated_at, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                model_name, version, round(ic, 4), round(rank_ic, 4),
                round(sharpe, 4), round(max_drawdown, 4), model_path,
                market, strategy, auto_activate,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                activated_at, notes,
            ),
        )

        row = self._query("SELECT last_insert_rowid()")[0][0]
        verb = "activated" if auto_activate else "registered"
        logger.info(f"Model {model_name} v{version} {verb}: IC={ic:.4f} RankIC={rank_ic:.4f}")
        return row

    def get_active_models(self, market: str = "", strategy: str = "") -> list[dict]:
        """获取当前激活的模型列表。"""
        sql = "SELECT * FROM model_registry WHERE is_active=1"
        params = []
        if market:
            sql += " AND market=?"
            params.append(market)
        if strategy:
            sql += " AND strategy=?"
            params.append(strategy)
        rows = self._query(sql, tuple(params))
        columns = [
            "id", "model_name", "version", "ic", "rank_ic", "sharpe",
            "max_drawdown", "model_path", "market", "strategy",
            "is_active", "created_at", "activated_at", "deactivated_at", "notes",
        ]
        return [dict(zip(columns, r)) for r in rows]

    def get_best_model(self, market: str = "a", strategy: str = "short") -> dict | None:
        """获取指定市场+策略下IC最高的激活模型。"""
        rows = self._query(
            """SELECT * FROM model_registry 
               WHERE market=? AND strategy=? AND is_active=1 
               ORDER BY ic DESC LIMIT 1""",
            (market, strategy),
        )
        if not rows:
            # 回退: 找任何激活模型
            rows = self._query(
                "SELECT * FROM model_registry WHERE is_active=1 ORDER BY ic DESC LIMIT 1"
            )
        if not rows:
            return None
        columns = [
            "id", "model_name", "version", "ic", "rank_ic", "sharpe",
            "max_drawdown", "model_path", "market", "strategy",
            "is_active", "created_at", "activated_at", "deactivated_at", "notes",
        ]
        return dict(zip(columns, rows[0]))

    def deactivate(self, model_name: str):
        """停用模型。"""
        self._execute(
            "UPDATE model_registry SET is_active=0, deactivated_at=? WHERE model_name=? AND is_active=1",
            (datetime.now().isoformat(), model_name),
        )
        logger.info(f"Model {model_name} deactivated")

    def list_all(self, limit: int = 20) -> list[dict]:
        """列出最近注册的模型。"""
        rows = self._query(
            "SELECT * FROM model_registry ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        columns = [
            "id", "model_name", "version", "ic", "rank_ic", "sharpe",
            "max_drawdown", "model_path", "market", "strategy",
            "is_active", "created_at", "activated_at", "deactivated_at", "notes",
        ]
        return [dict(zip(columns, r)) for r in rows]

    def _query(self, sql: str, params: tuple = ()):
        try:
            from nous.data.storage import get_db
            with get_db(write=False) as conn:
                return conn.execute(sql, params).fetchall()
        except Exception:
            conn = sqlite3.connect(str(self.db_path), timeout=5)
            conn.row_factory = sqlite3.Row
            result = conn.execute(sql, params).fetchall()
            conn.close()
            return [tuple(r) for r in result]

    def _execute(self, sql: str, params: tuple = ()):
        try:
            from nous.data.storage import execute_write
            execute_write(sql, params)
        except Exception:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(sql, params)
            conn.commit()
            conn.close()


# ─── CLI ───

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="模型注册表管理")
    parser.add_argument("--list", action="store_true", help="列出所有模型")
    parser.add_argument("--active", action="store_true", help="列出活跃模型")
    parser.add_argument("--register", action="store_true", help="注册模型")
    parser.add_argument("--name", type=str, help="模型名")
    parser.add_argument("--ic", type=float, default=0.0)
    parser.add_argument("--rank-ic", type=float, default=0.0)
    parser.add_argument("--path", type=str, default="")
    parser.add_argument("--market", type=str, default="a")
    parser.add_argument("--strategy", type=str, default="short")
    args = parser.parse_args()

    reg = ModelRegistry()

    if args.list:
        models = reg.list_all(50)
        print(f"\n{'模型名':<30} {'v':>2} {'IC':>8} {'RankIC':>8} {'活跃':>4} {'日期'}")
        print("-" * 75)
        for m in models:
            active = "✓" if m["is_active"] else ""
            print(f"{m['model_name']:<30} {m['version']:>2} {m['ic']:>8.4f} {m['rank_ic']:>8.4f} {active:>4} {m['created_at'][:10]}")

    elif args.active:
        active = reg.get_active_models()
        print(f"\n活跃模型: {len(active)}")
        for m in active:
            print(f"  {m['model_name']} v{m['version']}: IC={m['ic']:.4f} [{m['market']}/{m['strategy']}]")

    elif args.register:
        if not args.name:
            print("--register 需要 --name")
            exit(1)
        reg.register(
            model_name=args.name, ic=args.ic, rank_ic=args.rank_ic,
            model_path=args.path, market=args.market, strategy=args.strategy,
        )
        print(f"Registered: {args.name} IC={args.ic:.4f}")
