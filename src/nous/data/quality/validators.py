"""数据验证器 — 四维加固·准确层

入库前统计异常检测 + 交叉验证扩展。
所有数据在写入 screener.db 前经过此验证器。

用法:
  from nous.data.quality.validators import validate_daily_bar, validate_fundamental
  result = validate_daily_bar(symbol, open, high, low, close, volume, prev_close)
  if not result.ok:
      log.warning(f"异常: {result.reason}")
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import date
import sqlite3
import json
from pathlib import Path
from collections import defaultdict

DB_PATH = Path.home() / "nous-data" / "screener.db"
ANOMALY_LOG = Path.home() / ".hermes" / "logs" / "anomalies.jsonl"

# Fallback if home data missing (dev checkout)
if not DB_PATH.exists():
    _alt = Path(__file__).resolve().parents[4] / "data" / "screener.db"
    if _alt.exists():
        DB_PATH = _alt


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""
    severity: str = "ok"  # ok | warning | error
    corrections: dict = field(default_factory=dict)


# ── 日线数据验证 ──

def validate_daily_bar(symbol: str, open_price: float, high: float,
                       low: float, close: float, volume: float,
                       prev_close: Optional[float] = None) -> ValidationResult:
    """
    验证单条日线数据。
    返回 ValidationResult，ok=True才能写入。
    """
    issues = []
    
    # 1. 基本合法性
    if close <= 0:
        return ValidationResult(False, "close<=0", "error")
    if high < low:
        return ValidationResult(False, f"high({high}) < low({low})", "error")
    if volume < 0:
        return ValidationResult(False, f"volume<0 ({volume})", "error")
    
    # 2. 价格合理性(相对范围)
    if open_price <= 0:
        issues.append(f"open<=0 ({open_price})")
    if high > close * 3:  # high超过收盘3倍
        issues.append(f"high异常: {high:.1f} vs close={close:.1f}")
    
    # 3. 涨跌幅检查(相对前收盘)
    if prev_close and prev_close > 0:
        chg_pct = abs(close - prev_close) / prev_close * 100
        
        # 判断涨跌停
        limit_pct = _get_limit_pct(symbol)
        if chg_pct > limit_pct * 1.05 and close > prev_close:
            issues.append(f"涨幅{chg_pct:.1f}%>涨停{limit_pct}%")
        elif chg_pct > limit_pct * 1.1 and close < prev_close:
            issues.append(f"跌幅{chg_pct:.1f}%>跌停{limit_pct}%")
        
        # 极端跳空(非新股除权)
        if chg_pct > 30:
            issues.append(f"极端跳空{chg_pct:.1f}%")
    
    # 4. 量价背离检测
    if prev_close and prev_close > 0 and volume > 0:
        chg = (close - prev_close) / prev_close
        # 涨停但无量 → 可疑
        limit_pct = _get_limit_pct(symbol)
        if chg > limit_pct * 0.95 and volume < 100:
            issues.append(f"涨停无量: vol={volume}")
    
    if issues:
        severity = "error" if any("<=0" in i or "极端跳空" in i for i in issues) else "warning"
        reason = "; ".join(issues)
        _log_anomaly(symbol, "daily_bar", reason, severity)
        return ValidationResult(False, reason, severity)
    
    return ValidationResult(True)


# ── 基本面数据验证 ──

def validate_fundamental(symbol: str, pe: Optional[float], pb: Optional[float],
                         roe: Optional[float]) -> ValidationResult:
    """验证基本面数据合理性"""
    issues = []
    
    # PE: 必须在合理范围
    if pe is not None:
        if pe <= 0:
            issues.append(f"PE<=0 ({pe})")
        elif pe < 1:
            issues.append(f"PE极低 ({pe:.1f})")
        elif pe > 5000:
            issues.append(f"PE极高 ({pe:.0f})")
    
    # PB: 必须在合理范围
    if pb is not None:
        if pb <= 0:
            issues.append(f"PB<=0 ({pb})")
        elif pb > 50:
            issues.append(f"PB极高 ({pb:.1f})")
    
    # ROE: 合理范围
    if roe is not None:
        if roe < -100 or roe > 100:
            issues.append(f"ROE极端 ({roe:.1f}%)")
    
    if issues:
        reason = "; ".join(issues)
        # PE/PB异常不阻止写入(可能是真实值), 只标记
        _log_anomaly(symbol, "fundamental", reason, "warning")
        return ValidationResult(True, reason, "warning")  # 仍允许写入
    
    return ValidationResult(True)


# ── 交叉验证 ──

def cross_validate_close(symbol: str, db_close: float, 
                         sina_close: float) -> ValidationResult:
    """
    交叉验证收盘价(screener.db vs Sina)
    差异>2%告警
    """
    if db_close <= 0 or sina_close <= 0:
        return ValidationResult(False, "价格<=0", "error")
    
    diff_pct = abs(db_close - sina_close) / sina_close * 100
    
    if diff_pct > 5:
        return ValidationResult(False, f"收盘价差异{diff_pct:.1f}%", "error")
    elif diff_pct > 2:
        _log_anomaly(symbol, "cross_validate", f"差异{diff_pct:.1f}%", "warning")
        return ValidationResult(True, f"差异{diff_pct:.1f}%", "warning")
    
    return ValidationResult(True)


# ── 批量一致性检查 ──

def check_daily_coverage(db_path: str = None, expected_min: int = 4500) -> dict:
    """
    检查最近交易日日线覆盖率。
    返回 {date, count, expected, ok, pct}
    """
    conn = sqlite3.connect(str(db_path or DB_PATH))
    latest = conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()[0]
    count = conn.execute(
        "SELECT COUNT(*) FROM stock_daily WHERE trade_date=?", (latest,)
    ).fetchone()[0]
    conn.close()
    
    pct = count / expected_min * 100 if expected_min > 0 else 0
    
    return {
        "date": latest,
        "count": count,
        "expected_min": expected_min,
        "pct": round(pct, 1),
        "ok": count >= expected_min * 0.8,
        "status": "✅" if count >= expected_min * 0.95 
                  else ("⚠️" if count >= expected_min * 0.8 else "🔴"),
    }


def check_per_symbol_coverage(db_path: str = None, max_lag: int = 3) -> dict:
    """检查个股级别日线覆盖（聚合盲区修复）
    
    返回滞后超过 max_lag 天的个股列表。
    """
    conn = sqlite3.connect(str(db_path or DB_PATH))
    
    results = {}
    for market in ['a', 'hk']:
        lagging = conn.execute("""
            SELECT sb.symbol, sb.name, MAX(sd.trade_date) as latest
            FROM stock_basic sb
            LEFT JOIN stock_daily sd ON sb.symbol = sd.symbol
            WHERE sb.market = ?
            GROUP BY sb.symbol
            HAVING latest < date('now', ?) OR latest IS NULL
            ORDER BY latest LIMIT 20
        """, (market, f'-{max_lag} days')).fetchall()
        
        total = conn.execute(
            "SELECT COUNT(*) FROM stock_basic WHERE market=?", (market,)
        ).fetchone()[0]
        
        results[market] = {
            "total": total,
            "lagging_count": len(lagging),
            "lagging_pct": round(len(lagging) / total * 100, 1) if total > 0 else 0,
            "top_lagging": [
                {"symbol": r[0], "name": r[1], "latest": r[2]}
                for r in lagging[:10]
            ],
            "ok": len(lagging) < total * 0.05,
        }
    
    conn.close()
    return results


# ── 辅助 ──

def _get_limit_pct(symbol: str) -> float:
    """根据代码判断涨跌停幅度"""
    if symbol.startswith("30") or symbol.startswith("68"):
        return 20.0  # 创业板/科创板
    elif symbol.startswith("8") or symbol.startswith("4"):
        return 30.0  # 北交所
    return 10.0  # 主板


def _log_anomaly(symbol: str, category: str, detail: str, severity: str):
    """记录异常到日志文件"""
    try:
        ANOMALY_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": str(date.today()),
            "symbol": symbol,
            "category": category,
            "detail": detail,
            "severity": severity,
        }
        with open(ANOMALY_LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def get_recent_anomalies(limit: int = 20) -> list[dict]:
    """获取最近异常记录"""
    if not ANOMALY_LOG.exists():
        return []
    try:
        lines = ANOMALY_LOG.read_text().strip().split("\n")
        return [json.loads(l) for l in lines[-limit:]]
    except Exception:
        return []


# ── CLI entrypoints used by scheduler (morning / afternoon / preflight / cross) ──

def preflight(db_path: str = None) -> dict:
    """Morning preflight: coverage + lag summary."""
    cov = check_daily_coverage(db_path)
    per = check_per_symbol_coverage(db_path)
    ok = bool(cov.get("ok")) and all(v.get("ok", True) for v in per.values())
    return {"ok": ok, "coverage": cov, "per_symbol": per}


def morning(db_path: str = None) -> dict:
    """Alias for preflight (scheduler morning job)."""
    return preflight(db_path)


def afternoon(db_path: str = None) -> dict:
    """Afternoon freshness re-check after A-share close."""
    return check_daily_coverage(db_path)


def cross(db_path: str = None, sample: int = 50) -> dict:
    """
    Cross-validate a sample of latest closes vs Sina.
    Quarantines symbols with >5% divergence.
    """
    from nous.data.quality.quarantine import quarantine_symbols

    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    latest = conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()[0]
    if not latest:
        conn.close()
        return {"ok": False, "reason": "no_daily_data"}

    rows = conn.execute(
        """SELECT symbol, close FROM stock_daily
           WHERE trade_date=? AND close>0
           ORDER BY amount DESC LIMIT ?""",
        (latest, sample),
    ).fetchall()

    errors, warnings, checked = [], [], 0
    try:
        from nous.data.collectors.sim_executor import fetch_sina_price
    except Exception as e:
        conn.close()
        return {"ok": False, "reason": f"sina_import_failed:{e}"}

    for sym, db_close in rows:
        try:
            sina_px, _ = fetch_sina_price(sym)
            if not sina_px:
                continue
            checked += 1
            cv = cross_validate_close(sym, float(db_close), float(sina_px))
            if cv.severity == "error":
                errors.append({"symbol": sym, "reason": cv.reason})
                quarantine_symbols(conn, [sym], reason=cv.reason, severity="error")
            elif cv.severity == "warning":
                warnings.append({"symbol": sym, "reason": cv.reason})
        except Exception:
            continue

    conn.close()
    return {
        "ok": len(errors) == 0,
        "date": latest,
        "checked": checked,
        "errors": errors,
        "warnings": warnings,
    }


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Nous data validators")
    parser.add_argument(
        "action",
        choices=["morning", "afternoon", "preflight", "cross"],
        help="validator job",
    )
    parser.add_argument("--sample", type=int, default=50)
    args = parser.parse_args()

    fn = {"morning": morning, "afternoon": afternoon, "preflight": preflight, "cross": cross}[
        args.action
    ]
    if args.action == "cross":
        result = fn(sample=args.sample)
    else:
        result = fn()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if result.get("ok", True) else 1)
