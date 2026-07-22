"""价值指标：PE/PB/ROE/股息率/负债率 — 含 K1 数据溯源"""
from typing import Optional

from nous.data import storage


def get_fundamentals(symbol: str) -> Optional[dict]:
    """获取单只股票的基本面快照，返回 dict 或 None"""
    conn = storage.get_db()
    row = conn.execute(
        "SELECT * FROM stock_fundamental WHERE symbol=?", (symbol,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def value_scores(symbol: str, cfg: dict) -> dict:
    """
    价值维度打分（0-10分），K1指标带数据溯源。
    
    返回新增字段:
      pe_provenance / pb_provenance / roe_provenance: "real" | "fallback"
      k1_ready: 所有K1指标(PE/PB/ROE)都有真实数据
      k1_missing: 缺失的K1指标名列表
    """
    fund = get_fundamentals(symbol)
    vcfg = cfg["value"]
    result = {
        "symbol": symbol,
        "pe": None, "pb": None, "roe": None,
        "dividend_yield": None, "debt_ratio": None, "total_mv": None,
        "score": None, "available": False,
        # ── K1 溯源字段 ──
        "pe_provenance": "missing",
        "pb_provenance": "missing",
        "roe_provenance": "missing",
        "k1_ready": False,
        "k1_missing": [],
    }

    used_fallback = False
    k1_missing = []

    if not fund:
        # 所有数据源都失败 → 市场智能中性值（标记为fallback）
        result["available"] = True
        used_fallback = True
        fallback = _market_fallback(symbol)
        result["pe"] = fallback["pe"]
        result["pb"] = fallback["pb"]
        result["roe"] = fallback["roe"]
        fund = fallback
        k1_missing = ["pe", "pb", "roe"]
    else:
        result["available"] = True

    result["pe"] = fund.get("pe")
    result["pb"] = fund.get("pb")
    result["roe"] = fund.get("roe")
    result["dividend_yield"] = fund.get("dividend_yield")
    result["debt_ratio"] = fund.get("debt_ratio")
    result["total_mv"] = fund.get("total_mv")

    # ── 判定每个K1指标的溯源 ──
    if not used_fallback:
        # 从DB命中：逐指标判断是否真实有值
        if fund.get("pe") is not None and fund.get("pe") > 0:
            result["pe_provenance"] = "real"
        else:
            result["pe_provenance"] = "fallback"
            k1_missing.append("pe")
        
        if fund.get("pb") is not None and fund.get("pb") > 0:
            result["pb_provenance"] = "real"
        else:
            result["pb_provenance"] = "fallback"
            k1_missing.append("pb")
        
        if fund.get("roe") is not None:
            result["roe_provenance"] = "real"
        else:
            result["roe_provenance"] = "fallback"
            k1_missing.append("roe")
    else:
        result["pe_provenance"] = "fallback"
        result["pb_provenance"] = "fallback"
        result["roe_provenance"] = "fallback"

    result["k1_missing"] = k1_missing
    result["k1_ready"] = len(k1_missing) == 0

    # ── 打分（逻辑不变）──
    score = 0
    count = 0

    pe = fund.get("pe")
    if pe and 0 < pe <= vcfg["pe_max"]:
        score += min(10, 10 * (1 - pe / vcfg["pe_max"]))
        count += 1

    pb = fund.get("pb")
    if pb and 0 < pb <= vcfg["pb_max"]:
        score += min(10, 10 * (1 - pb / vcfg["pb_max"]))
        count += 1

    roe = fund.get("roe")
    if roe and roe >= vcfg["roe_min"]:
        score += min(10, 2 * (roe - vcfg["roe_min"]))
        count += 1

    dy = fund.get("dividend_yield")
    if dy and dy >= vcfg["dividend_yield_min"]:
        score += min(10, 5 * dy)
        count += 1

    dr = fund.get("debt_ratio")
    if dr and dr <= vcfg["debt_ratio_max"]:
        score += min(10, 10 * (1 - dr / vcfg["debt_ratio_max"]))
        count += 1

    mv = fund.get("total_mv")
    if mv and mv >= vcfg["total_mv_min"]:
        score += min(5, mv / vcfg["total_mv_min"])
        count += 1

    result["score"] = round(score / count, 1) if count > 0 else 0
    return result


# ── 市场智能Fallback ──
_FALLBACK_CACHE = {
    "ts": 0,
    "hk_pe_median": None, "hk_pb_median": None,
    "a_pe_median": None, "a_pb_median": None,
}

def _market_fallback(symbol: str) -> dict:
    """
    根据市场返回合理的PE/PB/ROE中性值。
    港股: 从 stock_fundamental 取港股中位数（缓存1h）
    A股:  从 stock_fundamental 取A股中位数（缓存1h），
          若取不到则用固定值50/5/8兜底
    """
    if len(symbol) == 5 and symbol.isdigit():
        return _hk_median_fallback()
    return _a_median_fallback()

def _a_median_fallback() -> dict:
    """取 stock_fundamental 中所有A股PE/PB中位数，缓存1小时。
    比固定50/5/8能更准确反映当前市场估值水位。
    """
    import time as _time
    now = _time.monotonic()
    if _FALLBACK_CACHE["a_pe_median"] and (now - _FALLBACK_CACHE["ts"]) < 3600:
        return {
            "pe": _FALLBACK_CACHE["a_pe_median"],
            "pb": _FALLBACK_CACHE["a_pb_median"] or 2.0,
            "roe": 8,
        }
    
    try:
        from nous.data import storage
        conn = storage.get_db()
        # A股: 6位数字代码
        pe_count = conn.execute(
            "SELECT COUNT(*) FROM stock_fundamental "
            "WHERE length(symbol)=6 AND symbol GLOB '[0-9]*' AND pe > 0 AND pe < 1000"
        ).fetchone()[0]
        pb_count = conn.execute(
            "SELECT COUNT(*) FROM stock_fundamental "
            "WHERE length(symbol)=6 AND symbol GLOB '[0-9]*' AND pb > 0 AND pb < 50"
        ).fetchone()[0]
        
        if pe_count > 50:  # 至少50只才有统计意义
            pe_median = conn.execute(
                "SELECT pe FROM stock_fundamental "
                "WHERE length(symbol)=6 AND symbol GLOB '[0-9]*' AND pe > 0 AND pe < 1000 "
                "ORDER BY pe LIMIT 1 OFFSET ?", (pe_count // 2,)
            ).fetchone()
            if pe_median:
                _FALLBACK_CACHE["a_pe_median"] = round(float(pe_median[0]), 1)
        if pb_count > 50:
            pb_median = conn.execute(
                "SELECT pb FROM stock_fundamental "
                "WHERE length(symbol)=6 AND symbol GLOB '[0-9]*' AND pb > 0 AND pb < 50 "
                "ORDER BY pb LIMIT 1 OFFSET ?", (pb_count // 2,)
            ).fetchone()
            if pb_median:
                _FALLBACK_CACHE["a_pb_median"] = round(float(pb_median[0]), 2)
        conn.close()
    except Exception:
        pass
    
    # 更新缓存时间
    _FALLBACK_CACHE["ts"] = now
    
    return {
        "pe": _FALLBACK_CACHE["a_pe_median"] or 50.0,
        "pb": _FALLBACK_CACHE["a_pb_median"] or 5.0,
        "roe": 8,
    }

def _hk_median_fallback() -> dict:
    """取 stock_fundamental 中所有港股PE/PB中位数，缓存1小时"""
    import time as _time
    now = _time.monotonic()
    if _FALLBACK_CACHE["hk_pe_median"] and (now - _FALLBACK_CACHE["ts"]) < 3600:
        return {
            "pe": _FALLBACK_CACHE["hk_pe_median"],
            "pb": _FALLBACK_CACHE["hk_pb_median"] or 1.0,
            "roe": 8,
        }
    
    try:
        from nous.data import storage
        conn = storage.get_db()
        pe_count = conn.execute(
            "SELECT COUNT(*) FROM stock_fundamental "
            "WHERE length(symbol)=5 AND symbol GLOB '[0-9]*' AND pe > 0"
        ).fetchone()[0]
        pb_count = conn.execute(
            "SELECT COUNT(*) FROM stock_fundamental "
            "WHERE length(symbol)=5 AND symbol GLOB '[0-9]*' AND pb > 0"
        ).fetchone()[0]
        
        if pe_count > 0:
            pe_median = conn.execute(
                "SELECT pe FROM stock_fundamental "
                "WHERE length(symbol)=5 AND symbol GLOB '[0-9]*' AND pe > 0 "
                "ORDER BY pe LIMIT 1 OFFSET ?", (pe_count // 2,)
            ).fetchone()
            if pe_median:
                _FALLBACK_CACHE["hk_pe_median"] = round(float(pe_median[0]), 1)
        if pb_count > 0:
            pb_median = conn.execute(
                "SELECT pb FROM stock_fundamental "
                "WHERE length(symbol)=5 AND symbol GLOB '[0-9]*' AND pb > 0 "
                "ORDER BY pb LIMIT 1 OFFSET ?", (pb_count // 2,)
            ).fetchone()
            if pb_median:
                _FALLBACK_CACHE["hk_pb_median"] = round(float(pb_median[0]), 2)
        conn.close()
    except Exception:
        pass
    
    return {
        "pe": _FALLBACK_CACHE["hk_pe_median"] or 10.0,
        "pb": _FALLBACK_CACHE["hk_pb_median"] or 1.0,
        "roe": 8,
    }
