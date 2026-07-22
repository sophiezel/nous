"""Unified data collectors — one function per data source.

Each collector:
  - Has a run() function callable from CLI or scheduler
  - Returns (status, count, message)
  - Auto-creates tables via SCHEMA from nous.data.storage
  - Uses akshare for most sources, Sina for equities

Usage:
    from nous.data.collectors.unified import collect_all
    collect_all()
"""

from __future__ import annotations

import sys
import time
import logging
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger("nous.collectors")

# ── Helpers ─────────────────────────────────────────────────────────────

def _get_conn(write=True):
    from nous.data.storage import get_db
    return get_db(write=write)

def _latest_date(conn, table, col="trade_date"):
    try:
        r = conn.execute(f"SELECT MAX({col}) FROM [{table}]").fetchone()
        return r[0] if r and r[0] else None
    except Exception:
        return None


def _result(status: str, count: int, msg: str = "") -> dict:
    return {"status": status, "count": count, "message": msg}


def _clear_proxies() -> None:
    """Drop proxy env vars — broken local proxies break Eastmoney/akshare."""
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            del os.environ[k]


def _em_get(url: str, timeout: int = 30):
    """HTTP GET via curl_cffi (Chrome TLS) — bypasses broken system proxies."""
    _clear_proxies()
    from curl_cffi import requests as creq
    return creq.get(url, impersonate="chrome131", timeout=timeout, proxies={})


def _fetch_a_spot_em() -> list[dict]:
    """Full A-share spot snapshot from Eastmoney (PE/PB/MV + pct)."""
    fields = "f12,f14,f2,f3,f9,f23,f20"
    fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
    # push2delay is more stable overnight; 82.push2 often resets connections.
    hosts = (
        "https://push2delay.eastmoney.com",
        "https://push2.eastmoney.com",
        "https://82.push2.eastmoney.com",
    )
    last_err: Exception | None = None
    for host in hosts:
        base = (
            f"{host}/api/qt/clist/get"
            f"?pz=100&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
            f"&fltt=2&invt=2&fid=f12&fs={fs}&fields={fields}&pn={{pn}}"
        )
        rows: list[dict] = []
        total = None
        pn = 1
        try:
            while True:
                r = _em_get(base.format(pn=pn))
                data = (r.json() or {}).get("data") or {}
                if total is None:
                    total = int(data.get("total") or 0)
                batch = data.get("diff") or []
                if not batch:
                    break
                rows.extend(batch)
                if total and len(rows) >= total:
                    break
                pn += 1
                if pn > 200:  # safety
                    break
            if rows:
                return rows
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    return []


def _last_trade_date(conn) -> str:
    """Latest stock_daily trade date (completed session proxy)."""
    d = _latest_date(conn, "stock_daily", "trade_date")
    return d or date.today().isoformat()


# ═══════════════════════════════════════════════════════════════════════
# 1. Stock Daily (A+H) — already working
# ═══════════════════════════════════════════════════════════════════════

def collect_stock_daily(market: str = "a", days: int = 1) -> dict:
    """Update stock daily prices via Sina API (fast, batch).

    This is the core price data source for all ML models.
    """
    import akshare as ak
    conn = _get_conn(write=True)
    t0 = time.time()
    count = 0

    try:
        symbols = conn.execute(
            "SELECT symbol FROM stock_basic WHERE market=?", (market,)
        ).fetchall()
        symbols = [r[0] for r in symbols]

        today = date.today().isoformat()
        for symbol in symbols:
            try:
                df = ak.stock_zh_a_daily(symbol=symbol, adjust="qfq")
                if df is None or df.empty:
                    continue
                df = df.tail(days)
                for _, row in df.iterrows():
                    conn.execute(
                        """INSERT OR REPLACE INTO stock_daily(symbol,trade_date,open,high,low,close,volume,amount)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (symbol, str(row["date"])[:10], row["open"], row["high"],
                         row["low"], row["close"], row["volume"], row.get("amount", 0)),
                    )
                    count += 1
            except Exception:
                continue
            if count % 100 == 0:
                conn.commit()

        conn.commit()
        elapsed = time.time() - t0
        return _result("ok", count, f"{count} rows in {elapsed:.1f}s")
    except Exception as e:
        return _result("error", count, str(e))
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# 2. Stock Fundamentals (PE/PB/ROE) — akshare
# ═══════════════════════════════════════════════════════════════════════

def collect_fundamentals() -> dict:
    """Collect PE/PB/market_cap for all A-share stocks (one EM spot pull)."""
    conn = _get_conn(write=True)
    t0 = time.time()
    count = 0

    try:
        as_of = _last_trade_date(conn)
        rows = _fetch_a_spot_em()
        if not rows:
            return _result("error", 0, "EM spot empty")

        def _num(v):
            try:
                if v is None or v in ("-", ""):
                    return None
                return float(v)
            except (TypeError, ValueError):
                return None

        for row in rows:
            symbol = str(row.get("f12") or "").zfill(6)
            if not symbol or len(symbol) > 6:
                # keep BJ 920xxx etc as-is when already 6
                symbol = str(row.get("f12") or "")
            pe = _num(row.get("f9"))
            pb = _num(row.get("f23"))
            mv = _num(row.get("f20"))
            conn.execute(
                """INSERT OR REPLACE INTO stock_fundamental
                   (symbol,pe,pb,total_mv,pe_dynamic,snapshot_date,updated_at)
                   VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                (symbol, pe, pb, mv, pe, as_of),
            )
            count += 1
            if count % 1000 == 0:
                conn.commit()
        conn.commit()
        return _result("ok", count, f"{count} stocks as_of={as_of} in {time.time()-t0:.1f}s")
    except Exception as e:
        return _result("error", count, str(e))
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# 3. Index Daily
# ═══════════════════════════════════════════════════════════════════════

INDEX_CODES = {
    "000001": "上证指数", "399001": "深证成指", "399006": "创业板指",
    "000688": "科创50", "000300": "沪深300", "000905": "中证500",
    "000852": "中证1000", "399673": "创业板50", "000016": "上证50",
}

def collect_index_daily() -> dict:
    """Collect A-share index daily data via akshare → index_daily (IDX_ prefix)."""
    import akshare as ak
    _clear_proxies()
    conn = _get_conn(write=True)
    t0 = time.time()
    count = 0

    try:
        for code, name in INDEX_CODES.items():
            try:
                sina = f"sh{code}" if code.startswith("0") else f"sz{code}"
                df = ak.stock_zh_index_daily(symbol=sina)
                if df is None or df.empty:
                    continue
                sym = f"IDX_{code}"
                for _, row in df.tail(60).iterrows():
                    dt = str(row["date"])[:10]
                    conn.execute(
                        """INSERT OR REPLACE INTO index_daily
                           (symbol,trade_date,open,high,low,close,volume,amount)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (sym, dt, row["open"], row["high"], row["low"],
                         row["close"], row["volume"], row.get("amount", 0)),
                    )
                    count += 1
            except Exception:
                continue
        conn.commit()
        mx = _latest_date(conn, "index_daily")
        return _result("ok", count, f"{count} rows, max={mx} in {time.time()-t0:.1f}s")
    except Exception as e:
        return _result("error", count, str(e))
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# 4. Margin / 融资融券
# ═══════════════════════════════════════════════════════════════════════

def collect_margin() -> dict:
    """Collect market-level 融资融券 (SH+SZ macro series → margin_daily)."""
    import akshare as ak
    import pandas as pd

    _clear_proxies()
    conn = _get_conn(write=True)
    t0 = time.time()
    count = 0

    try:
        sh = ak.macro_china_market_margin_sh()
        sz = ak.macro_china_market_margin_sz()
        if sh is None or sh.empty or sz is None or sz.empty:
            return _result("error", 0, "macro margin empty")

        def _norm(df):
            out = df.rename(columns={
                "日期": "trade_date",
                "融资余额": "margin_balance",
                "融资买入额": "margin_buy",
                "融券余额": "short_balance",
                "融券余量": "short_sell_volume",
                "融券卖出量": "short_value",
                "融资融券余额": "total_balance",
            }).copy()
            out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.strftime("%Y-%m-%d")
            return out

        sh_n, sz_n = _norm(sh), _norm(sz)
        merged = sh_n.merge(sz_n, on="trade_date", suffixes=("_sh", "_sz"), how="outer")
        since = _latest_date(conn, "margin_daily") or "2026-01-01"
        # refresh last ~40 sessions for safety
        for _, row in merged.iterrows():
            td = row["trade_date"]
            if td < since and td < (date.today() - timedelta(days=60)).isoformat():
                continue
            mb = (row.get("margin_balance_sh") or 0) + (row.get("margin_balance_sz") or 0)
            buy = (row.get("margin_buy_sh") or 0) + (row.get("margin_buy_sz") or 0)
            sb = (row.get("short_balance_sh") or 0) + (row.get("short_balance_sz") or 0)
            sv = (row.get("short_sell_volume_sh") or 0) + (row.get("short_sell_volume_sz") or 0)
            ssell = (row.get("short_value_sh") or 0) + (row.get("short_value_sz") or 0)
            tot = (row.get("total_balance_sh") or 0) + (row.get("total_balance_sz") or 0)
            if not mb and not tot:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO margin_daily
                   (trade_date,margin_balance,margin_buy,short_sell_volume,short_balance,short_value,total_balance)
                   VALUES(?,?,?,?,?,?,?)""",
                (td, float(mb or 0), float(buy or 0), float(sv or 0),
                 float(sb or 0), float(ssell or 0), float(tot or 0)),
            )
            count += 1
        conn.commit()
        mx = _latest_date(conn, "margin_daily")
        return _result("ok", count, f"{count} rows, max={mx} in {time.time()-t0:.1f}s")
    except Exception as e:
        return _result("error", count, str(e))
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# 5. HSGT / 沪深港通
# ═══════════════════════════════════════════════════════════════════════

def collect_hsgt() -> dict:
    """Collect 沪深港通 northbound/southbound flow data."""
    import akshare as ak
    conn = _get_conn(write=True)
    t0 = time.time()
    count = 0

    try:
        # Northbound + Southbound daily flow
        df_n = ak.stock_hsgt_hist_em(symbol="北向资金")
        if df_n is not None and not df_n.empty:
            for _, row in df_n.tail(5).iterrows():
                net = row.get("净买入", row.get("当日成交净买额", 0))
                conn.execute(
                    "INSERT OR REPLACE INTO hsgt_market_daily(trade_date,direction,net_flow) VALUES(?,?,?)",
                    (str(row["日期"])[:10], "北向", net if net and str(net) != "nan" else 0),
                )
                count += 1

        df_s = ak.stock_hsgt_hist_em(symbol="南向资金")
        if df_s is not None and not df_s.empty:
            for _, row in df_s.tail(5).iterrows():
                net = row.get("净买入", row.get("当日成交净买额", 0))
                conn.execute(
                    "INSERT OR REPLACE INTO hsgt_market_daily(trade_date,direction,net_flow) VALUES(?,?,?)",
                    (str(row["日期"])[:10], "南向", net if net and str(net) != "nan" else 0),
                )
                count += 1

        conn.commit()
        return _result("ok", count, f"{count} rows in {time.time()-t0:.1f}s")
    except Exception as e:
        return _result("error", count, str(e))
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# 6. LHB / 龙虎榜
# ═══════════════════════════════════════════════════════════════════════

def collect_lhb() -> dict:
    """Collect 龙虎榜 daily data."""
    import akshare as ak
    conn = _get_conn(write=True)
    t0 = time.time()
    count = 0

    try:
        today_str = date.today().strftime("%Y%m%d")
        try:
            df = ak.stock_lhb_detail_em(start_date=today_str, end_date=today_str)
        except (TypeError, AttributeError):
            return _result("skip", 0, f"今日({today_str})非交易日,无龙虎榜数据")
        if df is None or df.empty:
            return _result("skip", 0, f"今日({today_str})无龙虎榜数据(非交易日)")
        for _, row in df.iterrows():
            conn.execute(
                """INSERT OR REPLACE INTO lhb_daily(trade_date,symbol,name,reason,buy_amount,sell_amount,net_amount)
                   VALUES(?,?,?,?,?,?,?)""",
                (date.today().isoformat(), str(row.get("代码", "")), row.get("名称", ""),
                 row.get("上榜原因", ""), row.get("买入金额", 0), row.get("卖出金额", 0),
                 row.get("净买额", 0)),
            )
            count += 1
        conn.commit()
        return _result("ok", count, f"{count} rows in {time.time()-t0:.1f}s")
    except Exception as e:
        return _result("error", count, str(e))
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# 7. Fund Flow / 资金流向
# ═══════════════════════════════════════════════════════════════════════

def collect_fund_flow() -> dict:
    """Collect individual stock fund flow data."""
    import akshare as ak
    conn = _get_conn(write=True)
    t0 = time.time()
    count = 0

    try:
        df = ak.stock_individual_fund_flow_rank(indicator="今日")
        if df is not None and not df.empty:
            today = date.today().isoformat()
            for _, row in df.iterrows():
                conn.execute(
                    """INSERT OR REPLACE INTO fund_flow_stock(symbol,name,trade_date,main_net_inflow,super_large_net,large_net,medium_net,small_net)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (row.get("代码"), row.get("名称"), today,
                     row.get("主力净流入"), row.get("超大单净流入"), row.get("大单净流入"),
                     row.get("中单净流入"), row.get("小单净流入")),
                )
                count += 1
        conn.commit()
        return _result("ok", count, f"{count} rows in {time.time()-t0:.1f}s")
    except Exception as e:
        return _result("error", count, str(e))
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# 8. ETF Flow
# ═══════════════════════════════════════════════════════════════════════

def collect_etf_flow() -> dict:
    """Collect ETF fund flow data."""
    import akshare as ak
    conn = _get_conn(write=True)
    t0 = time.time()
    count = 0

    try:
        df = ak.fund_etf_fund_info_em()
        if df is not None and not df.empty:
            today = date.today().isoformat()
            for _, row in df.iterrows():
                conn.execute(
                    "INSERT OR REPLACE INTO etf_flow_daily(symbol,name,trade_date,fund_size,net_value) VALUES(?,?,?,?,?)",
                    (row.get("基金代码"), row.get("基金简称"), today,
                     row.get("基金规模"), row.get("单位净值")),
                )
                count += 1
        conn.commit()
        return _result("ok", count, f"{count} rows in {time.time()-t0:.1f}s")
    except Exception as e:
        return _result("error", count, str(e))
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# 9. Futures / 期货
# ═══════════════════════════════════════════════════════════════════════

FUTURES_SYMBOLS = ["IF", "IC", "IM", "IH", "T", "TF", "TS"]

def collect_futures() -> dict:
    """Collect futures daily data (IF/IC/IM/IH/bond futures)."""
    import akshare as ak
    conn = _get_conn(write=True)
    t0 = time.time()
    count = 0

    try:
        for sym in FUTURES_SYMBOLS:
            try:
                df = ak.futures_main_sina(symbol=sym)
                if df is not None and not df.empty:
                    for _, row in df.tail(5).iterrows():
                        dt = str(row["date"])[:10]
                        conn.execute(
                            "INSERT OR REPLACE INTO futures_daily(symbol,trade_date,open,high,low,close,volume,open_interest) VALUES(?,?,?,?,?,?,?,?)",
                            (sym, dt, row["open"], row["high"], row["low"], row["close"], row["volume"], row.get("hold", 0)),
                        )
                        count += 1
            except Exception:
                continue
        conn.commit()
        return _result("ok", count, f"{count} rows in {time.time()-t0:.1f}s")
    except Exception as e:
        return _result("error", count, str(e))
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# 10. Sentiment / 市场情绪
# ═══════════════════════════════════════════════════════════════════════

def collect_sentiment() -> dict:
    """Compute market sentiment from stock_daily into sentiment_cache schema."""
    import json

    conn = _get_conn(write=True)
    t0 = time.time()
    count = 0

    def _limit_pct(code: str) -> float:
        if not code:
            return 10.0
        if code.startswith(("8", "4", "92")):
            return 30.0
        if code.startswith("688"):
            return 20.0
        if code.startswith(("300", "301")):
            return 20.0
        return 10.0

    try:
        # Backfill missing dates since last cached (or last 40 sessions)
        last = _latest_date(conn, "sentiment_cache", "date") or "2026-05-01"
        dates = [r[0] for r in conn.execute(
            """SELECT DISTINCT trade_date FROM stock_daily
               WHERE trade_date > ? ORDER BY trade_date""",
            (last,),
        ).fetchall()]
        if not dates:
            # refresh latest trade day anyway
            latest = _last_trade_date(conn)
            dates = [latest]

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for trade_date in dates:
            prev = conn.execute(
                "SELECT MAX(trade_date) FROM stock_daily WHERE trade_date < ?",
                (trade_date,),
            ).fetchone()[0]
            today_rows = conn.execute(
                "SELECT symbol, open, high, close FROM stock_daily WHERE trade_date=?",
                (trade_date,),
            ).fetchall()
            if len(today_rows) < 100:
                continue
            prev_map = {}
            if prev:
                prev_map = {
                    r[0]: r[1]
                    for r in conn.execute(
                        "SELECT symbol, close FROM stock_daily WHERE trade_date=?",
                        (prev,),
                    ).fetchall()
                    if r[1] is not None
                }
            limit_up = limit_down = up = down = 0
            for symbol, open_p, high_p, close_p in today_rows:
                if close_p is None:
                    continue
                pc = prev_map.get(symbol)
                if not pc or pc <= 0:
                    continue
                pct = (close_p / pc - 1.0) * 100.0
                lim = _limit_pct(str(symbol))
                if pct >= lim * 0.99:
                    limit_up += 1
                if pct <= -lim * 0.99:
                    limit_down += 1
                if pct > 0:
                    up += 1
                elif pct < 0:
                    down += 1
            total = max(up + down, 1)
            rate = limit_up / total * 100.0
            # simple score: more limit-ups / advance → higher
            score = int(max(0, min(100, 50 + (up - down) / total * 25 + limit_up * 0.3 - limit_down * 0.3)))
            details = {
                "limit_up": limit_up,
                "limit_down": limit_down,
                "up": up,
                "down": down,
                "total": len(today_rows),
                "prev_date": prev,
            }
            conn.execute(
                """INSERT OR REPLACE INTO sentiment_cache
                   (date,score,limit_up_count,limit_up_rate,details,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (trade_date, score, limit_up, rate, json.dumps(details, ensure_ascii=False), now),
            )
            count += 1
        conn.commit()
        mx = _latest_date(conn, "sentiment_cache", "date")
        return _result("ok", count, f"{count} days, max={mx} in {time.time()-t0:.1f}s")
    except Exception as e:
        return _result("error", count, str(e))
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# 11. Industry / 行业分类
# ═══════════════════════════════════════════════════════════════════════

def collect_industry() -> dict:
    """Collect Shenwan industry classification for all A-share stocks."""
    import akshare as ak
    conn = _get_conn(write=True)
    t0 = time.time()
    count = 0

    try:
        df = ak.stock_board_industry_name_em()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                conn.execute(
                    "INSERT OR REPLACE INTO stock_industry(symbol,industry_l1,industry_l2,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP)",
                    (row.get("代码"), row.get("板块名称"), "",),
                )
                count += 1
        conn.commit()
        return _result("ok", count, f"{count} stocks in {time.time()-t0:.1f}s")
    except Exception as e:
        return _result("error", count, str(e))
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# 12. Global Indices
# ═══════════════════════════════════════════════════════════════════════

# Eastmoney secid map → store symbol matching historical index_global_daily
GLOBAL_INDEX_SECIDS = {
    "SPX": ("100.SPX", "标普500"),
    "NDX": ("100.NDX", "纳斯达克"),
    "DJI": ("100.DJIA", "道琼斯"),
    "N225": ("100.N225", "日经225"),
    "FTSE": ("100.FTSE", "富时100"),
    "GDAXI": ("100.GDAXI", "德国DAX"),
    "HSI": ("100.HSI", "恒生指数"),
}

def collect_global_index() -> dict:
    """Collect global index daily bars via Eastmoney kline (curl_cffi)."""
    conn = _get_conn(write=True)
    t0 = time.time()
    count = 0

    try:
        for symbol, (secid, name) in GLOBAL_INDEX_SECIDS.items():
            try:
                url = (
                    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                    f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
                    "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
                    "&klt=101&fqt=1&end=20500101&lmt=40"
                )
                data = (_em_get(url).json() or {}).get("data") or {}
                klines = data.get("klines") or []
                for line in klines:
                    parts = str(line).split(",")
                    if len(parts) < 6:
                        continue
                    dt, open_, close, high, low, vol = (
                        parts[0][:10], parts[1], parts[2], parts[3], parts[4], parts[5]
                    )
                    conn.execute(
                        """INSERT OR REPLACE INTO index_global_daily
                           (trade_date,symbol,close,open,high,low,volume)
                           VALUES(?,?,?,?,?,?,?)""",
                        (dt, symbol, float(close), float(open_), float(high),
                         float(low), float(vol or 0)),
                    )
                    count += 1
            except Exception:
                continue
        conn.commit()
        mx = _latest_date(conn, "index_global_daily")
        return _result("ok", count, f"{count} rows, max={mx} in {time.time()-t0:.1f}s")
    except Exception as e:
        return _result("error", count, str(e))
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# 13. Block Trade / 大宗交易
# ═══════════════════════════════════════════════════════════════════════

def collect_block_trade() -> dict:
    """Collect block trade data."""
    import akshare as ak
    conn = _get_conn(write=True)
    t0 = time.time()
    count = 0

    try:
        df = ak.stock_dzjy_mrmx(symbol="A股", start_date=date.today().strftime("%Y%m%d"), end_date=date.today().strftime("%Y%m%d"))
        if df is not None and not df.empty:
            today = date.today().isoformat()
            for _, row in df.iterrows():
                conn.execute(
                    """INSERT OR REPLACE INTO block_trades(trade_date,symbol,name,price,volume,amount, buyer, seller)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (today, row.get("证券代码"), row.get("证券简称"), row.get("成交价"),
                     row.get("成交量"), row.get("成交额"), row.get("买方营业部"), row.get("卖方营业部")),
                )
                count += 1
        conn.commit()
        return _result("ok", count, f"{count} rows in {time.time()-t0:.1f}s")
    except Exception as e:
        return _result("error", count, str(e))
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
# Master collector
# ═══════════════════════════════════════════════════════════════════════

ALL_COLLECTORS = {
    "daily":        ("日线行情", collect_stock_daily),
    "fundamental":  ("基本面(PE/PB)", collect_fundamentals),
    "index":        ("A股指数", collect_index_daily),
    "global-index": ("全球指数", collect_global_index),
    "margin":       ("融资融券", collect_margin),
    "hsgt":         ("沪深港通", collect_hsgt),
    "lhb":          ("龙虎榜", collect_lhb),
    "fund-flow":    ("资金流向", collect_fund_flow),
    "etf-flow":     ("ETF资金流", collect_etf_flow),
    "futures":      ("期货", collect_futures),
    "sentiment":    ("市场情绪", collect_sentiment),
    "industry":     ("行业分类", collect_industry),
    "block-trade":  ("大宗交易", collect_block_trade),
}


def collect_all(sources: list[str] | None = None) -> dict[str, dict]:
    """Run all (or specified) collectors. Returns {source_name: result}."""
    results = {}
    targets = {k: v for k, v in ALL_COLLECTORS.items() if sources is None or k in sources}

    for name, (label, func) in targets.items():
        print(f"  [{name}] {label}...", end=" ", flush=True)
        try:
            r = func()
            results[name] = r
            print(f"{r['status']}: {r['count']} ({r.get('message','')[:60]})")
        except Exception as e:
            results[name] = _result("error", 0, str(e))
            print(f"ERROR: {e}")

    # Summary
    ok = sum(1 for r in results.values() if r["status"] == "ok")
    print(f"\n  {ok}/{len(results)} collectors OK")
    return results
