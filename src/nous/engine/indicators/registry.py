"""指标注册表 — 所有指标的元数据定义

每个指标包含: tier(时间维度), criticality(关键性), sources(数据源优先级),
unit(单位), validation_threshold(交叉验证告警阈值), fallback(兜底值)
"""

from __future__ import annotations

INDICATORS = {
    # ── T0 实时 ──────────────────────────────
    "current_price": {
        "tier": "T0",
        "criticality": "K1",
        "description": "实时当前价",
        "sources": [
            {"name": "sina_real", "fn": "sina_real_quote"},
            {"name": "push2_quote", "fn": "push2_quote", "fallback": True},
        ],
        "sources_hk": [
            {"name": "sina_rt_hk", "fn": "sina_rt_hk_quote"},
        ],
        "unit": "元",
        "validation_threshold": 0.01,
        "fallback_value": None,
    },

    # ── T1 日级 ──────────────────────────────
    "close": {
        "tier": "T1",
        "criticality": "K1",
        "description": "收盘价",
        "sources": [
            {"name": "daily_db", "fn": "daily_db_close"},
            {"name": "sina_real", "fn": "sina_real_close", "after_hours": True},
            {"name": "push2_quote", "fn": "push2_close", "fallback": True},
        ],
        "unit": "元",
        "validation_threshold": 0.01,
        "require_freshness": {"a": "15:00", "hk": "16:10"},
        "fallback_value": None,
    },

    "pe_ttm": {
        "tier": "T1",
        "criticality": "K1",
        "description": "TTM市盈率（滚动四个季度）",
        "sources": [
            {"name": "sina_finance_compute", "fn": "compute_ttm_pe"},
            {"name": "push2_f164", "fn": "push2_ttm_pe", "fallback": True},
            {"name": "static_pe", "fn": "compute_static_pe", "fallback": True},
        ],
        "unit": "倍",
        "validation_threshold": 0.15,  # PE源间差异可达15%
        "fallback_value": 50,  # 大盘中位PE
    },

    "pe_static": {
        "tier": "T1",
        "criticality": "K2",
        "description": "静态市盈率（最新年报）",
        "sources": [
            {"name": "sina_finance_compute", "fn": "compute_static_pe"},
        ],
        "unit": "倍",
        "fallback_value": 50,
    },

    "pe_dynamic": {
        "tier": "T1",
        "criticality": "K2",
        "description": "动态市盈率（季度年化）",
        "sources": [
            {"name": "sina_finance_compute", "fn": "compute_dynamic_pe"},
        ],
        "unit": "倍",
        "note": "不进评分公式，仅展示",
    },

    "pb": {
        "tier": "T1",
        "criticality": "K1",
        "description": "市净率",
        "sources": [
            {"name": "sina_finance_compute", "fn": "compute_pb"},
            {"name": "push2_f167", "fn": "push2_pb", "fallback": True},
        ],
        "unit": "倍",
        "validation_threshold": 0.10,
        "fallback_value": 5,
    },

    "roe": {
        "tier": "T2",  # 季度更新
        "criticality": "K1",
        "description": "净资产收益率",
        "sources": [
            {"name": "sina_finance", "fn": "fetch_roe"},
        ],
        "unit": "%",
        "validation_threshold": 0.05,
        "fallback_value": 8,
    },

    "index_close": {
        "tier": "T1",
        "criticality": "K1",
        "description": "指数收盘价",
        "sources": [
            {"name": "sina_index", "fn": "sina_index_quote"},
            {"name": "push2_index", "fn": "push2_index_quote", "fallback": True},
        ],
        "unit": "点",
        "validation_threshold": 0.01,
        "require_freshness": {"a": "15:00", "hk": "16:10"},
    },

    "volume": {
        "tier": "T1",
        "criticality": "K1",
        "description": "成交量",
        "sources": [
            {"name": "sina_real", "fn": "sina_real_volume"},
            {"name": "daily_db", "fn": "daily_db_volume"},
        ],
        "unit": "股",
        "validation_threshold": 0.20,  # 不同源统计口径差异大
    },

    "change_pct": {
        "tier": "T1",
        "criticality": "K1",
        "description": "涨跌幅",
        "sources": [
            {"name": "sina_real", "fn": "sina_real_change"},
            {"name": "daily_db", "fn": "daily_db_change"},
        ],
        "unit": "%",
        "validation_threshold": 0.05,
    },

    "total_mv": {
        "tier": "T1",
        "criticality": "K2",
        "description": "总市值",
        "sources": [
            {"name": "compute", "fn": "compute_total_mv"},
        ],
        "unit": "亿元",
    },

    # ── T1 资金面 ────────────────────────────
    "margin_balance": {
        "tier": "T1",
        "criticality": "K1",
        "description": "融资余额（上交所）",
        "sources": [
            {"name": "akshare_margin", "fn": "fetch_margin_sh"},
        ],
        "unit": "亿元",
        "note": "T+1 早9点发布",
    },

    "sector_flow": {
        "tier": "T1",
        "criticality": "K1",
        "description": "板块资金流向",
        "sources": [
            {"name": "akshare_sector_flow", "fn": "fetch_sector_flow"},
        ],
        "unit": "亿元",
    },

    # ── T2 财务 ──────────────────────────────
    "eps": {
        "tier": "T2",
        "criticality": "K1",
        "description": "每股收益（最新年报列）",
        "sources": [
            {"name": "sina_finance", "fn": "fetch_eps"},
        ],
        "unit": "元",
        "prefer_diluted": True,
    },

    # ── T2 趋势指标 ──────────────────────────
    "rsi": {
        "tier": "T1",
        "criticality": "K2",
        "description": "相对强弱指标(14日)",
        "sources": [
            {"name": "compute", "fn": "compute_rsi"},
        ],
        "unit": "",
        "range": [0, 100],
    },

    "macd_signal": {
        "tier": "T1",
        "criticality": "K2",
        "description": "MACD金叉/死叉信号",
        "sources": [
            {"name": "compute", "fn": "compute_macd_signal"},
        ],
        "unit": "",
    },

    "ma_cross": {
        "tier": "T1",
        "criticality": "K2",
        "description": "均线金叉(MA5上穿MA20)",
        "sources": [
            {"name": "compute", "fn": "compute_ma_cross"},
        ],
        "unit": "",
    },

    "volume_ratio": {
        "tier": "T1",
        "criticality": "K2",
        "description": "量比",
        "sources": [
            {"name": "compute", "fn": "compute_volume_ratio"},
        ],
        "unit": "",
    },

    # ── T2 资金流 ────────────────────────────
    "north_flow": {
        "tier": "T1",
        "criticality": "K2",
        "description": "北向资金净买入",
        "sources": [
            {"name": "akshare_north", "fn": "fetch_north_flow"},
        ],
        "unit": "亿元",
        "note": "近期常为NaN，必须过滤",
    },

    # ── T3 静态 ──────────────────────────────
    "name": {
        "tier": "T3",
        "criticality": "K1",
        "description": "股票名称",
        "sources": [
            {"name": "stock_basic_db", "fn": "db_stock_basic"},
        ],
        "unit": "",
    },

    "market": {
        "tier": "T3",
        "criticality": "K1",
        "description": "市场 (a/hk)",
        "sources": [
            {"name": "stock_basic_db", "fn": "db_stock_basic"},
        ],
        "unit": "",
    },

    "total_shares": {
        "tier": "T3",
        "criticality": "K1",
        "description": "总股本",
        "sources": [
            {"name": "sina_finance", "fn": "fetch_total_shares"},
        ],
        "unit": "亿股",
    },
}


def get(name: str) -> dict | None:
    """获取指标定义"""
    return INDICATORS.get(name)


def list_by_tier(tier: str) -> list[str]:
    """列出指定时间维度的所有指标名"""
    return [k for k, v in INDICATORS.items() if v["tier"] == tier]


def list_k1() -> list[str]:
    """列出所有K1核心指标"""
    return [k for k, v in INDICATORS.items() if v["criticality"] == "K1"]


def list_k2() -> list[str]:
    """列出所有K2辅助指标"""
    return [k for k, v in INDICATORS.items() if v["criticality"] == "K2"]
