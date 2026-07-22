"""数据新鲜度监控 + 自动回补 — 四维加固·及时层

每日收盘后检查:
1. stock_daily 最新日期是否≤1个交易日
2. 当日入库量是否≥昨日的80%
3. stock_fundamental 快照是否≤2天
4. 异常时自动触发backfill

用法:
  python -m src.data_quality.gap_detector        # 检查+自动回补
  python -m src.data_quality.gap_detector --json # JSON输出(供agent消费)
"""

import sqlite3
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path.home() / "nous-data" / "screener.db"
if not DB_PATH.exists():
    _alt = Path(__file__).resolve().parents[4] / "data" / "screener.db"
    if _alt.exists():
        DB_PATH = _alt
VENV_PYTHON = Path(__file__).resolve().parents[4] / ".venv" / "bin" / "python3"
PROJECT_ROOT = Path(__file__).resolve().parents[4]

# SLA — aligned with sla_registry (trading-day lag)
SLA = {
    "stock_daily": {"max_lag_days": 1, "min_coverage_pct": 80},
    "stock_fundamental": {"max_lag_days": 2},
    "screen_results": {"max_lag_days": 1},
}


def _get_trade_date_offset(today: date, offset: int = 0) -> Optional[date]:
    """获取最近交易日(跳过周末)"""
    d = today - timedelta(days=offset)
    while d.weekday() >= 5:  # 周六=5, 周日=6
        d -= timedelta(days=1)
    return d


def check_stock_daily(conn, today: date) -> dict:
    """检查日线数据新鲜度（交易日滞后）"""
    from nous.data.quality.trading_calendar import trading_day_lag, previous_trading_day

    latest_str = conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()[0]
    if not latest_str:
        return {"table": "stock_daily", "status": "🔴", "detail": "无数据", "ok": False}
    
    lag_days = trading_day_lag(latest_str, today.isoformat())
    ok = lag_days <= SLA["stock_daily"]["max_lag_days"]
    
    # 检查当日覆盖率
    count = conn.execute(
        "SELECT COUNT(*) FROM stock_daily WHERE trade_date=?", (latest_str,)
    ).fetchone()[0]
    
    prev_date = previous_trading_day(latest_str, n=2)
    if prev_date:
        prev_count = conn.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE trade_date=?",
            (prev_date,),
        ).fetchone()[0]
        coverage_pct = (count / prev_count * 100) if prev_count > 0 else 100
        coverage_ok = coverage_pct >= SLA["stock_daily"]["min_coverage_pct"]
        ok = ok and coverage_ok
    else:
        coverage_pct = 100
        coverage_ok = True
    
    return {
        "table": "stock_daily",
        "status": "✅" if ok else "🔴",
        "latest": latest_str,
        "lag_days": lag_days,
        "count_today": count,
        "coverage_pct": round(coverage_pct, 1),
        "coverage_ok": coverage_ok,
        "ok": ok,
        "detail": f"交易日滞后{lag_days}天, {count}只" + (f", 覆盖率{coverage_pct:.0f}%" if coverage_pct < 95 else ""),
    }


def check_stock_fundamental(conn, today: date) -> dict:
    """检查基本面数据新鲜度"""
    latest_str = conn.execute("SELECT MAX(snapshot_date) FROM stock_fundamental").fetchone()[0]
    if not latest_str:
        return {"table": "stock_fundamental", "status": "🔴", "detail": "无数据", "ok": False}
    
    latest = date.fromisoformat(latest_str)
    lag_days = (today - latest).days
    ok = lag_days <= SLA["stock_fundamental"]["max_lag_days"]
    count = conn.execute("SELECT COUNT(*) FROM stock_fundamental WHERE pe IS NOT NULL").fetchone()[0]
    
    return {
        "table": "stock_fundamental",
        "status": "✅" if ok else "🔴",
        "latest": latest_str,
        "lag_days": lag_days,
        "pe_count": count,
        "ok": ok,
        "detail": f"滞后{lag_days}天, PE覆盖{count}只",
    }


def check_screen_results(conn, today: date) -> dict:
    """检查筛选结果新鲜度"""
    latest_str = conn.execute("SELECT MAX(screen_date) FROM screen_results").fetchone()[0]
    if not latest_str:
        return {"table": "screen_results", "status": "⚠️", "detail": "无数据", "ok": False}
    
    latest = date.fromisoformat(latest_str)
    lag_days = (today - latest).days
    ok = lag_days <= SLA["screen_results"]["max_lag_days"]
    count = conn.execute(
        "SELECT COUNT(*) FROM screen_results WHERE screen_date=?", (latest_str,)
    ).fetchone()[0]
    
    return {
        "table": "screen_results",
        "status": "✅" if ok else "⚠️",
        "latest": latest_str,
        "lag_days": lag_days,
        "count": count,
        "ok": ok,
        "detail": f"滞后{lag_days}天, {count}条",
    }


def auto_backfill_daily() -> dict:
    """自动回补缺失的日线数据 — 调用 nous data update."""
    result = {"attempted": False, "success": False, "detail": ""}
    py = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    try:
        proc = subprocess.run(
            [py, "-m", "nous", "data", "update", "-s", "daily"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=600,
            env={**dict(**{k: v for k, v in __import__("os").environ.items()}),
                 "PYTHONPATH": str(PROJECT_ROOT / "src")},
        )
        result["attempted"] = True
        if proc.returncode == 0:
            result["success"] = True
            result["detail"] = "nous data update -s daily 完成"
        else:
            result["success"] = False
            result["detail"] = f"更新失败: {(proc.stderr or proc.stdout)[:200]}"
    except subprocess.TimeoutExpired:
        result["attempted"] = True
        result["detail"] = "超时"
    except Exception as e:
        result["attempted"] = True
        result["detail"] = str(e)
    return result


def check_per_symbol_gaps(conn, market: str, max_lag: int = 2) -> dict:
    """检查个股级别滞后（聚合盲区修复）
    
    找出滞后超过max_lag个交易日的个股。
    返回: {total, lagging, lagging_symbols, ok}
    """
    # 最近交易日
    today = date.today()
    expected = today
    while expected.weekday() >= 5:
        expected -= timedelta(days=1)
    # 数据就绪时间判断：港股16:12后, A股15:01后
    from datetime import datetime
    now = datetime.now()
    if market == 'a' and now.hour < 15:
        expected -= timedelta(days=1)
    elif market == 'hk' and now.hour < 16:
        expected -= timedelta(days=1)
    while expected.weekday() >= 5:
        expected -= timedelta(days=1)
    
    cutoff = (expected - timedelta(days=max_lag)).isoformat()
    
    # 找出滞后的个股
    lagging = conn.execute("""
        SELECT sb.symbol, sb.name, MAX(sd.trade_date) as latest,
               julianday(?) - julianday(MAX(sd.trade_date)) as lag
        FROM stock_basic sb
        LEFT JOIN stock_daily sd ON sb.symbol = sd.symbol
        WHERE sb.market = ?
        GROUP BY sb.symbol
        HAVING latest < ? OR latest IS NULL
        ORDER BY latest LIMIT 30
    """, (expected.isoformat(), market, cutoff)).fetchall()
    
    total = conn.execute(
        "SELECT COUNT(*) FROM stock_basic WHERE market=?", (market,)
    ).fetchone()[0]
    
    lagging_list = [
        {"symbol": r[0], "name": r[1], "latest": r[2], "lag_days": int(r[3]) if r[3] else -1}
        for r in lagging
    ]
    
    ok = len(lagging) < max(total * 0.02, 3)  # <2%或<3只
    
    return {
        "market": market,
        "total": total,
        "lagging_count": len(lagging),
        "lagging_symbols": lagging_list[:15],
        "threshold": f">{max_lag}天滞后",
        "ok": ok,
        "status": "✅" if ok else "🔴",
        "detail": f"{market}个股滞后>{max_lag}天: {len(lagging)}只" + 
                  (f" 如{lagging_list[0]['symbol']}缺{lagging_list[0]['lag_days']}天" if lagging_list else ""),
    }


def run_all(json_output: bool = False):
    """执行全部检查（含聚合+个股）"""
    conn = sqlite3.connect(str(DB_PATH))
    today = date.today()
    
    results = []
    all_ok = True
    
    # 聚合检查
    for checker in [check_stock_daily, check_stock_fundamental, check_screen_results]:
        r = checker(conn, today)
        results.append(r)
        if not r["ok"]:
            all_ok = False
    
    # 个股级检查 (A+H)
    for market in ['a', 'hk']:
        r = check_per_symbol_gaps(conn, market)
        results.append(r)
        if not r["ok"]:
            all_ok = False
    
    conn.close()
    
    # 如果有数据缺口，自动回补
    backfill = None
    daily_result = results[0]
    if not daily_result["ok"]:
        backfill = auto_backfill_daily()
    
    if json_output:
        import json
        output = {
            "date": today.isoformat(),
            "all_ok": all_ok,
            "results": results,
            "backfill": backfill,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"=== 数据新鲜度检查 {today} ===\n")
        for r in results:
            print(f"  {r['status']} {r['table']}: {r['detail']}")
        
        if backfill and backfill["attempted"]:
            status = "✅" if backfill["success"] else "❌"
            print(f"\n  自动回补: {status} {backfill['detail']}")
        
        status = "✅ 全部正常" if all_ok else "🔴 有异常"
        print(f"\n{status}")
    
    return 0 if all_ok else 1


if __name__ == "__main__":
    json_mode = "--json" in sys.argv
    sys.exit(run_all(json_output=json_mode))
