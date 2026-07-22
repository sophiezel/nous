#!/usr/bin/env python3
"""
机构调研数据采集器
数据源: akshare stock_jgdy_tj_em(date='YYYYMMDD')
写入 institution_research 表 (去重)
提取指标: 调研家数突增 / 月度热度TOP20
"""

import sys, os
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from nous.data.collectors import resilient_fetch, CircuitBreaker, heartbeat
from nous.data.storage import get_db
from typing import Optional

PROCESS_NAME = "institution_tracker"
DB_PATH = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"

DDL = """
CREATE TABLE IF NOT EXISTS institution_research (
    announce_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    close_price REAL,
    pct_change REAL,
    institution_count INTEGER,
    research_type TEXT,
    research_date TEXT,
    PRIMARY KEY (announce_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_ir_symbol ON institution_research(symbol);
CREATE INDEX IF NOT EXISTS idx_ir_date ON institution_research(announce_date);
"""


def yesterday_str() -> str:
    return (date.today() - timedelta(days=1)).strftime("%Y%m%d")


def ensure_schema():
    conn = get_db(write=True)
    try:
        conn.executescript(DDL)
        conn.commit()
    finally:
        conn.close()


def fetch_research(date_str: str) -> list[dict]:
    """采集单日机构调研数据"""
    import akshare as ak

    def _fetch():
        df = ak.stock_jgdy_tj_em()  # 无参数=全量, 返回所有历史
        if df is None or len(df) == 0:
            return []
        # 过滤公告日期为指定日期的记录
        target = date_str[:4] + "-" + date_str[4:6] + "-" + date_str[6:8]
        df = df[df["公告日期"].astype(str).str.startswith(target)]
        if len(df) == 0:
            return []
        records = []
        for _, r in df.iterrows():
            records.append({
                "announce_date": target,
                "symbol": str(r.get("代码", "")),
                "name": str(r.get("名称", "")),
                "close_price": _safe_float(r.get("最新价")),
                "pct_change": _safe_float(r.get("涨跌幅")),
                "institution_count": _safe_int(r.get("接待机构数量")),
                "research_type": str(r.get("接待方式", "")),
                "research_date": _safe_date(r.get("接待日期")),
            })
        return records

    result, status = resilient_fetch("akshare", _fetch, fallback_fn=lambda: [])
    if status.get("retries", 0) > 0:
        print(f"  [institution_tracker] {status['retries']} retries", file=sys.stderr)
    return result if result else []


def save_records(records: list[dict]) -> int:
    """写入数据库, 返回写入行数"""
    if not records:
        return 0
    conn = get_db(write=True)
    sql = """
        INSERT OR IGNORE INTO institution_research
        (announce_date, symbol, name, close_price, pct_change,
         institution_count, research_type, research_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    count = 0
    try:
        for r in records:
            try:
                conn.execute(sql, (
                    r["announce_date"], r["symbol"], r["name"],
                    r["close_price"], r["pct_change"],
                    r["institution_count"], r["research_type"],
                    r["research_date"],
                ))
                count += 1
            except Exception:
                pass
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return count


def _safe_float(val) -> Optional[float]:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> Optional[int]:
    try:
        return int(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _safe_date(val) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    if len(s) >= 10:
        return s[:10]
    return s


def main():
    ensure_schema()
    heartbeat(PROCESS_NAME)

    date_str = yesterday_str()
    print(f"[{PROCESS_NAME}] {date.today().isoformat()} fetching {date_str} ...")

    records = fetch_research(date_str)
    print(f"  fetched {len(records)} research records")

    if records:
        saved = save_records(records)
        print(f"  saved {saved} (new)")

        # 统计亮点
        hot = sorted(records, key=lambda r: r.get("institution_count", 0) or 0, reverse=True)
        if hot and hot[0].get("institution_count", 0):
            top3 = [f"{h['name']}({h['institution_count']}家)" for h in hot[:3]]
            print(f"  🔥 机构调研 TOP3: {', '.join(top3)}")

    heartbeat(PROCESS_NAME)
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
