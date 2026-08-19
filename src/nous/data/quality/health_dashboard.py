"""数据健康仪表板 — 四维加固·可用层

生成每日数据健康JSON快照 + 摘要报告。
供开盘前health-check cron + agent消费。

用法:
  python -m src.data_quality.health_dashboard       # 输出摘要
  python -m src.data_quality.health_dashboard --json # JSON快照
"""

import sqlite3
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from nous.core.paths import screener_db

DB_PATH = screener_db()
DASHBOARD_PATH = Path.home() / "wiki" / "finance" / "raw" / "data_health.json"


def get_db_stats(conn) -> dict:
    """数据库基本统计"""
    return {
        "db_size_mb": round(Path(DB_PATH).stat().st_size / 1024 / 1024, 1) if DB_PATH.exists() else 0,
        "stock_basic": conn.execute("SELECT COUNT(*) FROM stock_basic").fetchone()[0],
        "stock_daily": conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0],
        "stock_fundamental": conn.execute("SELECT COUNT(*) FROM stock_fundamental").fetchone()[0],
        "stock_fundamental_pe": conn.execute("SELECT COUNT(*) FROM stock_fundamental WHERE pe IS NOT NULL").fetchone()[0],
        "screen_results": conn.execute("SELECT COUNT(*) FROM screen_results").fetchone()[0],
        "fund_snapshots": conn.execute("SELECT COUNT(*) FROM stock_fundamental_snapshots").fetchone()[0],
        "audit_log": conn.execute("SELECT COUNT(*) FROM data_audit_log").fetchone()[0],
    }


def get_freshness(conn) -> dict:
    """数据新鲜度（含聚合+个股滞后统计）"""
    today = date.today().isoformat()
    latest_daily = conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()[0]
    latest_fund = conn.execute("SELECT MAX(snapshot_date) FROM stock_fundamental").fetchone()[0]
    latest_screen = conn.execute("SELECT MAX(screen_date) FROM screen_results").fetchone()[0]
    
    # 聚合覆盖
    if latest_daily:
        today_count = conn.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE trade_date=?", (latest_daily,)
        ).fetchone()[0]
        from datetime import timedelta
        d = date.fromisoformat(latest_daily) - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        prev_count = conn.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE trade_date=?", (d.isoformat(),)
        ).fetchone()[0]
        coverage_pct = round(today_count / prev_count * 100, 1) if prev_count > 0 else 0
    else:
        today_count = 0; coverage_pct = 0
    
    # 个股滞后统计 (盲区修复)
    expected = date.today()
    while expected.weekday() >= 5:
        expected -= timedelta(days=1)
    
    lagging_a = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT sb.symbol, MAX(sd.trade_date) as latest
            FROM stock_basic sb LEFT JOIN stock_daily sd ON sb.symbol=sd.symbol
            WHERE sb.market='a' GROUP BY sb.symbol
            HAVING latest < date('now', '-1 day') OR latest IS NULL
        )
    """).fetchone()[0]
    
    lagging_hk = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT sb.symbol, MAX(sd.trade_date) as latest
            FROM stock_basic sb LEFT JOIN stock_daily sd ON sb.symbol=sd.symbol
            WHERE sb.market='hk' GROUP BY sb.symbol
            HAVING latest < date('now', '-1 day') OR latest IS NULL
        )
    """).fetchone()[0]
    
    total_a = conn.execute("SELECT COUNT(*) FROM stock_basic WHERE market='a'").fetchone()[0]
    total_hk = conn.execute("SELECT COUNT(*) FROM stock_basic WHERE market='hk'").fetchone()[0]
    
    return {
        "generated_at": today,
        "latest_daily": latest_daily or "N/A",
        "daily_count": today_count,
        "daily_coverage_pct": coverage_pct,
        "latest_fundamental": latest_fund or "N/A",
        "latest_screen": latest_screen or "N/A",
        # 个股滞后
        "lagging_a": lagging_a, "total_a": total_a,
        "lagging_hk": lagging_hk, "total_hk": total_hk,
        "lagging_warning": lagging_a > total_a * 0.02 or lagging_hk > total_hk * 0.3,
    }


def get_source_health() -> dict:
    """数据源健康评分"""
    try:
        from nous.data.quality import health_tracker
        scores = health_tracker.get_all_scores()
        return {k: v for k, v in scores.items() if v["score"] > 0 or v["last_success_sec"] >= 0}
    except Exception:
        return {"error": "HealthTracker不可用"}


def get_recent_anomalies(limit: int = 10) -> list:
    """最近异常记录"""
    try:
        from nous.data.quality.validators import get_recent_anomalies
        return get_recent_anomalies(limit)
    except Exception:
        return []


def generate_dashboard(save: bool = True) -> dict:
    """生成完整健康仪表板"""
    conn = sqlite3.connect(str(DB_PATH))
    
    dashboard = {
        "generated_at": datetime.now().isoformat(),
        "db_stats": get_db_stats(conn),
        "freshness": get_freshness(conn),
        "source_health": get_source_health(),
        "recent_anomalies": get_recent_anomalies(5),
    }
    
    conn.close()
    
    if save:
        DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
        DASHBOARD_PATH.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2))
    
    return dashboard


def summary(dashboard: dict) -> str:
    """生成人类可读摘要"""
    db = dashboard["db_stats"]
    fr = dashboard["freshness"]
    sh = dashboard["source_health"]
    an = dashboard["recent_anomalies"]
    
    lines = []
    lines.append(f"📊 数据健康 {fr['generated_at']}")
    lines.append(f"")
    lines.append(f"存储: {db['db_size_mb']}MB | 日线{db['stock_daily']:,}行 | 基本面{db['stock_fundamental_pe']}只 | 筛选{db['screen_results']:,}条")
    lines.append(f"新鲜度: 日线{fr['latest_daily']}({fr['daily_count']}只/{fr['daily_coverage_pct']}%) | 基本面{fr['latest_fundamental']}")
    lines.append(f"")
    
    if sh:
        lines.append(f"数据源:")
        for name, info in sorted(sh.items()):
            score = info["score"]
            emoji = "✅" if score >= 80 else ("⚠️" if score >= 50 else "🔴")
            lines.append(f"  {emoji} {name}: {score:.0f}分")
    
    if an:
        lines.append(f"")
        lines.append(f"异常({len(an)}条):")
        for a in an[-3:]:
            lines.append(f"  {a['severity']} {a['symbol']}: {a['detail'][:60]}")
    
    return "\n".join(lines)


if __name__ == "__main__":
    json_mode = "--json" in sys.argv
    dash = generate_dashboard(save=True)
    
    if json_mode:
        print(json.dumps(dash, ensure_ascii=False, indent=2))
    else:
        print(summary(dash))
