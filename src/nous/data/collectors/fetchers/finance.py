"""财务基本面：TTM PE（Sina财务自算）+ 静态PE兜底"""
import re
from typing import Optional

import akshare as ak
import pandas as pd

from nous.data import storage


def fetch_financial_abstract(symbol: str) -> Optional[dict]:
    """
    从 akshare stock_financial_abstract 获取核心财务指标。
    返回: {eps, bvps, roe} 或 None
    """
    try:
        df = ak.stock_financial_abstract(symbol)
    except Exception:
        return None

    result = {}

    # 每股收益 — 优先级：摊薄(最新股数) > 稀释 > 基本 > 每股收益
    eps_row = df[df["指标"] == "摊薄每股收益_最新股数"]
    if eps_row.empty:
        eps_row = df[df["指标"] == "稀释每股收益"]
    if eps_row.empty:
        eps_row = df[df["指标"] == "基本每股收益"]
    if eps_row.empty:
        eps_row = df[df["指标"] == "每股收益"]
    if eps_row.empty:
        eps_row = df[df["指标"].str.contains("每股收益", na=False)]
    if not eps_row.empty:
        latest_col = _latest_valid_col(eps_row)
        if latest_col:
            try:
                result["eps"] = float(eps_row[latest_col].iloc[0])
            except (ValueError, TypeError):
                pass

    # 每股净资产
    bv_row = df[df["指标"] == "每股净资产"]
    if not bv_row.empty:
        latest_col = _latest_valid_col(bv_row)
        if latest_col:
            try:
                result["bvps"] = float(bv_row[latest_col].iloc[0])
            except (ValueError, TypeError):
                pass

    # ROE
    roe_row = df[df["指标"] == "净资产收益率(ROE)"]
    if roe_row.empty:
        roe_row = df[df["指标"].str.contains("净资产收益率", na=False)]
    if not roe_row.empty:
        latest_col = _latest_valid_col(roe_row)
        if latest_col:
            try:
                result["roe"] = float(roe_row[latest_col].iloc[0])
            except (ValueError, TypeError):
                pass

    return result if result else None


def _latest_valid_col(df: pd.DataFrame, prefer_annual: bool = True) -> Optional[str]:
    """找到最新一列有数据的列名（日期列，如 20260331）。
    优先取年报列(末尾1231)，PE计算用年度EPS更准确。"""
    date_cols = [c for c in df.columns if re.match(r"^\d{8}$", str(c))]

    if prefer_annual:
        # 年报优先：找以1231结尾的最新有数据列
        annual_cols = [c for c in date_cols if str(c).endswith('1231')]
        for col in sorted(annual_cols, reverse=True):
            val = df[col].iloc[0]
            if pd.notna(val) and val != "--":
                return col

    # 回退：任意最新列
    for col in sorted(date_cols, reverse=True):
        val = df[col].iloc[0]
        if pd.notna(val) and val != "--":
            return col
    return None


def _compute_ttm_pe(symbol: str, close: float, fin: dict) -> Optional[float]:
    """
    用 Sina 财务摘要自算 TTM PE（不依赖东财 push2）。
    算法：TTM EPS = 最新年报EPS - 去年Q1 EPS + 今年Q1 EPS
    返回 TTM PE float 或 None。
    """
    # 需要再拉一次 stock_financial_abstract 取完整季度数据
    # 因为 fin 只取了最新年报列，没有 Q1 数据
    try:
        df = ak.stock_financial_abstract(symbol)
    except Exception:
        return None

    # EPS 优先级：摊薄(最新股数) > 稀释 > 基本 > 每股收益
    eps_row = df[df["指标"] == "摊薄每股收益_最新股数"]
    if eps_row.empty:
        eps_row = df[df["指标"] == "稀释每股收益"]
    if eps_row.empty:
        eps_row = df[df["指标"] == "基本每股收益"]
    if eps_row.empty:
        eps_row = df[df["指标"] == "每股收益"]
    if eps_row.empty:
        return None

    # 构建 日期→EPS 映射
    eps_map = {}
    for c in eps_row.columns:
        if re.match(r"^\d{8}$", str(c)):
            try:
                v = float(eps_row[c].iloc[0])
                eps_map[c] = v
            except (ValueError, TypeError):
                pass

    if not eps_map:
        return None

    # 找最新年报(1231结尾)
    annual_cols = sorted([c for c in eps_map if c.endswith('1231')], reverse=True)
    if not annual_cols:
        return None
    latest_annual = annual_cols[0]
    year = latest_annual[:4]
    q1_prev = f"{year}0331"
    q1_next = f"{int(year)+1}0331"

    annual_eps = eps_map.get(latest_annual)
    q1_prev_eps = eps_map.get(q1_prev)
    q1_next_eps = eps_map.get(q1_next)

    if annual_eps and q1_prev_eps and q1_next_eps and annual_eps > 0:
        ttm_eps = annual_eps - q1_prev_eps + q1_next_eps
        if ttm_eps > 0:
            return round(close / ttm_eps, 1)

    return None


def compute_pe_pb(symbol: str) -> dict:
    """
    获取 PE/PB，TTM PE 优先，静态PE兜底。
    返回: {pe, pe_static, pb, roe, eps, bvps, pe_type, available}
    """
    latest = storage.get_latest_date(symbol)
    if not latest:
        return _empty_result(symbol, "无日线数据")

    # 获取最新收盘价
    rows = storage.get_daily(symbol, limit=1)
    if not rows:
        return _empty_result(symbol, "无收盘价")

    # 获取财务数据
    fin = fetch_financial_abstract(symbol)
    if not fin:
        return _empty_result(symbol, "无财务摘要数据")
    close = rows[0]["close"]

    result = {"available": True, "eps": fin.get("eps"), "bvps": fin.get("bvps"),
              "roe": fin.get("roe"), "pe": None, "pe_static": None, "pe_dynamic": None,
              "pb": None, "pe_type": "none"}

    # --- TTM PE（优先）---
    ttm_pe = _compute_ttm_pe(symbol, close, fin)
    if ttm_pe is not None:
        result["pe"] = round(ttm_pe, 1)
        result["pe_type"] = "ttm"

    # --- 静态PE（兜底 + 参考）---
    if fin.get("eps") and fin["eps"] > 0:
        static_pe = round(close / fin["eps"], 1)
        result["pe_static"] = static_pe
        if result["pe"] is None:
            result["pe"] = static_pe
            result["pe_type"] = "static"

    # --- PB ---
    if fin.get("bvps") and fin["bvps"] > 0:
        result["pb"] = round(close / fin["bvps"], 2)

    # --- 动态PE（季度年化，仅参考）---
    try:
        result["pe_dynamic"] = _compute_dynamic_pe(symbol, close)
    except Exception:
        result["pe_dynamic"] = None

    return result


def _compute_dynamic_pe(symbol: str, close: float) -> Optional[float]:
    """动态PE = 股价 / (最新季度EPS × 4)"""
    import re
    try:
        df = ak.stock_financial_abstract(symbol)
    except Exception:
        return None

    eps_row = df[df["指标"] == "摊薄每股收益_最新股数"]
    if eps_row.empty:
        eps_row = df[df["指标"] == "稀释每股收益"]
    if eps_row.empty:
        eps_row = df[df["指标"] == "基本每股收益"]
    if eps_row.empty:
        return None

    date_cols = sorted([c for c in eps_row.columns if re.match(r"^\d{8}$", str(c))], reverse=True)
    if not date_cols:
        return None
    latest = date_cols[0]
    try:
        eps_q = float(eps_row[latest].iloc[0])
    except (ValueError, TypeError):
        return None

    if eps_q > 0:
        return round(close / (eps_q * 4), 1)
    return None


def sync_fundamentals(symbol: str):
    """同步单只股票的基本面到 SQLite"""
    data = compute_pe_pb(symbol)
    if not data["available"]:
        return

    from datetime import date
    storage.upsert_fundamentals([{
        "symbol": symbol,
        "pe": data.get("pe"),
        "pe_static": data.get("pe_static"),
        "pe_dynamic": data.get("pe_dynamic"),
        "pb": data.get("pb"),
        "roe": data.get("roe"),
        "dividend_yield": None,
        "debt_ratio": None,
        "total_mv": None,
        "snapshot_date": str(date.today()),
    }])


def _empty_result(symbol: str = "", reason: str = "") -> dict:
    """返回空结果并记录失败日志"""
    if symbol and reason:
        try:
            from nous.data.quality.validators import _log_anomaly
            _log_anomaly(symbol, "pe_compute_failed", reason, "warning")
        except ImportError:
            pass
    return {"available": False, "pe": None, "pe_static": None, "pe_dynamic": None, "pb": None,
            "roe": None, "eps": None, "bvps": None, "pe_type": "none"}
