"""
Stock Advisor 综合评分引擎 (Phase 3)
包装 stock-screener，增加：
- 政策催化因子（wiki 知识库匹配）
- 板块动量因子
- 龙虎榜/大宗加分项
- 北向/南向资金因子
- 做空比例监控（港股）
- A股/港股 × 长线/短线 四象限权重
"""

import json
import os
import sys
import sqlite3
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

# 项目路径
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SCREENER_ROOT = os.path.expanduser("~/code/stock-screener")
sys.path.insert(0, SCREENER_ROOT)

# 配置
def load_config():
    import yaml
    config_path = os.path.join(PROJECT_ROOT, "config.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)

WIKI_ROOT = os.path.expanduser("~/wiki/finance")

def get_policy_catalyst_score(symbol: str, name: str, cfg: dict) -> float:
    """
    政策催化因子：从 wiki 知识库匹配
    读取 concepts/ 下的政策事件页，匹配板块 → 返回催化分数
    """
    # TODO: 实现 wiki 知识库的政策-板块匹配
    # 当前返回基础分
    return 0.5

def get_sector_momentum(symbol: str, sector: str) -> float:
    """
    板块动量：板块内多只个股同步走强则得分
    """
    # TODO: 实现板块动量计算
    return 0.5

def get_northbound_score(symbol: str) -> float:
    """北向资金持仓变化（A股专用）"""
    try:
        db = _get_screener_db()
        five_days_ago = (date.today() - timedelta(days=10)).isoformat()
        cur = db.execute("""
            SELECT trade_date, net_inflow FROM hsgt_stock_daily
            WHERE symbol = ? AND direction = '北向' AND trade_date >= ?
            ORDER BY trade_date DESC LIMIT 5
        """, (symbol, five_days_ago))
        rows = cur.fetchall()
        db.close()

        if not rows:
            return 0.5

        net_flows = [r["net_inflow"] or 0 for r in rows]
        weights = [0.35, 0.25, 0.2, 0.12, 0.08]
        weighted = sum(nf * w for nf, w in zip(net_flows, weights[:len(net_flows)]))

        if weighted > 100_000_000:
            return 0.9
        elif weighted > 10_000_000:
            return 0.75
        elif weighted > 0:
            return 0.6
        elif weighted > -10_000_000:
            return 0.4
        elif weighted > -100_000_000:
            return 0.3
        else:
            return 0.2
    except Exception as e:
        print(f"[northbound] {symbol}: {e}")
        return 0.5

def get_dragon_tiger_bonus(symbol: str) -> int:
    """
    龙虎榜加分项（短线专用）
    数据源：stock_lhb_detail_daily_sina（新浪源）
    字段：股票代码, 股票名称, 收盘价, 对应值, 成交量, 成交额, 指标
    无净买额字段，改为：上榜即+3分（有资金关注），不做净买卖判断
    """
    today = date.today().isoformat()
    data_path = os.path.join(WIKI_ROOT, f"raw/data/dragon_tiger_{today}.json")
    if not os.path.exists(data_path):
        return 0

    try:
        with open(data_path) as f:
            lhb = json.load(f)
        for row in lhb:
            code = str(row.get("股票代码", "")).zfill(6)
            if code == symbol or row.get("股票代码") == symbol:
                indicator = str(row.get("指标", ""))
                if "涨幅" in indicator or "振幅" in indicator:
                    return 3  # 涨势上榜+3分
                elif "跌幅" in indicator:
                    return -2  # 跌势上榜-2分
                else:
                    return 1  # 其他上榜+1分
    except:
        pass
    return 0

def get_block_trade_bonus(symbol: str) -> int:
    """大宗交易加分项：溢价+3分，折价>-8%减3分"""
    today = date.today().isoformat()
    data_path = os.path.join(WIKI_ROOT, f"raw/data/block_trades_{today}.json")
    if not os.path.exists(data_path):
        return 0

    try:
        with open(data_path) as f:
            trades = json.load(f)
        for row in trades:
            if row.get("代码") == symbol:
                discount = float(row.get("折溢率", 0) or 0)
                if discount > 0:
                    return 3
                elif discount < -8:
                    return -3
    except:
        pass
    return 0

def _get_screener_db():
    """获取 screener.db 只读连接"""
    db_path = os.path.expanduser("~/code/stock-screener/data/screener.db")
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    return db


def get_southbound_score(symbol: str) -> float:
    """南向资金持仓变化（港股专用）：最近5日南向净买入力度"""
    try:
        db = _get_screener_db()
        five_days_ago = (date.today() - timedelta(days=10)).isoformat()
        cur = db.execute("""
            SELECT trade_date, net_inflow FROM hsgt_stock_daily
            WHERE symbol = ? AND direction = '南向' AND trade_date >= ?
            ORDER BY trade_date DESC LIMIT 5
        """, (symbol, five_days_ago))
        rows = cur.fetchall()
        db.close()

        if not rows:
            return 0.5

        net_flows = [r["net_inflow"] or 0 for r in rows]
        weights = [0.35, 0.25, 0.2, 0.12, 0.08]
        weighted = sum(nf * w for nf, w in zip(net_flows, weights[:len(net_flows)]))

        if weighted > 100_000_000:
            return 0.9
        elif weighted > 10_000_000:
            return 0.7
        elif weighted > 0:
            return 0.6
        elif weighted > -10_000_000:
            return 0.4
        elif weighted > -100_000_000:
            return 0.3
        else:
            return 0.2
    except Exception as e:
        print(f"[southbound] {symbol}: {e}")
        return 0.5


def get_short_sell_score(symbol: str) -> float:
    """做空比例因子（港股专用）：高做空→低分"""
    try:
        db = _get_screener_db()
        cur = db.execute("""
            SELECT short_ratio FROM hk_short_signal
            WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1
        """, (symbol,))
        row = cur.fetchone()
        db.close()

        if not row or row["short_ratio"] is None:
            return 0.5

        ratio = float(row["short_ratio"])
        if ratio > 25:
            return 0.1
        elif ratio > 15:
            return 0.3
        elif ratio > 8:
            return 0.5
        elif ratio > 3:
            return 0.7
        else:
            return 0.9
    except Exception as e:
        print(f"[short_sell] {symbol}: {e}")
        return 0.5


def get_global_macro_score(symbol: str) -> float:
    """全球宏观因子（港股专用）：VIX/DXY/CNH对港股的影响"""
    try:
        db = _get_screener_db()
        cur = db.execute("""
            SELECT symbol, close FROM index_global_daily
            WHERE symbol IN ('VIX', 'USDCNH')
            AND trade_date = (SELECT MAX(trade_date) FROM index_global_daily WHERE symbol IN ('VIX', 'USDCNH'))
        """)
        rows = {r["symbol"]: r["close"] for r in cur.fetchall()}
        db.close()

        vix = rows.get("VIX", 20)
        usdcnh = rows.get("USDCNH", 7.2)

        vix_factor = 1.0
        if vix > 30:
            vix_factor = 0.3
        elif vix > 25:
            vix_factor = 0.5
        elif vix > 20:
            vix_factor = 0.7
        elif vix > 15:
            vix_factor = 0.85

        cnh_factor = 1.0
        if usdcnh > 7.3:
            cnh_factor = 0.5
        elif usdcnh > 7.1:
            cnh_factor = 0.7
        elif usdcnh < 6.8:
            cnh_factor = 0.9

        return (vix_factor * 0.6 + cnh_factor * 0.4)
    except Exception as e:
        print(f"[global_macro] {e}")
        return 0.5

def load_premarket_sentiment() -> Dict:
    """加载当日开盘情绪分数"""
    today = date.today().isoformat()
    path = os.path.join(WIKI_ROOT, f"raw/data/premarket_{today}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f).get("sentiment", {})
    return {"recommendation": "neutral", "position_advice": "稳健", "a_max": 4, "hk_max": 4}

def score_a_share(symbol: str, name: str, strategy: str = "short_term") -> Dict:
    """
    A股综合打分
    strategy: "long_term" | "short_term"
    """
    cfg = load_config()
    weights = cfg["a_share"][strategy]

    # 基础因子（复用 stock-screener）
    from src import screener
    screener_cfg = {
        "value": {"pe_max": 20, "pb_max": 3, "roe_min": 10, "dividend_yield_min": 2, "debt_ratio_max": 60, "total_mv_min": 50},
        "trend": {"ma_short": 5, "ma_long": 20, "ma_cross_days": 5, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "rsi_period": 14, "rsi_min": 50, "rsi_max": 75},
        "volume": {"volume_short": 5, "volume_long": 20, "volume_ratio_min": 1.5, "price_strength_window": 60, "price_strength_high_pct": 0.80},
        "scoring": {"value_weight": 0.35, "trend_weight": 0.35, "volume_weight": 0.30}
    }

    base = screener.screen_single(symbol, name, "A", screener_cfg)

    # 基础因子（从 base 结果反推各因子分）
    value_score = base.get("pe", 0) * 0.35
    trend_score = base.get("score", 0) * 0.35
    volume_score = base.get("score", 0) * 0.30

    # 新增因子
    policy_score = get_policy_catalyst_score(symbol, name, cfg)
    sector_score = get_sector_momentum(symbol, "default")
    northbound_score = get_northbound_score(symbol)

    # 组装加权总分
    base_weight = sum([weights["value"], weights["trend"], weights["volume"]])
    new_weight = sum([weights["policy"], weights["sector_momentum"], weights["northbound"]])
    total_weight = base_weight + new_weight

    if total_weight == 0:
        final_score = 0
    else:
        final_score = (
            value_score * weights["value"] / total_weight +
            trend_score * weights["trend"] / total_weight +
            volume_score * weights["volume"] / total_weight +
            policy_score * weights["policy"] / total_weight +
            sector_score * weights["sector_momentum"] / total_weight +
            northbound_score * weights["northbound"] / total_weight
        ) * 100

    final_score = round(final_score, 1)

    # 短线加分项
    bonus = 0
    if strategy == "short_term":
        bonus += get_dragon_tiger_bonus(symbol) * (weights.get("dragon_tiger_bonus", 8) / 100)
        bonus += get_block_trade_bonus(symbol) * (weights.get("block_trade_bonus", 3) / 100)

    final_score += bonus

    return {
        "symbol": symbol,
        "name": name,
        "market": "A",
        "strategy": strategy,
        "score": round(final_score, 1),
        "base_score": base.get("score", 0),
        "bonus": round(bonus, 1),
        "factors": {
            "value": round(value_score, 2),
            "trend": round(trend_score, 2),
            "volume": round(volume_score, 2),
            "policy": round(policy_score, 2),
            "sector": round(sector_score, 2),
            "northbound": round(northbound_score, 2),
        }
    }


def score_hk_share(symbol: str, name: str, strategy: str = "short_term") -> Dict:
    """港股综合打分（6因子加权）"""
    cfg = load_config()
    weights = cfg["hk_share"][strategy]

    # 基础因子（尝试接入港股 screener）
    base_score = 50
    value_score = 50
    trend_score = 50
    volume_score = 50
    try:
        from src import screener
        screener_cfg = {
            "value": {"pe_max": 20, "pb_max": 3, "roe_min": 10, "dividend_yield_min": 2, "debt_ratio_max": 60, "total_mv_min": 50},
            "trend": {"ma_short": 5, "ma_long": 20, "ma_cross_days": 5, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "rsi_period": 14, "rsi_min": 50, "rsi_max": 75},
            "volume": {"volume_short": 5, "volume_long": 20, "volume_ratio_min": 1.5, "price_strength_window": 60, "price_strength_high_pct": 0.80},
            "scoring": {"value_weight": 0.35, "trend_weight": 0.35, "volume_weight": 0.30}
        }
        base = screener.screen_single(symbol, name, "HK", screener_cfg)
        base_score = base.get("score", 50)
        value_score = base.get("pe", base_score)
        trend_score = base.get("trend_score", base_score)
        volume_score = base.get("volume_score", base_score)
    except Exception as e:
        pass

    # 港股特有因子
    southbound = get_southbound_score(symbol) * 100
    short_sell = get_short_sell_score(symbol) * 100
    global_macro = get_global_macro_score(symbol) * 100

    # 加权计算（与A股score_a_share保持一致的加权方式）
    w_value = weights.get("value", 0.40 if strategy == "long_term" else 0.15)
    w_trend = weights.get("trend", 0.15 if strategy == "long_term" else 0.25)
    w_volume = weights.get("volume", 0.05 if strategy == "long_term" else 0.10)
    w_southbound = weights.get("southbound", 0.15)
    w_short_sell = weights.get("short_sell", 0.10 if strategy == "long_term" else 0.15)
    w_global_macro = weights.get("global_macro", 0.15)

    total_weight = w_value + w_trend + w_volume + w_southbound + w_short_sell + w_global_macro

    if total_weight > 0:
        final_score = (
            value_score * w_value + trend_score * w_trend + volume_score * w_volume +
            southbound * w_southbound + short_sell * w_short_sell + global_macro * w_global_macro
        ) / total_weight
    else:
        final_score = base_score

    return {
        "symbol": symbol,
        "name": name,
        "market": "HK",
        "strategy": strategy,
        "score": round(final_score, 1),
        "factors": {
            "value": round(value_score, 1),
            "trend": round(trend_score, 1),
            "volume": round(volume_score, 1),
            "southbound": round(southbound, 1),
            "short_sell": round(short_sell, 1),
            "global_macro": round(global_macro, 1),
        }
    }


def screen_all(market: str = "A", strategy: str = "short_term", limit: int = 5):
    """
    全市场筛选：拉取行情 → 打分 → 排序 → 返回 Top N
    """
    sentiment = load_premarket_sentiment()
    max_count = sentiment.get(f"{'a' if market == 'A' else 'hk'}_max", 5)
    max_count = min(max_count, limit)

    # TODO: 接入 akshare 全量股票列表 + 批量打分
    # 当前占位返回
    return {
        "market": market,
        "strategy": strategy,
        "sentiment": sentiment,
        "max_count": max_count,
        "recommendations": [],
        "timestamp": datetime.now().isoformat(),
    }


# ═══════════════════════════════════════════════════════════════
# 四象限评分函数 (Phase 3)
# ═══════════════════════════════════════════════════════════════

def _calc_value_score(pe: Optional[float], pb: Optional[float], roe: Optional[float],
                      dividend_yield: Optional[float] = None) -> float:
    """计算价值因子分 0-100. 低PE+低PB+高ROE+高股息 → 高分"""
    score = 50.0
    if pe is not None and pe > 0:
        if pe <= 10:
            score = 90
        elif pe <= 15:
            score = 80
        elif pe <= 25:
            score = 65
        elif pe <= 40:
            score = 50
        else:
            score = 30
    if pb is not None and pb > 0:
        pb_score = 50
        if pb <= 1:
            pb_score = 90
        elif pb <= 2:
            pb_score = 75
        elif pb <= 5:
            pb_score = 55
        elif pb <= 10:
            pb_score = 40
        else:
            pb_score = 25
        score = score * 0.6 + pb_score * 0.4
    if roe is not None:
        if roe > 20:
            score = min(score + 10, 100)
        elif roe > 15:
            score = min(score + 5, 100)
        elif roe < 5:
            score = max(score - 10, 0)
    if dividend_yield is not None and dividend_yield > 0:
        if dividend_yield > 5:
            score = min(score + 10, 100)
        elif dividend_yield > 3:
            score = min(score + 5, 100)
    return max(0, min(100, score))


def _calc_trend_score(rsi: Optional[float], ma_cross: Optional[int] = 0,
                      macd_signal: Optional[int] = 0) -> float:
    """计算趋势因子分 0-100. RSI适中+金叉+MACD信号 → 高分"""
    score = 50.0
    if rsi is not None:
        if 50 <= rsi <= 65:
            score = 80  # 温和上升
        elif 40 <= rsi < 50:
            score = 60  # 偏弱但有反弹可能
        elif 65 < rsi <= 75:
            score = 70  # 强势但注意回调
        elif rsi > 75:
            score = 40  # 超买风险
        else:
            score = 30  # 超卖
    if ma_cross and ma_cross == 1:
        score = min(score + 15, 100)
    if macd_signal and macd_signal == 1:
        score = min(score + 10, 100)
    return max(0, min(100, score))


def _calc_volume_score(volume_ratio: Optional[float],
                       daily_amount: Optional[float] = None,
                       market: str = "A") -> float:
    """计算量能因子分 0-100"""
    score = 50.0
    if volume_ratio is not None:
        if volume_ratio > 3:
            score = 90
        elif volume_ratio > 2:
            score = 80
        elif volume_ratio > 1.5:
            score = 65
        elif volume_ratio > 1.0:
            score = 50
        else:
            score = 35
    return max(0, min(100, score))


def _enrich_candidate_from_db(cand: dict) -> dict:
    """从 screener.db 补充候选股票数据（基本面、日线、资金流）"""
    symbol = cand.get("symbol", "")
    result = dict(cand)

    try:
        db = _get_screener_db()

        # 基本面
        row = db.execute(
            "SELECT pe, pb, roe, dividend_yield, total_mv FROM stock_fundamental WHERE symbol=?",
            (symbol,)
        ).fetchone()
        if row:
            for k in ["pe", "pb", "roe", "dividend_yield", "total_mv"]:
                if result.get(k) is None:
                    result[k] = row[k]

        # 最新日线（成交额）
        row = db.execute(
            "SELECT close, volume, amount FROM stock_daily WHERE symbol=? ORDER BY trade_date DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        if row:
            result.setdefault("close", row["close"])
            result.setdefault("volume", row["volume"])
            result.setdefault("amount", row["amount"])

        # 最新 screen_results 中的技术指标
        row = db.execute(
            "SELECT volume_ratio, rsi, ma_cross, macd_signal, score, pe, pb, roe "
            "FROM screen_results WHERE symbol=? ORDER BY screen_date DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        if row:
            for k in ["volume_ratio", "rsi", "ma_cross", "macd_signal"]:
                if result.get(k) is None and row[k] is not None:
                    result[k] = row[k]

        db.close()
    except Exception as e:
        print(f"[_enrich] {symbol}: {e}")

    return result


def _check_limit_down_from_db(symbol: str) -> bool:
    """检查是否跌停（通过 lhb pct_change 或 close 变化判断）"""
    try:
        db = _get_screener_db()
        row = db.execute(
            "SELECT pct_change FROM lhb_daily WHERE symbol=? ORDER BY trade_date DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        db.close()
        if row and row["pct_change"] is not None:
            return row["pct_change"] <= -9.5
    except:
        pass
    return False


def _check_southbound_continuity(symbol: str, min_days: int = 3) -> float:
    """检查南向资金持续流入天数"""
    try:
        db = _get_screener_db()
        rows = db.execute(
            "SELECT net_inflow FROM hsgt_stock_daily "
            "WHERE symbol=? AND direction='南向' ORDER BY trade_date DESC LIMIT 10",
            (symbol,)
        ).fetchall()
        db.close()
        if not rows:
            return 0.0
        positive_days = sum(1 for r in rows if r["net_inflow"] and r["net_inflow"] > 0)
        return positive_days / max(len(rows), 1)
    except:
        return 0.0


# ─── A股长期投资评分 ────────────────────────────────────────────

def score_a_long_term(candidates: list[dict]) -> list[dict]:
    """
    A股长期投资评分: PE×ROE×PB×分红率×北向持续性
    权重: value 0.35 + trend 0.20 + northbound 0.15 + policy 0.20 + sector 0.10
    """
    cfg = load_config()
    weights = cfg["a_share"]["long_term"]

    scored = []
    for raw_cand in candidates:
        cand = _enrich_candidate_from_db(raw_cand)
        symbol = cand.get("symbol", "")
        name = cand.get("name", "")
        pe = cand.get("pe")
        pb = cand.get("pb")
        roe = cand.get("roe")
        dividend_yield = cand.get("dividend_yield")
        rsi = cand.get("rsi")
        volume_ratio = cand.get("volume_ratio")
        ma_cross = cand.get("ma_cross", 0)
        macd_signal = cand.get("macd_signal", 0)
        amount = cand.get("amount", 0)

        # ── 硬过滤 ──
        pe_val = pe if pe and pe > 0 else float("inf")
        if pe_val >= 20:
            continue  # 首轮PE<20
        if roe is not None and roe <= 8:
            continue  # ROE>8%
        if amount is not None and amount < 20_000_000:
            continue  # 日均成交额<2000万排除

        # ── 因子评分 (0-100) ──
        value_score = _calc_value_score(pe, pb, roe, dividend_yield)

        trend_score = _calc_trend_score(rsi, ma_cross, macd_signal)

        volume_score = _calc_volume_score(volume_ratio, amount, "A")

        northbound_score = get_northbound_score(symbol) * 100

        policy_score = get_policy_catalyst_score(symbol, name, cfg) * 100

        sector_score = get_sector_momentum(symbol, "default") * 100

        # ── 加权总分 ──
        total_w = sum(weights.get(k, 0) for k in ["value", "trend", "volume", "northbound", "policy", "sector_momentum"])
        if total_w > 0:
            composite = (
                value_score * weights.get("value", 0) +
                trend_score * weights.get("trend", 0) +
                volume_score * weights.get("volume", 0) +
                northbound_score * weights.get("northbound", 0) +
                policy_score * weights.get("policy", 0) +
                sector_score * weights.get("sector_momentum", 0)
            ) / total_w
        else:
            composite = 50.0

        scored.append({
            "symbol": symbol,
            "name": name,
            "market": "A",
            "strategy_type": "long_term",
            "score": round(composite, 1),
            "filters_passed": True,
            "factors": {
                "value": round(value_score, 1),
                "trend": round(trend_score, 1),
                "volume": round(volume_score, 1),
                "northbound": round(northbound_score, 1),
                "policy": round(policy_score, 1),
                "sector": round(sector_score, 1),
            },
            "raw_data": {k: cand.get(k) for k in ["pe", "pb", "roe", "dividend_yield", "rsi",
                                                    "volume_ratio", "ma_cross", "macd_signal", "amount"]}
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # 去重（同symbol保留最高分）
    seen = set()
    deduped = []
    for item in scored:
        if item["symbol"] not in seen:
            seen.add(item["symbol"])
            deduped.append(item)
    return deduped


# ─── A股短线投机评分 ────────────────────────────────────────────

def score_a_short_term(candidates: list[dict]) -> list[dict]:
    """
    A股短线投机评分: 量比×RSI动量×龙虎榜×政策事件
    权重: trend 0.30 + volume 0.20 + value 0.10 + policy 0.15 + sector 0.10 + northbound 0.05
    """
    cfg = load_config()
    weights = cfg["a_share"]["short_term"]

    scored = []
    for raw_cand in candidates:
        cand = _enrich_candidate_from_db(raw_cand)
        symbol = cand.get("symbol", "")
        name = cand.get("name", "")
        volume_ratio = cand.get("volume_ratio", 0)
        rsi = cand.get("rsi")
        ma_cross = cand.get("ma_cross", 0)
        macd_signal = cand.get("macd_signal", 0)
        amount = cand.get("amount", 0)

        # ── 硬过滤 ──
        if volume_ratio is None or volume_ratio < 1.5:
            continue
        if rsi is not None and (rsi < 30 or rsi > 75):
            continue  # RSI适中区间 30-75
        if _check_limit_down_from_db(symbol):
            continue  # 排除跌停

        # ── 因子评分 ──
        trend_score = _calc_trend_score(rsi, ma_cross, macd_signal)
        volume_score = _calc_volume_score(volume_ratio, amount, "A")

        pe = cand.get("pe")
        pb = cand.get("pb")
        roe = cand.get("roe")
        value_score = _calc_value_score(pe, pb, roe)

        policy_score = get_policy_catalyst_score(symbol, name, cfg) * 100
        sector_score = get_sector_momentum(symbol, "default") * 100
        northbound_score = get_northbound_score(symbol) * 100

        bonus = 0.0
        dt_bonus = get_dragon_tiger_bonus(symbol)
        bt_bonus = get_block_trade_bonus(symbol)
        bonus += dt_bonus * (weights.get("dragon_tiger_bonus", 8) / 100) * 10
        bonus += bt_bonus * (weights.get("block_trade_bonus", 3) / 100) * 10

        # ── 加权总分 ──
        total_w = sum(weights.get(k, 0) for k in ["value", "trend", "volume", "policy", "sector_momentum", "northbound"])
        if total_w > 0:
            composite = (
                value_score * weights.get("value", 0) +
                trend_score * weights.get("trend", 0) +
                volume_score * weights.get("volume", 0) +
                policy_score * weights.get("policy", 0) +
                sector_score * weights.get("sector_momentum", 0) +
                northbound_score * weights.get("northbound", 0)
            ) / total_w + bonus
        else:
            composite = 50.0 + bonus

        scored.append({
            "symbol": symbol,
            "name": name,
            "market": "A",
            "strategy_type": "short_term",
            "score": round(composite, 1),
            "filters_passed": True,
            "bonus": round(bonus, 1),
            "factors": {
                "value": round(value_score, 1),
                "trend": round(trend_score, 1),
                "volume": round(volume_score, 1),
                "policy": round(policy_score, 1),
                "sector": round(sector_score, 1),
                "northbound": round(northbound_score, 1),
            },
            "raw_data": {k: cand.get(k) for k in ["pe", "pb", "roe", "rsi", "volume_ratio",
                                                    "ma_cross", "macd_signal", "amount"]}
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # 去重（同symbol保留最高分）
    seen = set()
    deduped = []
    for item in scored:
        if item["symbol"] not in seen:
            seen.add(item["symbol"])
            deduped.append(item)
    return deduped


# ─── 港股长期投资评分 ────────────────────────────────────────────

def score_hk_long_term(candidates: list[dict]) -> list[dict]:
    """
    港股长期投资评分: PE折价×股息率×南向持续性×全球宏观
    权重: value 0.40 + southbound 0.15 + global_macro 0.15 + trend 0.15 + volume 0.05
    """
    cfg = load_config()
    weights = cfg["hk_share"]["long_term"]

    scored = []
    for raw_cand in candidates:
        cand = _enrich_candidate_from_db(raw_cand)
        symbol = cand.get("symbol", "")
        name = cand.get("name", "")
        pe = cand.get("pe")
        pb = cand.get("pb")
        roe = cand.get("roe")
        dividend_yield = cand.get("dividend_yield")
        rsi = cand.get("rsi")
        ma_cross = cand.get("ma_cross", 0)
        macd_signal = cand.get("macd_signal", 0)
        volume_ratio = cand.get("volume_ratio")
        amount = cand.get("amount", 0)

        # ── 硬过滤 ──
        # PE折价>20%: PE相对于行业显著偏低(相对行业中位数)
        # 这里简化: PE<12视为有折价
        if pe is None or pe <= 0 or pe > 12:
            continue
        if dividend_yield is None or dividend_yield < 3:
            continue  # 股息率>3%

        # 南向持续流入检查
        south_cont = _check_southbound_continuity(symbol)
        if south_cont < 0.4:
            continue  # 南向资金持续度不够

        # ── 因子评分 ──
        value_score = _calc_value_score(pe, pb, roe, dividend_yield)

        trend_score = _calc_trend_score(rsi, ma_cross, macd_signal)

        volume_score = _calc_volume_score(volume_ratio, amount, "HK")

        southbound_score = get_southbound_score(symbol) * 100
        short_sell_score = get_short_sell_score(symbol) * 100
        global_macro_score = get_global_macro_score(symbol) * 100

        # ── 加权总分 ──
        total_w = sum(weights.get(k, 0) for k in ["value", "trend", "southbound", "global_macro", "short_sell", "volume"])
        if total_w > 0:
            composite = (
                value_score * weights.get("value", 0) +
                trend_score * weights.get("trend", 0) +
                southbound_score * weights.get("southbound", 0) +
                global_macro_score * weights.get("global_macro", 0) +
                short_sell_score * weights.get("short_sell", 0) +
                volume_score * weights.get("volume", 0)
            ) / total_w
        else:
            composite = 50.0

        scored.append({
            "symbol": symbol,
            "name": name,
            "market": "HK",
            "strategy_type": "long_term",
            "score": round(composite, 1),
            "filters_passed": True,
            "factors": {
                "value": round(value_score, 1),
                "trend": round(trend_score, 1),
                "volume": round(volume_score, 1),
                "southbound": round(southbound_score, 1),
                "short_sell": round(short_sell_score, 1),
                "global_macro": round(global_macro_score, 1),
            },
            "raw_data": {k: cand.get(k) for k in ["pe", "pb", "roe", "dividend_yield", "rsi",
                                                    "volume_ratio", "ma_cross", "macd_signal", "amount"]}
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # 去重（同symbol保留最高分）
    seen = set()
    deduped = []
    for item in scored:
        if item["symbol"] not in seen:
            seen.add(item["symbol"])
            deduped.append(item)
    return deduped


# ─── 港股短线投机评分 ────────────────────────────────────────────

def score_hk_short_term(candidates: list[dict]) -> list[dict]:
    """
    港股短线投机评分: 量能×做空回补×AH溢价波动×板块效应
    权重: trend 0.25 + value 0.15 + southbound 0.15 + global_macro 0.15 + short_sell 0.15 + volume 0.10
    """
    cfg = load_config()
    weights = cfg["hk_share"]["short_term"]

    scored = []
    for raw_cand in candidates:
        cand = _enrich_candidate_from_db(raw_cand)
        symbol = cand.get("symbol", "")
        name = cand.get("name", "")
        volume_ratio = cand.get("volume_ratio", 0)
        rsi = cand.get("rsi")
        ma_cross = cand.get("ma_cross", 0)
        macd_signal = cand.get("macd_signal", 0)
        amount = cand.get("amount", 0)

        # ── 硬过滤 ──
        # 做空比例检查
        try:
            db = _get_screener_db()
            row = db.execute(
                "SELECT short_ratio FROM hk_short_signal WHERE symbol=? ORDER BY trade_date DESC LIMIT 1",
                (symbol,)
            ).fetchone()
            db.close()
            sr = float(row["short_ratio"]) if row and row["short_ratio"] is not None else 0
        except:
            sr = 0
        if sr >= 20:
            continue  # 做空比例<20%

        if volume_ratio is None or volume_ratio < 1.5:
            continue  # 量比>1.5

        # ── 因子评分 ──
        pe = cand.get("pe")
        pb = cand.get("pb")
        roe = cand.get("roe")
        value_score = _calc_value_score(pe, pb, roe)

        trend_score = _calc_trend_score(rsi, ma_cross, macd_signal)
        volume_score = _calc_volume_score(volume_ratio, amount, "HK")

        southbound_score = get_southbound_score(symbol) * 100
        global_macro_score = get_global_macro_score(symbol) * 100
        short_sell_score = get_short_sell_score(symbol) * 100

        sector_score = get_sector_momentum(symbol, "default") * 100

        # ── 加权总分 ──
        sector_w = weights.get("sector_momentum", 0.05)
        total_w = sum(weights.get(k, 0) for k in ["value", "trend", "southbound", "global_macro", "short_sell", "volume"])
        total_w += sector_w
        if total_w > 0:
            composite = (
                value_score * weights.get("value", 0) +
                trend_score * weights.get("trend", 0) +
                southbound_score * weights.get("southbound", 0) +
                global_macro_score * weights.get("global_macro", 0) +
                short_sell_score * weights.get("short_sell", 0) +
                volume_score * weights.get("volume", 0) +
                sector_score * sector_w
            ) / total_w
        else:
            composite = 50.0

        scored.append({
            "symbol": symbol,
            "name": name,
            "market": "HK",
            "strategy_type": "short_term",
            "score": round(composite, 1),
            "filters_passed": True,
            "factors": {
                "value": round(value_score, 1),
                "trend": round(trend_score, 1),
                "volume": round(volume_score, 1),
                "southbound": round(southbound_score, 1),
                "short_sell": round(short_sell_score, 1),
                "global_macro": round(global_macro_score, 1),
            },
            "raw_data": {k: cand.get(k) for k in ["pe", "pb", "roe", "rsi", "volume_ratio",
                                                    "ma_cross", "macd_signal", "amount"]}
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # 去重（同symbol保留最高分）
    seen = set()
    deduped = []
    for item in scored:
        if item["symbol"] not in seen:
            seen.add(item["symbol"])
            deduped.append(item)
    return deduped


# ─── 推荐回退机制 ────────────────────────────────────────────────

def recommend_with_fallback(results: dict, min_score: float = 5.0) -> dict:
    """
    确保每个象限至少1个推荐。
    对于空象限，逐步放松过滤条件。

    results: {
        "a_long_term": [scored items],
        "a_short_term": [...],
        "hk_long_term": [...],
        "hk_short_term": [...]
    }

    回退等级:
      Level 1: 放宽主要过滤条件 (PE<25 for long, volume_ratio>1.2 for short)
      Level 2: 取最高分（即使低于初始阈值，但>=min_score）
      Level 3: 标记为 "NO_QUALIFIED_PICK"
    """
    quadrants = {
        "a_long_term": {"market": "A", "strategy": "long_term", "score_fn": score_a_long_term},
        "a_short_term": {"market": "A", "strategy": "short_term", "score_fn": score_a_short_term},
        "hk_long_term": {"market": "HK", "strategy": "long_term", "score_fn": score_hk_long_term},
        "hk_short_term": {"market": "HK", "strategy": "short_term", "score_fn": score_hk_short_term},
    }

    # 从原始 candidates 池构建
    all_candidates = {}
    for qname, qinfo in quadrants.items():
        if qname not in results:
            results[qname] = []
        all_candidates[qname] = results[qname]

    # ── 确保每个象限至少1个 ──
    for qname, qinfo in quadrants.items():
        items = all_candidates.get(qname, [])
        if items and any(item.get("score", 0) >= min_score for item in items):
            # 已有足够好的推荐
            continue

        market = qinfo["market"]
        strategy = qinfo["strategy"]
        score_fn = qinfo["score_fn"]

        # 从原 candidates 构建宽松版本
        result_items = items[:]

        # Level 1: 放宽过滤条件
        if not result_items or all(item.get("score", 0) < min_score for item in result_items):
            relaxed = _relax_candidates(results.get(f"_{qname}_raw", results.get(qname, [])),
                                        market, strategy, level=1)
            if relaxed:
                relaxed_scored = score_fn(relaxed)
                result_items.extend(relaxed_scored)

        # Level 2: 取最高分 >= min_score
        if not result_items or all(item.get("score", 0) < min_score for item in result_items):
            relaxed = _relax_candidates(results.get(f"_{qname}_raw", results.get(qname, [])),
                                        market, strategy, level=2)
            if relaxed:
                relaxed_scored = score_fn(relaxed)
                # 过滤掉分数过低的
                for item in relaxed_scored:
                    if item.get("score", 0) >= min_score:
                        result_items.append(item)

        # Level 3: 无合格标的
        if not result_items or all(item.get("score", 0) < min_score for item in result_items):
            result_items.append({
                "symbol": "NONE",
                "name": "NO_QUALIFIED_PICK",
                "market": market,
                "strategy_type": strategy,
                "score": 0,
                "filters_passed": False,
                "factors": {},
                "raw_data": {},
                "fallback": True,
                "fallback_level": 3
            })

        # 去重（按symbol去重，保留高分）
        seen = {}
        for item in result_items:
            sym = item.get("symbol", "")
            if sym in seen:
                if item.get("score", 0) > seen[sym].get("score", 0):
                    seen[sym] = item
            else:
                seen[sym] = item
        deduped = sorted(seen.values(), key=lambda x: x.get("score", 0), reverse=True)

        # 标记回退级别
        for item in deduped:
            if "fallback" not in item:
                if item.get("score", 0) < min_score:
                    item["fallback"] = True
                    item["fallback_level"] = 2
                else:
                    item["fallback"] = False
                    item["fallback_level"] = 0

        results[qname] = deduped

    return results


def _relax_candidates(candidates: list[dict], market: str, strategy: str,
                      level: int = 1) -> list[dict]:
    """
    根据回退级别放松过滤条件。
    level=1: 放宽主要过滤
    level=2: 仅取最高分
    """
    if not candidates:
        return []

    relaxed = []
    for cand in candidates:
        c = dict(cand)
        if market == "A" and strategy == "long_term":
            pe = c.get("pe")
            pe_val = pe if pe and pe > 0 else float("inf")
            if level == 1:
                # 放宽到PE<25（原为20）
                if pe_val >= 25:
                    continue
            elif level == 2:
                # 接受任何PE
                pass
        elif market == "A" and strategy == "short_term":
            vol_ratio = c.get("volume_ratio", 0)
            if level == 1:
                # 放宽到volume_ratio>1.2（原为1.5）
                if vol_ratio < 1.2:
                    continue
            elif level == 2:
                pass
        elif market == "HK" and strategy == "long_term":
            pe = c.get("pe")
            pe_val = pe if pe and pe > 0 else float("inf")
            div_yield = c.get("dividend_yield", 0)
            if level == 1:
                # 放宽PE<18, 股息率>2%
                if pe_val >= 18:
                    continue
                if div_yield is not None and div_yield < 2:
                    continue
            elif level == 2:
                pass
        elif market == "HK" and strategy == "short_term":
            vol_ratio = c.get("volume_ratio", 0)
            if level == 1:
                if vol_ratio < 1.2:
                    continue
            elif level == 2:
                pass
        relaxed.append(c)
    return relaxed


def run_four_quadrant_scoring(a_candidates: list[dict] = None,
                              hk_candidates: list[dict] = None,
                              min_score: float = 5.0) -> dict:
    """
    一站式四象限评分入口。

    a_candidates: A股候选列表（来自 screen_results 或其他来源）
    hk_candidates: 港股候选列表

    返回: {
        "a_long_term": [...],
        "a_short_term": [...],
        "hk_long_term": [...],
        "hk_short_term": [...],
    }
    """
    if a_candidates is None:
        # 默认从数据库拉取最新 A 股 screen_results
        db = _get_screener_db()
        latest = db.execute("SELECT MAX(screen_date) as dt FROM screen_results").fetchone()
        if latest and latest["dt"]:
            rows = db.execute(
                "SELECT * FROM screen_results WHERE screen_date=? AND market='a' ORDER BY score DESC",
                (latest["dt"],)
            ).fetchall()
            a_candidates = [dict(r) for r in rows]
        else:
            a_candidates = []
        db.close()

    if hk_candidates is None:
        db = _get_screener_db()
        latest = db.execute("SELECT MAX(screen_date) as dt FROM screen_results").fetchone()
        if latest and latest["dt"]:
            rows = db.execute(
                "SELECT * FROM screen_results WHERE screen_date=? AND market='hk' ORDER BY score DESC",
                (latest["dt"],)
            ).fetchall()
            hk_candidates = [dict(r) for r in rows]
        else:
            hk_candidates = []
        db.close()

    results = {
        "a_long_term": score_a_long_term(a_candidates),
        "a_short_term": score_a_short_term(a_candidates),
        "hk_long_term": score_hk_long_term(hk_candidates),
        "hk_short_term": score_hk_short_term(hk_candidates),
    }

    # 保存原始候选用于回退
    results["_a_candidates_raw"] = a_candidates
    results["_hk_candidates_raw"] = hk_candidates

    results = recommend_with_fallback(results, min_score)

    # 清理临时字段
    for k in list(results.keys()):
        if k.startswith("_"):
            del results[k]

    return results


if __name__ == "__main__":
    # 测试
    result = score_a_share("600519", "贵州茅台", "short_term")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 四象限评分测试
    print("\n=== 四象限评分测试 ===")
    quad_results = run_four_quadrant_scoring(min_score=5.0)
    for qname in ["a_long_term", "a_short_term", "hk_long_term", "hk_short_term"]:
        items = quad_results.get(qname, [])
        print(f"\n--- {qname} ({len(items)} items) ---")
        for item in items[:5]:
            sym = item.get("symbol", "")
            name = item.get("name", "")
            score = item.get("score", 0)
            fl = item.get("fallback_level", 0)
            fallback_tag = f" [FALLBACK L{fl}]" if fl > 0 else ""
            print(f"  {sym:10s} {name:12s} score={score:6.1f}{fallback_tag}")
