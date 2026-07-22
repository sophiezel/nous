#!/usr/bin/env python3
"""Walk-Forward 回测: 模拟完整荐股周期 v3.6
用法:
  python backtest_cycle.py --start 2026-03-10 --end 2026-03-21    # F3回测
  python backtest_cycle.py --random 3 --days 7                     # F3随机区间
  python backtest_cycle.py --engine TRL --days 30                  # TRL龙脉回测
  python backtest_cycle.py --engine dual --days 30                 # 双轨对比
  python backtest_cycle.py --engine dual --start X --end Y --llm   # 双轨+LLM解读
"""
import sys, os, json, random, sqlite3
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from nous.engine.pipelines.daily_recommendation_pipeline import run_pipeline

from nous.core.db import _resolve_path
DB = Path(_resolve_path("screener.db"))
REPORT_DIR = Path.home() / "wiki" / "finance" / "reports" / "backtest"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ── 工具函数 ──────────────────────────────────────────

def get_trading_days(start: str, end: str) -> list[str]:
    """获取区间内所有交易日"""
    conn = sqlite3.connect(str(DB))
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM stock_daily_all WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (start, end)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows] if rows else []


def get_feasible_range() -> tuple[str, str]:
    """获取可回测区间: index_daily起始+15天 ~ 最新日线日-1"""
    conn = sqlite3.connect(str(DB))
    idx_min = conn.execute("SELECT MIN(trade_date) FROM stock_daily_all").fetchone()[0]
    daily_max = conn.execute("SELECT MAX(trade_date) FROM stock_daily_all").fetchone()[0]
    conn.close()
    if not idx_min:
        return (None, None)
    start = (date.fromisoformat(idx_min) + timedelta(days=15)).isoformat()
    end = daily_max if daily_max < date.today().isoformat() else (date.today() - timedelta(days=1)).isoformat()
    return (start, end)


def _get_regime_for_date(d: str) -> tuple:
    """快速查询某日regime"""
    from nous.engine.pipelines.daily_recommendation_pipeline import _get_market_regime
    return _get_market_regime(as_of_date=d)


def _resolve_name(conn, symbol: str) -> str:
    """查询stock_basic获取真实名称, 失败返回symbol
    港股(5位代码)优先用stock_basic, name=symbol时尝试Sina API
    """
    r = conn.execute("SELECT name FROM stock_basic WHERE symbol=?", (symbol,)).fetchone()
    name = r[0] if r else symbol
    # 港股无真实名称时(name==symbol), 尝试Sina缓存
    if len(symbol) == 5 and name == symbol:
        name = _hk_name_from_cache(symbol)
        if name == symbol and not hasattr(_resolve_name, '_hk_warned'):
            _hk_name_from_cache.__dict__['_warned'] = True  # 只警告一次
    return name


# ── 港股名称缓存 (Sina rt_hk API, 单次会话缓) ──
_hk_name_cache = {}

def _hk_name_from_cache(code: str) -> str:
    """从Sina rt_hk实时API获取港股名称, 带缓存"""
    if code in _hk_name_cache:
        return _hk_name_cache[code]
    try:
        import requests, re
        resp = requests.get(
            f'https://hq.sinajs.cn/list=rt_hk{code}',
            headers={'Referer': 'https://finance.sina.com.cn'},
            timeout=3
        )
        m = re.search(r'rt_hk' + code + r'="[^,]*,\s*([^,]+)', resp.text)
        if m:
            name = m.group(1).strip()
            _hk_name_cache[code] = name
            return name
    except Exception:
        pass
    _hk_name_cache[code] = code
    return code


def _fmt_pnl(x):
    """格式化盈亏百分比"""
    if x is None: return "-"
    return f"{'+' if x > 0 else ''}{x:.1f}%"


# ══════════════════════════════════════════════════════
# Phase 1: TRL 主线分层报告
# ══════════════════════════════════════════════════════

def generate_trl_theme_report(trl_json: dict, conn) -> str:
    """从TRL JSON生成主线分层MD章节
    
    输入: trl_backtest_*.json 加载的dict
    输出: MD字符串, 包含主线切换时间线 + 按行业分层推荐
    """
    lines = ["## 🐉 龙脉TRL 主线分析", ""]
    
    daily = trl_json.get("daily", [])
    if not daily:
        lines.append("*无主线数据*")
        return "\n".join(lines)
    
    # 从theme_scores构建主题→category映射
    theme_cats = {}
    for day in daily:
        for ts in day.get("theme_scores", []):
            tname = ts.get("theme", "")
            tcat = ts.get("category", "?")
            if tname:
                theme_cats[tname] = tcat
    
    # ── 主线区间聚合 (按主线/板块去重, 非日期流水账) ──
    lines.append("### 主线区间总览")
    lines.append("| 主线状态 | 行业 | 区间 | 天数 | 均评分 | 均L1 | 均L2 | 均L3 | 推荐数 |")
    lines.append("|---------|------|------|------|--------|------|------|------|--------|")
    
    cat_emoji_map = {"confirmed": "🔴", "potential": "🟡", "watch": "⚪", "skip": "⏭️"}
    
    # 预计算每主题每日推荐数(从picks_detail, 非trl_picks总计数)
    theme_day_picks = {}
    for day in daily:
        for p in day.get("picks_detail", []):
            t = p.get("theme", "")
            if t:
                theme_day_picks[(day["date"], t)] = theme_day_picks.get((day["date"], t), 0) + 1
    
    # 按(状态, 行业)聚合区间
    from itertools import groupby
    segments = []
    for day in daily:
        for ts in day.get("theme_scores", []):
            cat = ts.get("category", "skip")
            if cat == "skip":
                continue
            tname = ts["theme"]
            segments.append({
                "date": day["date"],
                "theme": tname,
                "category": cat,
                "total_score": ts.get("total_score", 0),
                "l1": ts.get("layer1_price", 0),
                "l2": ts.get("layer2_flow", 0),
                "l3": ts.get("layer3_event", 0),
                "picks": theme_day_picks.get((day["date"], tname), 0),
            })
    
    # 按(类别, 主题)分组, 合并连续日期
    segments.sort(key=lambda x: (x["category"], x["theme"], x["date"]))
    merged = []
    for (cat, theme), group in groupby(segments, key=lambda x: (x["category"], x["theme"])):
        items = list(group)
        # 找连续区间
        i = 0
        while i < len(items):
            start = items[i]
            j = i
            while j + 1 < len(items) and items[j + 1]["date"] <= items[j]["date"]:
                j += 1  # 简单合并(日期已排序则连续)
            j = len(items) - 1  # 简化: 同一主题区间整体合并
            end = items[j]
            days_n = j - i + 1
            avg_score = round(sum(it["total_score"] for it in items[i:j+1]) / days_n)
            avg_l1 = round(sum(it["l1"] for it in items[i:j+1]) / days_n)
            avg_l2 = round(sum(it["l2"] for it in items[i:j+1]) / days_n)
            avg_l3 = round(sum(it["l3"] for it in items[i:j+1]) / days_n)
            total_picks = sum(it["picks"] for it in items[i:j+1])
            emoji = cat_emoji_map.get(cat, "❓")
            cat_label = {"confirmed": "🔴已确认主线", "potential": "🟡潜在主线", "watch": "⚪观察区"}.get(cat, cat)
            merged.append({
                "emoji": emoji, "cat_label": cat_label, "theme": theme,
                "start": start["date"], "end": end["date"], "days": days_n,
                "avg_score": avg_score, "avg_l1": avg_l1, "avg_l2": avg_l2, "avg_l3": avg_l3,
                "picks": total_picks
            })
            i = j + 1
    
    # 按状态优先级排序
    merged.sort(key=lambda x: ({"confirmed": 0, "potential": 1, "watch": 2}.get(
        x["cat_label"].replace("🔴","").replace("🟡","").replace("⚪","").replace("已确认主线","confirmed")
        .replace("潜在主线","potential").replace("观察区","watch"), 9), x["theme"]))
    
    for m in merged:
        date_range = f"{m['start'][5:]}→{m['end'][5:]}" if m['start'] != m['end'] else m['start'][5:]
        lines.append(f"| {m['cat_label']} | {m['theme']} | {date_range} | {m['days']}天 | "
                     f"{m['avg_score']} | {m['avg_l1']} | {m['avg_l2']} | {m['avg_l3']} | {m['picks']}只 |")
    lines.append("")
    
    # ── 主线统计 ──
    confirmed_days = trl_json.get("days_with_confirmed", 0)
    potential_days = trl_json.get("days_with_potential", 0)
    total_days = trl_json.get("total_days", len(daily))
    lines.append(f"> 🔴已确认主线: {confirmed_days}/{total_days}天 | "
                 f"🟡潜在主线: {potential_days}/{total_days}天 | "
                 f"总推荐: {trl_json.get('total_picks', 0)}只")
    lines.append("")
    
    # ── 主线维度拆解 ──
    theme_scores_seen = set()
    for day in daily:
        for ts in day.get("theme_scores", []):
            tname = ts["theme"]
            if tname not in theme_scores_seen:
                theme_scores_seen.add(tname)
    if theme_scores_seen:
        lines.append("### 主线评分维度拆解")
        latest_scores = {}
        for day in daily:
            for ts in day.get("theme_scores", []):
                tname = ts["theme"]
                if tname not in latest_scores:
                    latest_scores[tname] = ts
        lines.append("| 行业 | L1价格(30) | L2资金(25) | L3事件(20) | 总分 | 层级 | 未达Confirmed主因 |")
        lines.append("|------|-----------|-----------|-----------|------|------|-------------------|")
        for tname, ts in sorted(latest_scores.items(), key=lambda x: -x[1]["total_score"]):
            l1 = ts.get("layer1_price", 0)
            l2 = ts.get("layer2_flow", 0)
            l3 = ts.get("layer3_event", 0)
            cat = ts.get("category", "?")
            cat_emo = {"confirmed": "🔴", "potential": "🟡", "watch": "⚪"}.get(cat, "?")
            gap_reasons = []
            if cat != "confirmed":
                if l1 < 20: gap_reasons.append(f"价格动能偏低({l1}/30)")
                if l2 < 15: gap_reasons.append(f"资金流向不足({l2}/25)")
                if l3 < 10: gap_reasons.append(f"政策催化弱({l3}/20)")
            reason = "; ".join(gap_reasons) if gap_reasons else "-"
            lines.append(f"| {tname} | {l1} | {l2} | {l3} | {ts['total_score']} | {cat_emo}{cat} | {reason} |")
        lines.append("")
        lines.append("> 判定逻辑: classify_theme_v2 三层共振 → L1≥12 & L2≥10 & L3≥12 → confirmed")
        lines.append("> ✅ L2资金流已通过本地DB(北向+融资+主力)百分位校准, 支持confirmed判定")
        lines.append("> 阈值: L1≥12 & L2≥10 & L3≥12 → confirmed | 2层通过 → potential | 1层 → watch")
        lines.append("")
        lines.append("#### 评分推导说明")
        lines.append("- **L1价格动能(满30)**: 板块RS排名(行业相对强弱) + 5日/20日动量 + 涨停/上涨比例")
        lines.append("- **L2资金流向(满25)**: 北向资金(≥50亿=10分) + 主力资金 + 融资余额环比")
        lines.append("- **L3事件催化(满20)**: 涨停潮数量 + 48h内政策事件 + 研报覆盖 + 龙头股公告")
        lines.append("- **总分=L1+L2+L3+量价结构**: 量价结构=放量比例/均量比/RSI分化程度")
        lines.append("")
    
    # ── 按行业分层推荐 ──
    # 收集所有picks, 按theme分组
    theme_picks = defaultdict(list)
    for day in daily:
        for p in day.get("picks_detail", []):
            sym = p["symbol"]
            name = _resolve_name(conn, sym)
            entry_price = None
            # 查入场价
            price_r = conn.execute(
                "SELECT close FROM stock_daily_all WHERE symbol=? AND trade_date=?", 
                (sym, day["date"])
            ).fetchone()
            if price_r:
                entry_price = price_r[0]
            
            # T+5收益
            fwd = conn.execute(
                "SELECT close FROM stock_daily_all WHERE symbol=? AND trade_date>? ORDER BY trade_date LIMIT 5",
                (sym, day["date"])
            ).fetchall()
            t5_pnl = None
            if len(fwd) >= 5 and entry_price:
                t5_pnl = round((fwd[4][0] - entry_price) / entry_price * 100, 2)
            
            theme_picks[p["theme"]].append({
                "symbol": sym, "name": name, "tier": p["tier"], "score": p["score"],
                "date": day["date"], "entry": entry_price, "t5_pnl": t5_pnl,
                "category": theme_cats.get(p.get("theme",""), day.get("top_category", "?")),  # 主题自身category
            })
    
    tier_labels = {"leader": "🥇龙头", "weight": "🥈权重", "laggard": "🥉待涨"}
    tier_order = {"leader": 0, "weight": 1, "laggard": 2}
    
    for theme_name, picks in sorted(theme_picks.items()):
        cat = picks[0]["category"]
        theme_cat_emoji = cat_emoji_map.get(cat, "?")
        lines.append(f"### {theme_cat_emoji} {theme_name}")
        
        # 按个股聚合: 同一股票的多次推荐合并为一行汇总
        stock_map = defaultdict(lambda: {
            "tiers": [], "scores": [], "dates": [], "entries": [], "t5_pnls": [],
            "name": "", "symbol": ""
        })
        for p in picks:
            sym = p["symbol"]
            stock_map[sym]["symbol"] = sym
            stock_map[sym]["name"] = p["name"]
            stock_map[sym]["tiers"].append(p["tier"])
            stock_map[sym]["scores"].append(p["score"])
            stock_map[sym]["dates"].append(p["date"])
            if p["entry"]:
                stock_map[sym]["entries"].append(p["entry"])
            if p["t5_pnl"] is not None:
                stock_map[sym]["t5_pnls"].append(p["t5_pnl"])
        
        from collections import Counter as _Cnt
        lines.append("| 层级 | 代码 | 名称 | 推荐区间 | 入场价区间 | 推荐次数 | 均得分 | 均T+5 | 胜率 |")
        lines.append("|------|------|------|---------|-----------|---------|--------|-------|------|")
        
        stock_rows = []
        for sym, data in stock_map.items():
            if data["tiers"]:
                tier_counter = _Cnt(data["tiers"])
                main_tier = tier_counter.most_common(1)[0][0]
            else:
                main_tier = "weight"
            tier_lbl = tier_labels.get(main_tier, main_tier)
            
            dates_sorted = sorted(data["dates"])
            date_range = f"{dates_sorted[0][5:]}→{dates_sorted[-1][5:]}" if len(dates_sorted) > 1 else dates_sorted[0][5:]
            
            entries = data["entries"]
            price_range = f"{min(entries):.1f}~{max(entries):.1f}" if len(entries) > 1 else (f"{entries[0]:.1f}" if entries else "?")
            
            avg_score = round(sum(data["scores"]) / len(data["scores"]), 1) if data["scores"] else 0
            avg_t5 = round(sum(data["t5_pnls"]) / len(data["t5_pnls"]), 1) if data["t5_pnls"] else None
            count = len(data["scores"])
            wins = sum(1 for pnl in data["t5_pnls"] if pnl > 0)
            wr = f"{wins}/{len(data['t5_pnls'])}({wins/len(data['t5_pnls'])*100:.0f}%)" if data["t5_pnls"] else "-"
            
            stock_rows.append((tier_order.get(main_tier, 99), -avg_score, sym, tier_lbl, sym, data["name"],
                              date_range, price_range, count, avg_score, _fmt_pnl(avg_t5), wr))
        
        stock_rows.sort(key=lambda x: (x[0], x[1]))
        for _, _, sym, tier_lbl, _, name, date_range, price_range, count, avg_score, t5_str, wr in stock_rows:
            lines.append(f"| {tier_lbl} | {sym} | {name} | {date_range} | {price_range} | "
                        f"{count}次 | {avg_score} | {t5_str} | {wr} |")
        lines.append("")
    
    return "\n".join(lines)


# ══════════════════════════════════════════════════════
# Phase 2: 个股交易生命周期 + ASCII盈亏线图
# ══════════════════════════════════════════════════════

def track_trade_lifecycle(picks: dict, report_date: str,
                          market_regime: str = "SIDEWAYS") -> dict:
    """追踪每只推荐的完整生命周期: 入场信号检查→持仓→信号出场
    
    返回: {date, trades: [trade_dict, ...], summary: {total, signal_stats, ...}}
    """
    from nous.engine.backtest.signal_engine import (evaluate_buy_signal, evaluate_sell_signal,
                                             compute_position_size)
    
    conn = sqlite3.connect(str(DB))
    trades = []
    signal_stats = {"buy_rejected": 0, "ma_cross_buy": 0, "atr_stop": 0,
                    "ma_cross_sell": 0, "hard_stop": 0, "time_stop": 0, "expiry": 0}
    
    for market, periods in picks.items():
        for period, pool_picks in periods.items():
            pool_type = f"{market}_{period}"
            
            for p in pool_picks:
                sym = p["symbol"]
                entry_price = p.get("close")
                name = p.get("name", sym)
                
                if not entry_price:
                    trades.append({"symbol": sym, "name": name, "pool": pool_type,
                                   "error": "no entry price"})
                    continue
                
                # ── 入场信号检查 ──
                buy_sig = evaluate_buy_signal(sym, report_date, conn,
                                              pool_type, market_regime)
                if not buy_sig.approved:
                    signal_stats["buy_rejected"] += 1
                    trades.append({
                        "symbol": sym, "name": name, "pool": pool_type,
                        "entry_date": report_date, "entry_price": entry_price,
                        "error": f"买入信号拒绝: {buy_sig.reason}",
                        "buy_signal_reason": buy_sig.reason,
                    })
                    continue
                
                signal_stats["ma_cross_buy"] += 1
                
                # 获取forward数据
                fwd = conn.execute("""
                    SELECT trade_date, close FROM stock_daily_all 
                    WHERE symbol=? AND trade_date > ? 
                    ORDER BY trade_date LIMIT 30
                """, (sym, report_date)).fetchall()
                
                if not fwd:
                    trades.append({"symbol": sym, "name": name, "pool": pool_type,
                                   "error": "no forward data"})
                    continue
                
                # ── 逐日出场信号检查 ──
                is_long = period == "long"
                target_holding = 20 if is_long else 5
                highest_since_entry = entry_price
                exit_idx = None
                exit_reason = ""
                
                for i, (fwd_date, fwd_close) in enumerate(fwd):
                    # 更新最高价
                    if fwd_close > highest_since_entry:
                        highest_since_entry = fwd_close
                    
                    # 出场信号检查
                    sell_sig = evaluate_sell_signal(
                        sym, entry_price, report_date, fwd_date, conn,
                        pool_type, highest_since_entry
                    )
                    
                    if sell_sig.approved:
                        exit_idx = i
                        exit_reason = sell_sig.reason
                        if "ATR" in sell_sig.reason or "移动止盈" in sell_sig.reason:
                            signal_stats["atr_stop"] += 1
                        elif "MA死叉" in sell_sig.reason:
                            signal_stats["ma_cross_sell"] += 1
                        elif "硬止损" in sell_sig.reason:
                            signal_stats["hard_stop"] += 1
                        elif "时间止损" in sell_sig.reason:
                            signal_stats["time_stop"] += 1
                        break
                    
                    # 持有期满检查
                    if i >= target_holding - 1 and exit_idx is None:
                        exit_idx = i
                        exit_reason = f"持有期满(T+{target_holding})"
                        signal_stats["expiry"] += 1
                        break
                
                # 最终兜底: 无信号且无forward数据足够长
                if exit_idx is None:
                    exit_idx = min(len(fwd) - 1, target_holding - 1)
                    exit_reason = f"持有期满(T+{target_holding})(兜底)"
                    signal_stats["expiry"] += 1
                
                exit_price = fwd[exit_idx][1]
                exit_date = fwd[exit_idx][0]
                pnl_pct = round((exit_price - entry_price) / entry_price * 100, 2)
                holding_days = exit_idx + 1
                
                # 每日路径 + 最高价
                daily_path = [entry_price] + [row[1] for row in fwd[:exit_idx+1]]
                
                # T+N快照
                exits_snapshot = {}
                snap_points = [1, 5, 20] if is_long else [1, 3, 5]
                for lb in snap_points:
                    if len(fwd) >= lb:
                        exits_snapshot[f"t{lb}"] = {
                            "date": fwd[lb-1][0],
                            "close": fwd[lb-1][1],
                            "pnl_pct": round((fwd[lb-1][1] - entry_price) / entry_price * 100, 2)
                        }
                
                # 仓位计算
                position_pct = compute_position_size(sym, report_date, conn, pool_type)
                
                trades.append({
                    "symbol": sym, "name": name, "pool": pool_type,
                    "entry_date": report_date, "entry_price": entry_price,
                    "exit_date": exit_date, "exit_price": exit_price,
                    "exit_reason": exit_reason, "holding_days": holding_days,
                    "pnl_pct": pnl_pct, "daily_path": daily_path,
                    "exits_snapshot": exits_snapshot,
                    "buy_signal_score": buy_sig.score,
                    "position_pct": position_pct,
                    "buy_signal_detail": buy_sig.details,
                })
    
    conn.close()
    
    valid = [t for t in trades if "error" not in t]
    
    return {
        "date": report_date,
        "trades": trades,
        "signal_stats": signal_stats,
        "summary": {
            "total": len(trades),
            "valid": len(valid),
            "avg_holding": round(sum(t["holding_days"] for t in valid)/len(valid), 1) if valid else 0,
            "avg_pnl": round(sum(t["pnl_pct"] for t in valid)/len(valid), 2) if valid else 0,
        }
    }



def generate_per_stock_ascii(trade: dict) -> str:
    """为单只标的生成ASCII盈亏线图
    
    示例输出:
      002371 北方华创 A_long  入场465.80 → 出场447.42  -4.0%  🔴止损(5天)
        465.80 ●
        460.21 │ █
        454.62 │ ██
        448.80 │ ███            ← 止损触发
        443.01 │ ████ ○
    """
    if "error" in trade:
        return f"  {trade['symbol']} {trade.get('name','?')}: {trade['error']}"
    
    path = trade.get("daily_path", [])
    if len(path) < 2:
        return f"  {trade['symbol']} {trade.get('name','?')}: 数据不足"
    
    entry = path[0]
    exit_p = trade["exit_price"]
    pnl = trade["pnl_pct"]
    reason = trade["exit_reason"]
    days = trade["holding_days"]
    
    emoji = "🔴止损" if reason == "stop_loss" else f"📅{reason}"
    title = (f"  {trade['symbol']} {trade.get('name','?')} {trade.get('pool','')}  "
             f"入场{entry} → 出场{exit_p}  {_fmt_pnl(pnl)}  {emoji}({days}天)")
    lines = [title]
    
    # 确定柱状图范围
    prices = path + [exit_p]
    min_p, max_p = min(prices), max(prices)
    price_range = max_p - min_p
    if price_range == 0:
        price_range = entry * 0.01  # 防除零
    
    bar_width = 30  # 柱状图最大宽度
    
    for i, p in enumerate(path):
        bar_len = int(abs(p - min_p) / price_range * bar_width)
        bar = "█" * bar_len
        marker = "●" if i == 0 else ("○" if i == len(path)-1 else "│")
        stop_mark = " ← 止损触发" if (reason == "stop_loss" and i == len(path)-1) else ""
        lines.append(f"    {p:.2f} {marker} {bar}{stop_mark}")
    
    return "\n".join(lines)


def generate_stop_loss_stats(all_trades: list[dict]) -> str:
    """生成止损统计章节"""
    valid = [t for t in all_trades if "error" not in t]
    stopped = [t for t in valid if t["exit_reason"] == "stop_loss"]
    
    if not stopped:
        return "## 止损分析\n\n*无止损触发*\n"
    
    # 止损后反弹: 检查止损出场后5日内是否回到入场价以上
    bounced = 0
    conn = sqlite3.connect(str(DB))
    for t in stopped:
        fwd = conn.execute(
            "SELECT close FROM stock_daily_all WHERE symbol=? AND trade_date>? ORDER BY trade_date LIMIT 5",
            (t["symbol"], t["exit_date"])
        ).fetchall()
        if fwd and any(row[0] >= t["entry_price"] for row in fwd):
            bounced += 1
    conn.close()
    
    avg_holding = sum(t["holding_days"] for t in stopped) / len(stopped) if stopped else 0
    avg_sl_pnl = sum(t["pnl_pct"] for t in stopped) / len(stopped) if stopped else 0
    
    lines = [
        "## 止损分析",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 止损触发率 | {len(stopped)}/{len(valid)} ({len(stopped)/len(valid)*100:.0f}%) |",
        f"| 平均持仓天数 | {avg_holding:.1f}天 |",
        f"| 平均止损亏损 | {avg_sl_pnl:.1f}% |",
        f"| 止损后5日反弹率 | {bounced}/{len(stopped)} ({bounced/len(stopped)*100:.0f}%) |",
        "",
        "### 止损明细",
        "| 代码 | 名称 | 入场 | 出场 | 亏损 | 持仓天 |",
        "|------|------|------|------|------|--------|",
    ]
    for t in stopped:
        lines.append(
            f"| {t['symbol']} | {t.get('name','?')} | {t['entry_price']} | "
            f"{t['exit_price']} | {_fmt_pnl(t['pnl_pct'])} | {t['holding_days']} |"
        )
    
    return "\n".join(lines)


def generate_consecutive_stats(all_trades: list[dict]) -> str:
    """生成最大连续盈亏序列统计"""
    valid = sorted(
        [t for t in all_trades if "error" not in t],
        key=lambda x: x["entry_date"]
    )
    if not valid:
        return ""
    
    # 计算连续盈亏
    max_win_streak = cur_win = 0
    max_loss_streak = cur_loss = 0
    win_pnl_sum = loss_pnl_sum = 0
    best_streak_pnl = 0
    worst_streak_pnl = 0
    
    for t in valid:
        if t["pnl_pct"] > 0:
            cur_win += 1
            cur_loss = 0
            win_pnl_sum += t["pnl_pct"]
            max_win_streak = max(max_win_streak, cur_win)
            best_streak_pnl = max(best_streak_pnl, win_pnl_sum)
        else:
            cur_loss += 1
            cur_win = 0
            win_pnl_sum = 0
            loss_pnl_sum += t["pnl_pct"]
            max_loss_streak = max(max_loss_streak, cur_loss)
            worst_streak_pnl = min(worst_streak_pnl, loss_pnl_sum)
    
    wins = sum(1 for t in valid if t["pnl_pct"] > 0)
    total = len(valid)
    
    return "\n".join([
        "## 韧性指标",
        f"- 胜率: {wins}/{total} ({wins/total*100:.1f}%)" if total else "- 胜率: N/A",
        f"- 最大连胜: {max_win_streak}连赢 (累计+{best_streak_pnl:.1f}%)" if max_win_streak else "",
        f"- 最大连败: {max_loss_streak}连败 (累计{worst_streak_pnl:.1f}%)" if max_loss_streak else "",
        f"- 平均盈亏: {sum(t['pnl_pct'] for t in valid)/total:+.2f}%" if total else "",
        "",
    ])


# ══════════════════════════════════════════════════════
# F3 报告生成 (保留原有逻辑)
# ══════════════════════════════════════════════════════

def generate_recommendation_report(picks: dict, meta: dict, report_date: str) -> str:
    """盘前荐股报告 → MD (F3海鹰)"""
    lines = [f"# 每日荐股 {report_date}", ""]
    lines.append(f"## 市场体制: {meta.get('regime','?')} | RSI14={meta.get('rsi14','?')} | 日涨跌={meta.get('daily_change','?')}%")
    lines.append("")
    
    if meta.get('is_bull_trap'):
        lines.append("⚡ **BULL_TRAP 熊市反弹信号** — 短线池开放")
        lines.append("")
    
    for label, market, period in [("A股 长线", "A", "long"), ("A股 短线", "A", "short"),
                                     ("港股 长线", "HK", "long"), ("港股 短线", "HK", "short")]:
        pool_picks = picks.get(market, {}).get(period, [])
        lines.append(f"### {label} ({len(pool_picks)}只)")
        if not pool_picks:
            lines.append("*无推荐（市场体制跳过或数据不足）*")
            lines.append("")
            continue
        
        lines.append("| 代码 | 名称 | 行业 | PE | ATR% | 止损 | 仓位% |")
        lines.append("|------|------|------|-----|------|------|-------|")
        for p in pool_picks:
            pe_str = f"{p.get('pe','?')}" if p.get('pe') else "?"
            atr_str = f"{p.get('atr_atr_pct','?')}%" if p.get('atr_atr_pct') else "?"
            sl_str = f"{p.get('stop_loss_price','?')}" if p.get('stop_loss_price') else "?"
            pos_str = f"{p.get('position_pct',0)*100:.0f}%"
            lines.append(f"| {p['symbol']} | {p.get('name','?')} | {p.get('industry','?')} | {pe_str} | {atr_str} | {sl_str} | {pos_str} |")
        lines.append("")
    
    # 数据质量
    missing_pe = []
    missing_ind = []
    for market_pools in picks.values():
        for period_picks in market_pools.values():
            for p in period_picks:
                if not p.get('pe'): missing_pe.append(p['symbol'])
                if not p.get('industry') or p.get('industry') == 'unknown': missing_ind.append(p['symbol'])
    
    if missing_pe or missing_ind:
        lines.append("## 数据质量")
        if missing_pe:
            lines.append(f"- ⚠️ PE缺失: {', '.join(missing_pe[:10])}")
        if missing_ind:
            lines.append(f"- ⚠️ 行业缺失: {', '.join(missing_ind[:10])}")
        lines.append("")
    
    path = REPORT_DIR / f"{report_date}-recommendations.md"
    content = "\n".join(lines)
    path.write_text(content)
    return str(path)


def generate_review_report(lifecycle: dict, meta: dict, report_date: str) -> str:
    """盘后复盘报告 → MD，含逐日盈亏+个股ASCII图+止损统计+韧性指标"""
    lines = [f"# 荐股复盘 {report_date}", ""]
    lines.append(f"## 市场: {meta.get('regime','?')} | 模拟盘基准100万")
    lines.append("")
    
    trades = lifecycle.get("trades", [])
    valid = [t for t in trades if "error" not in t]
    
    if not valid:
        lines.append("*无有效交易*")
        path = REPORT_DIR / f"{report_date}-review.md"
        path.write_text("\n".join(lines))
        return str(path)
    
    # ── T+1/3/5 快照表 ──
    lines.append("### 逐日快照")
    # 自适应表头: 长线展示T+20, 短线展示T+5
    sample_trade = valid[0] if valid else {}
    is_long_pool = "long" in sample_trade.get("pool", "")
    if is_long_pool:
        lines.append("| 代码 | 名称 | 池 | 入场 | T+1 | T+5 | T+20 | 出场 |")
        lines.append("|------|------|-----|------|------|------|------|------|")
    else:
        lines.append("| 代码 | 名称 | 池 | 入场 | T+1 | T+3 | T+5 | 出场 |")
    for t in valid:
        snap = t.get("exits_snapshot", {})
        t1 = snap.get("t1", {}).get("pnl_pct")
        t3 = snap.get("t3", {}).get("pnl_pct")
        t5 = snap.get("t5", {}).get("pnl_pct")
        t20 = snap.get("t20", {}).get("pnl_pct")
        exit_icon = '🔴' if 'stop_loss' in str(t.get('exit_reason','')) else ''
        exit_str = f"{_fmt_pnl(t['pnl_pct'])} {exit_icon}"
        if is_long_pool:
            cols = [t['symbol'], t.get('name','?'), t.get('pool','?'), str(t['entry_price']),
                    _fmt_pnl(t1), _fmt_pnl(t5), _fmt_pnl(t20), exit_str]
        else:
            cols = [t['symbol'], t.get('name','?'), t.get('pool','?'), str(t['entry_price']),
                    _fmt_pnl(t1), _fmt_pnl(t3), _fmt_pnl(t5), exit_str]
        lines.append("| " + " | ".join(str(c) for c in cols) + " |")
    lines.append("")
    
    # ── 个股ASCII盈亏线图 ──
    lines.append("### 个股盈亏线图")
    lines.append("")
    for t in valid[:15]:  # 最多展示15只
        lines.append(generate_per_stock_ascii(t))
        lines.append("")
    if len(valid) > 15:
        lines.append(f"*...还有 {len(valid)-15} 只未展示*")
        lines.append("")
    
    # ── 止损统计 ──
    lines.append(generate_stop_loss_stats(trades))
    lines.append("")
    
    # ── 韧性指标 ──
    lines.append(generate_consecutive_stats(trades))
    
    # ── 累计PnL ├──
    lines.append("## 累计PnL曲线")
    lines.append("```")
    cum = 0
    for i, t in enumerate(valid):
        cum += t["pnl_pct"]
        bar_len = min(int(abs(t["pnl_pct"]) / 3), 20)
        bar = "█" * bar_len
        sign = "+" if t["pnl_pct"] >= 0 else "-"
        lines.append(f"  {i+1:2d}. [{sign}] {bar} {t['pnl_pct']:+.1f}%  cum:{cum:+.1f}%  {t['symbol']}")
    lines.append("```")
    lines.append("")
    
    path = REPORT_DIR / f"{report_date}-review.md"
    content = "\n".join(lines)
    path.write_text(content)
    return str(path)


# ══════════════════════════════════════════════════════
# 双轨合并报告 + 质量门禁
# ══════════════════════════════════════════════════════

def generate_dual_report(f3_intervals: list[dict], trl_jsons: list[dict], 
                         llm_section: str = "", as_of: str = "") -> str:
    """合并F3+TRL回测结果为统一双轨报告"""
    lines = [f"# 双引擎回测报告", ""]
    if as_of:
        lines.append(f"> 生成时间: {as_of}")
    
    # ── 窗口汇总表 ──
    lines.append("## 窗口汇总")
    lines.append("| 窗口 | 区间 | F3推荐 | F3胜率 | TRL推荐 | TRL胜率 |")
    lines.append("|------|------|--------|--------|---------|--------|")
    
    for i, (f3_data, trl_data) in enumerate(zip(f3_intervals, trl_jsons)):
        label = f3_data.get("label", f"窗口{i+1}")
        interval = f3_data.get("interval", "?")
        # F3胜率: 从trades中计算
        f3_trades = f3_data.get("trades", [])
        f3_valid = [t for t in f3_trades if "error" not in t]
        f3_wins = sum(1 for t in f3_valid if t.get("pnl_pct", 0) > 0)
        f3_rate = f"{f3_wins/len(f3_valid)*100:.0f}%" if f3_valid else "N/A"
        
        # TRL胜率
        trl_trades = trl_data.get("trades", [])
        trl_valid = [t for t in trl_trades if "error" not in t]
        trl_wins = sum(1 for t in trl_valid if t.get("pnl_pct", 0) > 0)
        trl_rate = f"{trl_wins/len(trl_valid)*100:.0f}%" if trl_valid else "N/A"
        
        lines.append(
            f"| {label} | {interval} | {len(f3_valid)} | {f3_rate} | "
            f"{len(trl_valid)} | {trl_rate} |"
        )
    lines.append("")
    
    # ── 置顶摘要 ──
    all_f3 = []
    for fd in f3_intervals:
        all_f3.extend([t for t in fd.get("trades", []) if "error" not in t])
    all_trl = []
    for td in trl_jsons:
        all_trl.extend([t for t in td.get("trades", []) if "error" not in t])
    
    if all_f3 or all_trl:
        f3_avg = sum(t["pnl_pct"] for t in all_f3)/len(all_f3) if all_f3 else 0
        trl_avg = sum(t["pnl_pct"] for t in all_trl)/len(all_trl) if all_trl else 0
        f3_win = sum(1 for t in all_f3 if t["pnl_pct"]>0)/len(all_f3)*100 if all_f3 else 0
        trl_win = sum(1 for t in all_trl if t["pnl_pct"]>0)/len(all_trl)*100 if all_trl else 0
        
        lines.append("## 📌 摘要")
        lines.append(f"| | 🦅 F3海鹰 | 🐉 TRL龙脉 |")
        lines.append(f"|--|---------|----------|")
        lines.append(f"| 有效交易 | {len(all_f3)} | {len(all_trl)} |")
        lines.append(f"| 胜率 | {f3_win:.0f}% | {trl_win:.0f}% |")
        lines.append(f"| 均收益 | {f3_avg:+.1f}% | {trl_avg:+.1f}% |")
        lines.append(f"| 🏆优胜 | {'✅' if f3_avg>trl_avg else ''} | {'✅' if trl_avg>f3_avg else ''} |")
        
        # 信号过滤
        all_ss = {"buy_rejected":0,"ma_cross_buy":0}
        for fd in f3_intervals:
            ss = fd.get("signal_stats", {})
            for k in all_ss: all_ss[k] += ss.get(k,0)
        for td in trl_jsons:
            ss = td.get("signal_stats", {})
            for k in all_ss: all_ss[k] += ss.get(k,0)
        total_signals = all_ss["buy_rejected"] + all_ss["ma_cross_buy"]
        if total_signals > 0:
            lines.append(f"| 信号过滤 | {all_ss['ma_cross_buy']}/{total_signals}通过 ({all_ss['ma_cross_buy']/total_signals*100:.0f}%) |")
        lines.append("")
    
    # ── TRL主线分析 ──
    conn = sqlite3.connect(str(DB))
    for i, trl_data in enumerate(trl_jsons):
        lines.append(f"### 🐉 龙脉TRL — 窗口{i+1}")
        lines.append(generate_trl_theme_report(trl_data, conn))
    conn.close()
    
    # ── F3逐日明细: 按四池分组 ──
    pool_map = {"A_long": "🇦 A股长线", "A_short": "🇦 A股短线", "HK_long": "🇭🇰 港股长线", "HK_short": "🇭🇰 港股短线"}
    for i, f3_data in enumerate(f3_intervals):
        lines.append(f"### 🦅 海鹰F3 — 窗口{i+1}")
        trades = f3_data.get("trades", [])
        valid = [t for t in trades if "error" not in t]
        
        # Re-resolve HK names
        conn2 = sqlite3.connect(str(DB))
        for t in valid:
            if t.get("name") and t["name"] == t["symbol"]:
                t["name"] = _resolve_name(conn2, t["symbol"])
        conn2.close()
        
        by_pool = defaultdict(list)
        for t in valid:
            pool = t.get("pool", "unknown")
            by_pool[pool].append(t)
        
        for pool_key in ["A_long", "A_short", "HK_long", "HK_short"]:
            pool_trades = by_pool.get(pool_key, [])
            if not pool_trades:
                continue
            pool_label = pool_map.get(pool_key, pool_key)
            wins = sum(1 for t in pool_trades if t.get("pnl_pct", 0) > 0)
            avg = sum(t["pnl_pct"] for t in pool_trades) / len(pool_trades)
            best_t = max(pool_trades, key=lambda t: t.get("pnl_pct", -999))
            worst_t = min(pool_trades, key=lambda t: t.get("pnl_pct", 999))
            
            lines.append(f"#### {pool_label} ({len(pool_trades)}笔)")
            lines.append(f"> 胜率{wins}/{len(pool_trades)}({wins/len(pool_trades)*100:.0f}%) | "
                        f"均{avg:+.1f}% | 最佳{best_t.get('name','?')}{best_t['pnl_pct']:+.1f}% | "
                        f"最差{worst_t.get('name','?')}{worst_t['pnl_pct']:.1f}%")
            lines.append("")
            
            # ── 按个股聚合 ──
            from collections import Counter as _Counter
            stock_map = defaultdict(lambda: {"trades": [], "name": ""})
            for t in pool_trades:
                sym = t["symbol"]
                stock_map[sym]["symbol"] = sym
                stock_map[sym]["name"] = t.get("name", sym)
                stock_map[sym]["trades"].append(t)
            
            lines.append("| 代码 | 名称 | 交易次数 | 胜率 | 均盈亏 | 总盈亏 | 均持仓 | 最佳 | 最差 |")
            lines.append("|------|------|---------|------|--------|--------|--------|------|------|")
            
            # Sort by total P&L desc
            stock_summaries = []
            for sym, data in stock_map.items():
                ts = data["trades"]
                n = len(ts)
                w = sum(1 for t in ts if t.get("pnl_pct", 0) > 0)
                avg_pnl = sum(t["pnl_pct"] for t in ts) / n
                # 总盈亏: 本金加权 (不可简单sum百分比)
                # 位置计算引擎可能返回过小值, clamp到3%下限
                def _clamp_pos(pct):
                    return max(pct, 3.0) if pct and pct > 0 else 10.0
                total_cap = sum(1_000_000 * _clamp_pos(t.get("position_pct", 10)) / 100 for t in ts)
                total_profit = sum(1_000_000 * _clamp_pos(t.get("position_pct", 10)) / 100 * t["pnl_pct"] / 100 for t in ts)
                total_pnl = total_profit / total_cap * 100 if total_cap else 0
                avg_hold = sum(t.get("holding_days", 0) for t in ts) / n
                best = max(ts, key=lambda t: t.get("pnl_pct", -999))
                worst = min(ts, key=lambda t: t.get("pnl_pct", 999))
                stock_summaries.append((sym, data["name"], n, w, avg_pnl, total_pnl, avg_hold, best, worst))
            
            stock_summaries.sort(key=lambda x: -x[5])  # by total P&L
            
            for sym, name, n, w, avg_pnl, total_pnl, avg_hold, best, worst in stock_summaries[:15]:
                wr = f"{w}/{n}({w/n*100:.0f}%)" if n else "-"
                lines.append(f"| {sym} | {name} | {n}次 | {wr} | {avg_pnl:+.1f}% | {total_pnl:+.1f}% | "
                           f"{avg_hold:.0f}天 | {best['pnl_pct']:+.1f}% | {worst['pnl_pct']:+.1f}% |")
            if len(stock_summaries) > 15:
                lines.append(f"| ... | 还有{len(stock_summaries)-15}只 | | | | | | | |")
            lines.append("")
    
    # ── 共识票 ──
    lines.append("## ⚡ 共识票 (双引擎同时推荐)")
    # 交叉匹配: 同日期+同代码 同时被F3和TRL推荐
    f3_by_date = defaultdict(set)
    for f3_data in f3_intervals:
        for t in f3_data.get("trades", []):
            if "error" not in t:
                f3_by_date[t["entry_date"]].add(t["symbol"])
    trl_by_date = defaultdict(set)
    for trl_data in trl_jsons:
        for t in trl_data.get("trades", []):
            if "error" not in t:
                trl_by_date[t["entry_date"]].add(t["symbol"])
    
    consensus = []
    for d in sorted(set(list(f3_by_date.keys()) + list(trl_by_date.keys()))):
        common = f3_by_date.get(d, set()) & trl_by_date.get(d, set())
        for sym in common:
            # Get name from either side
            f3_t = next((t for t in sum([fd.get("trades",[]) for fd in f3_intervals], []) 
                        if t.get("symbol")==sym and t.get("entry_date")==d), None)
            trl_t = next((t for t in sum([td.get("trades",[]) for td in trl_jsons], [])
                         if t.get("symbol")==sym and t.get("entry_date")==d), None)
            name = (f3_t or trl_t or {}).get("name", sym)
            consensus.append({"date": d, "symbol": sym, "name": name})
    
    if consensus:
        lines.append(f"| 日期 | 代码 | 名称 |")
        lines.append(f"|------|------|------|")
        for c in consensus[:20]:
            lines.append(f"| {c['date']} | {c['symbol']} | {c.get('name','?')} |")
        lines.append(f"")
        lines.append(f"> 共{len(consensus)}只共识票 (双引擎同日推荐)")
    else:
        lines.append("*本区间无双引擎共识票*")
    lines.append("")
    
    # ── 个股盈亏瀑布图 (Phase B) ──
    lines.append(generate_stock_waterfall(f3_intervals, trl_jsons))
    
    # ── 板块收益热力图 (Phase B) ──
    lines.append(generate_sector_heatmap(f3_intervals, trl_jsons))
    
    # ── 回测总结 (Phase C) ──
    lines.append(generate_postmortem(f3_intervals, trl_jsons))
    
    # ── LLM解读 ──
    if llm_section:
        lines.append("## 🧠 LLM解读")
        lines.append(llm_section)
        lines.append("")
    
    # 用第一个窗口的区间日期命名, 不用today避免覆盖
    interval_tag = f3_intervals[0].get("interval", as_of) if f3_intervals else as_of
    path = REPORT_DIR / "dual_random" / f"backtest_report_{interval_tag}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines)
    path.write_text(content)
    return str(path)


def validate_backtest_quality(f3_intervals: list[dict], trl_jsons: list[dict]) -> list[str]:
    """回测质量门禁: 返回不通过项列表, 空=通过"""
    failures = []
    
    # 窗口数 ≥ 2
    if len(f3_intervals) < 2:
        failures.append("窗口数不足(>=2), 当前{}".format(len(f3_intervals)))
    
    for i, (f3_data, trl_data) in enumerate(zip(f3_intervals, trl_jsons)):
        label = f"窗口{i+1}"
        
        # F3推荐数 ≥ 20
        f3_trades = f3_data.get("trades", [])
        f3_valid = [t for t in f3_trades if "error" not in t]
        if len(f3_valid) < 20:
            failures.append(f"{label} F3推荐数不足({len(f3_valid)}<20)")
        
        # TRL推荐数 ≥ 10
        trl_trades = trl_data.get("trades", [])
        trl_valid = [t for t in trl_trades if "error" not in t]
        if len(trl_valid) < 10:
            failures.append(f"{label} TRL推荐数不足({len(trl_valid)}<10)")
    
    return failures


def generate_stock_waterfall(f3_intervals: list[dict], trl_jsons: list[dict]) -> str:
    """个股盈亏瀑布图 (AQR style): 按F3+TRL合并, 排序展示最佳/最差"""
    all_trades = []
    for f3_data in f3_intervals:
        for t in f3_data.get("trades", []):
            if "error" not in t:
                t["engine"] = "F3"
                all_trades.append(t)
    for trl_data in trl_jsons:
        for t in trl_data.get("trades", []):
            if "error" not in t:
                t["engine"] = "TRL"
                all_trades.append(t)
    
    if not all_trades:
        return "## 📊 个股盈亏瀑布图\n\n*无数据*\n"
    
    # Re-resolve HK names in context
    conn = sqlite3.connect(str(DB))
    for t in all_trades:
        if t.get("name") and t["name"] == t["symbol"]:  # HK name not resolved
            t["name"] = _resolve_name(conn, t["symbol"])
    conn.close()
    
    trades_sorted = sorted(all_trades, key=lambda t: t.get("pnl_pct", 0), reverse=True)
    max_pnl = max(abs(t["pnl_pct"]) for t in trades_sorted) or 1
    
    lines = ["## 📊 个股盈亏瀑布图 (F3+TRL合并)", ""]
    
    # Top 10 best
    lines.append("### 🏆 最佳10笔")
    for t in trades_sorted[:10]:
        bar_len = int(t["pnl_pct"] / max_pnl * 30) if t["pnl_pct"] > 0 else 0
        bar = "█" * bar_len
        lines.append(
            f"  [{t['engine']}] {t['symbol']} {t.get('name','?')}  "
            f"{_fmt_pnl(t['pnl_pct']):>8s}  {bar}"
        )
    
    # Bottom 10 worst  
    lines.append("")
    lines.append("### 📉 最差10笔")
    for t in trades_sorted[-10:]:
        bar_len = int(abs(t["pnl_pct"]) / max_pnl * 30) if t["pnl_pct"] < 0 else 0
        bar = "█" * bar_len
        lines.append(
            f"  [{t['engine']}] {t['symbol']} {t.get('name','?')}  "
            f"{_fmt_pnl(t['pnl_pct']):>8s}  {bar}"
        )
    
    # Summary
    all_pnl = [t["pnl_pct"] for t in trades_sorted]
    avg = sum(all_pnl) / len(all_pnl) if all_pnl else 0
    lines.append("")
    lines.append(f"> 总计{len(trades_sorted)}笔 | 均{avg:+.1f}% | "
                 f"范围{min(all_pnl):+.1f}%~{max(all_pnl):+.1f}%")
    lines.append("")
    
    return "\n".join(lines)


def generate_sector_heatmap(f3_intervals: list[dict], trl_jsons: list[dict]) -> str:
    """板块收益热力图 (九坤style): 按引擎×板块展示"""
    from collections import defaultdict
    
    sector_stats = defaultdict(lambda: {"F3_count": 0, "F3_pnl": 0, "TRL_count": 0, "TRL_pnl": 0})
    
    for f3_data in f3_intervals:
        for t in f3_data.get("trades", []):
            if "error" in t: continue
            # Determine sector from pool name or theme
            pool = t.get("pool", "unknown")
            sector = "unknown"
            if "A_" in pool: sector = "A股通用"
            if "HK_" in pool: sector = "港股通用"
            # Try to infer from theme
            if t.get("theme"):
                sector = t["theme"]
            elif t.get("name"):
                sector = pool  # fallback
            sector_stats[sector]["F3_count"] += 1
            sector_stats[sector]["F3_pnl"] += t.get("pnl_pct", 0)
    
    for trl_data in trl_jsons:
        for t in trl_data.get("trades", []):
            if "error" in t: continue
            sector = t.get("theme", "unknown")
            sector_stats[sector]["TRL_count"] += 1
            sector_stats[sector]["TRL_pnl"] += t.get("pnl_pct", 0)
    
    if not sector_stats:
        return "## 🔥 板块收益热力图\n\n*无数据*\n"
    
    lines = ["## 🔥 板块收益热力图", ""]
    lines.append("| 板块 | F3推荐 | F3均收益 | TRL推荐 | TRL均收益 |")
    lines.append("|------|--------|---------|---------|----------|")
    
    for sector, stats in sorted(sector_stats.items()):
        f3_avg = f"{stats['F3_pnl']/stats['F3_count']:+.1f}%" if stats['F3_count'] else "-"
        trl_avg = f"{stats['TRL_pnl']/stats['TRL_count']:+.1f}%" if stats['TRL_count'] else "-"
        lines.append(
            f"| {sector} | {stats['F3_count']} | {f3_avg} | "
            f"{stats['TRL_count']} | {trl_avg} |"
        )
    lines.append("")
    
    return "\n".join(lines)


def generate_postmortem(f3_intervals: list[dict], trl_jsons: list[dict]) -> str:
    """回测总结: 双引擎绩效卡片 + 体制分解 + 行动建议 + 资金汇总"""
    lines = ["## 📊 回测总结", ""]
    
    f3_trades = []
    for f3_data in f3_intervals:
        f3_trades.extend([t for t in f3_data.get("trades", []) if "error" not in t])
    trl_trades = []
    for trl_data in trl_jsons:
        trl_trades.extend([t for t in trl_data.get("trades", []) if "error" not in t])
    
    # Re-resolve HK names
    conn2 = sqlite3.connect(str(DB))
    for t in f3_trades + trl_trades:
        if t.get("name") and t["name"] == t["symbol"]:
            t["name"] = _resolve_name(conn2, t["symbol"])
    conn2.close()
    
    def engine_scorecard(trades, name):
        valid = [t for t in trades if "error" not in t]
        if not valid:
            return [f"### {name}", "", "*无有效交易*", ""]
        wins = sum(1 for t in valid if t.get("pnl_pct", 0) > 0)
        avg_pnl = sum(t["pnl_pct"] for t in valid) / len(valid)
        stops = sum(1 for t in valid if t.get("exit_reason") == "stop_loss")
        best = max(valid, key=lambda t: t.get("pnl_pct", -999))
        worst = min(valid, key=lambda t: t.get("pnl_pct", 999))
        
        win_rate = f"{wins/len(valid)*100:.0f}%"
        win_rating = "🟢" if wins/len(valid) > 0.6 else ("🟡" if wins/len(valid) > 0.4 else "🔴")
        pnl_rating = "🟢" if avg_pnl > 2 else ("🟡" if avg_pnl > -2 else "🔴")
        stop_rating = "✅" if stops/len(valid) < 0.2 else ("⚠️" if stops/len(valid) < 0.4 else "🔴")
        
        return [
            f"### {name}",
            "| 指标 | 值 | 评级 |",
            "|------|-----|------|",
            f"| 有效交易 | {len(valid)}笔 | - |",
            f"| 胜率 | {wins}/{len(valid)} ({win_rate}) | {win_rating} |",
            f"| 均收益 | {avg_pnl:+.1f}% | {pnl_rating} |",
            f"| 最佳单票 | {best['pnl_pct']:+.1f}% ({best.get('name','?')}) | - |",
            f"| 最差单票 | {worst['pnl_pct']:+.1f}% ({worst.get('name','?')}) | - |",
            f"| 止损率 | {stops}/{len(valid)} ({stops/len(valid)*100:.0f}%) | {stop_rating} |",
            "",
        ]
    
    lines.extend(engine_scorecard(f3_trades, "🦅 海鹰F3"))
    lines.extend(engine_scorecard(trl_trades, "🐉 龙脉TRL"))
    
    f3_valid = [t for t in f3_trades if "error" not in t]
    trl_valid = [t for t in trl_trades if "error" not in t]
    if f3_valid and trl_valid:
        f3_avg = sum(t["pnl_pct"] for t in f3_valid) / len(f3_valid)
        trl_avg = sum(t["pnl_pct"] for t in trl_valid) / len(trl_valid)
        winner = "TRL龙脉" if trl_avg > f3_avg else ("F3海鹰" if f3_avg > trl_avg else "平手")
        lines.append("### ⚔️ 双引擎对比")
        lines.append("| 对比维度 | F3海鹰 | TRL龙脉 |")
        lines.append("|---------|--------|---------|")
        lines.append(f"| 有效交易 | {len(f3_valid)} | {len(trl_valid)} |")
        lines.append(f"| 均收益 | {f3_avg:+.1f}% | {trl_avg:+.1f}% |")
        lines.append(f"| 此区间优胜 | {'✅' if f3_avg > trl_avg else ''} | {'✅' if trl_avg > f3_avg else ''} |")
        lines.append("")
        lines.append(f"> 🏆 此区间优胜引擎: **{winner}**")
        lines.append("")
    
    for i, trl_data in enumerate(trl_jsons):
        confirmed = trl_data.get("days_with_confirmed", 0)
        total = trl_data.get("total_days", 0)
        lines.append(f"### 🐉 主线统计 — 窗口{i+1}")
        lines.append(f"- 主线确认: {confirmed}/{total}天")
        if confirmed == 0:
            lines.append(f"- ⚠️ **0天主线确认但仍推{trl_data.get('total_picks',0)}只(potential主线)**")
            lines.append("  → 建议: 回测对比 '只推confirmed' vs 'confirmed+potential' 两种策略")
        lines.append("")
    
    # ── 信号统计 ── (moved before money)
    all_signal_stats2 = {"buy_rejected": 0, "ma_cross_buy": 0, "atr_stop": 0,
                        "ma_cross_sell": 0, "hard_stop": 0, "time_stop": 0, "expiry": 0}
    for f3_data in f3_intervals:
        ss = f3_data.get("signal_stats", {})
        for k in all_signal_stats2: all_signal_stats2[k] += ss.get(k, 0)
    for trl_data in trl_jsons:
        ss = trl_data.get("signal_stats", {})
        for k in all_signal_stats2: all_signal_stats2[k] += ss.get(k, 0)
    total_checks2 = sum(all_signal_stats2.values())
    if total_checks2 > 0:
        lines.append("### 📡 信号统计")
        lines.append("| 信号类型 | 触发 | 说明 |")
        lines.append("|---------|------|------|")
        lines.append(f"| 🟢 MA金叉买入 | {all_signal_stats2['ma_cross_buy']} | MA5>MA20 + 量比>1.2 + RSI<75 |")
        lines.append(f"| 🔴 买入拒绝 | {all_signal_stats2['buy_rejected']} | 信号触发前放弃 |")
        lines.append(f"| 📅 持有期满 | {all_signal_stats2['expiry']} | T+20(长)/T+5(短)正常到期 |")
        lines.append(f"| 🔻 ATR移动止盈 | {all_signal_stats2['atr_stop']} | 最高价回撤>2×ATR |")
        lines.append(f"| 📉 MA死叉卖出 | {all_signal_stats2['ma_cross_sell']} | MA5<MA20 且亏损 |")
        lines.append(f"| 🛑 硬止损 | {all_signal_stats2['hard_stop']} | 跌>7%(长)/5%(短) |")
        lines.append(f"| ⏰ 时间止损 | {all_signal_stats2['time_stop']} | 长线>30天且亏>5% |")
        lines.append("")

    # ── 资金汇总 (双引擎独立100万) ──
    lines.append("### 💰 资金汇总 (各100万独立运作)")
    lines.append("")
    
    def engine_money(trades, name, capital=1000000):
        valid = [t for t in trades if "error" not in t]
        if not valid:
            return [f"**{name}**: 无有效交易", ""]
        count = len(valid)
        per_trade = capital / count
        total_pnl_pct = sum(t["pnl_pct"] for t in valid)
        pnl_amt = total_pnl_pct / 100 * per_trade
        final_val = capital + pnl_amt
        ret_pct = (final_val - capital) / capital * 100
        
        avg_pnl = total_pnl_pct / count
        lines = [
            f"### {name}",
            f"| 指标 | 值 |",
            f"|------|-----|",
            f"| 初始资金 | ¥{capital:,.0f} |",
            f"| 有效交易 | {count}笔 |",
            f"| 每笔投入 | ¥{per_trade:,.0f} |",
            f"| 均收益 | {avg_pnl:+.1f}% |",
            f"| 盈亏金额 | ¥{pnl_amt:+,.0f} |",
            f"| **最终资金** | **¥{final_val:,.0f}** |",
            f"| **收益率** | **{ret_pct:+.1f}%** |",
            "",
        ]
        return lines
    
    lines.extend(engine_money(f3_valid, "🦅 海鹰F3"))
    lines.extend(engine_money(trl_valid, "🐉 龙脉TRL"))
    
    # 对比
    f3_final = 1000000 + (sum(t["pnl_pct"] for t in f3_valid)/100 * (1000000/len(f3_valid))) if f3_valid else 1000000
    trl_final = 1000000 + (sum(t["pnl_pct"] for t in trl_valid)/100 * (1000000/len(trl_valid))) if trl_valid else 1000000
    total_final = f3_final + trl_final
    total_ret = (total_final - 2000000) / 2000000 * 100
    lines.append(f"### ⚖️ 汇总对比")
    lines.append(f"| 引擎 | 初始 | 最终 | 收益 | 收益率 |")
    lines.append(f"|------|------|------|------|--------|")
    lines.append(f"| 🦅 海鹰F3 | ¥1,000,000 | ¥{f3_final:,.0f} | ¥{f3_final-1000000:+,.0f} | {(f3_final/1000000-1)*100:+.1f}% |")
    lines.append(f"| 🐉 龙脉TRL | ¥1,000,000 | ¥{trl_final:,.0f} | ¥{trl_final-1000000:+,.0f} | {(trl_final/1000000-1)*100:+.1f}% |")
    lines.append(f"| **合计** | **¥2,000,000** | **¥{total_final:,.0f}** | **¥{total_final-2000000:+,.0f}** | **{total_ret:+.1f}%** |")
    lines.append("")
    
    # ── 行动建议 ──
    lines.append("### 🔧 行动建议")
    lines.append("")
    lines.append("> ⚠️ 铁律: **回测不调参，只诊断。** 以下建议需要通过独立验证集(PurgedCV)确认后才能上线。")
    lines.append("")
    
    if f3_valid:
        f3_stops = sum(1 for t in f3_valid if t.get("exit_reason") == "stop_loss")
        f3_stop_rate = f3_stops / len(f3_valid)
        f3_lines = []
        if f3_stop_rate > 0.25:
            f3_lines.append(f"- ⚠️ 止损率{f3_stop_rate*100:.0f}%偏高 → 回测验证ATR倍数[1.5x/2x/2.5x/3x]")
        f3_loss_trades = [t for t in f3_valid if t["pnl_pct"] < -5]
        if f3_loss_trades:
            f3_lines.append(f"- {len(f3_loss_trades)}笔亏损>5% → 检查是否集中在特定板块/池")
        if f3_lines:
            lines.append("#### F3海鹰")
            lines.extend(f3_lines)
            lines.append("")
    
    if trl_valid:
        trl_lines = []
        leader_trades = [t for t in trl_valid if t.get("tier") == "leader"]
        laggard_trades = [t for t in trl_valid if t.get("tier") == "laggard"]
        if leader_trades and laggard_trades:
            leader_avg = sum(t["pnl_pct"] for t in leader_trades) / len(leader_trades)
            laggard_avg = sum(t["pnl_pct"] for t in laggard_trades) / len(laggard_trades)
            if laggard_avg > leader_avg:
                trl_lines.append(f"- 🔴 **leader层均{leader_avg:+.1f}% < laggard层均{laggard_avg:+.1f}%** — 反直觉!")
                trl_lines.append("  → 检查龙头选取逻辑(当前按close排序, 建议按成交额排序)")
        if trl_lines:
            lines.append("#### TRL龙脉")
            lines.extend(trl_lines)
            lines.append("")
    
    return "\n".join(lines)


def run_backtest_interval(start_date: str, end_date: str, tier: str = "B") -> dict:
    """对区间执行F3完整荐股周期, 返回含trades的完整数据"""
    trading_days = get_trading_days(start_date, end_date)
    if not trading_days:
        print(f"[回测] 区间 {start_date}~{end_date} 无交易日")
        return {"error": "no trading days", "interval": f"{start_date}_{end_date}", "trades": []}
    
    print(f"[回测] 区间: {start_date} → {end_date} ({len(trading_days)}个交易日)")
    
    all_trades = []
    reports = {"interval": f"{start_date}_{end_date}", "days": [], "trades": all_trades,
              "signal_stats": {"buy_rejected": 0, "ma_cross_buy": 0, "atr_stop": 0,
                                "ma_cross_sell": 0, "hard_stop": 0, "time_stop": 0, "expiry": 0}}
    
    for d in trading_days:
        print(f"\n--- {d} ---")
        result = run_pipeline(dry_run=False, force=True, replay_date=d)
        if result["status"] != "ok":
            print(f"  ❌ Pipeline失败: {result.get('status')}")
            continue
        
        all_picks = result["all_picks"]
        regime = _get_regime_for_date(d)
        
        # 盘前荐股报告
        rec_path = generate_recommendation_report(all_picks, {
            "regime": regime[0], "rsi14": regime[1].get("rsi14"),
            "daily_change": regime[1].get("daily_change"),
            "is_bull_trap": regime[1].get("is_bull_trap", False)
        }, d)
        print(f"  荐股报告: {rec_path}")
        
        # 个股生命周期追踪 (含信号检查)
        lifecycle = track_trade_lifecycle(all_picks, d, market_regime=regime[0])
        all_trades.extend(lifecycle.get("trades", []))
        
        # 盘后复盘 (含ASCII图)
        rev_path = generate_review_report(lifecycle, {"regime": regime[0]}, d)
        print(f"  复盘报告: {rev_path}")
        
        # 每日复盘HTML
        try:
            rev_json_path = Path(str(rev_path)).with_suffix('.json')
            import json as _json
            _json.dump(lifecycle, open(str(rev_json_path), 'w'), ensure_ascii=False, default=str)
            from scripts.daily_review_html import generate as gen_daily_html
            html_path = gen_daily_html(str(rev_json_path), str(Path(str(rev_path)).with_suffix('.html')))
            from scripts.md_to_png import report_to_png
            report_to_png(str(html_path))
        except Exception:
            pass
        
        # 累积信号统计
        ss = lifecycle.get("signal_stats", {})
        for k in reports["signal_stats"]:
            reports["signal_stats"][k] += ss.get(k, 0)
        
        reports["days"].append({
            "date": d, "regime": regime[0],
            "picks_count": result.get("total_picks", 0),
            "elapsed": result.get("elapsed", 0),
            "rec_report": str(rec_path), "review_report": str(rev_path),
        })
    
    return reports


def compute_l2_multi_source(as_of_date: str, db_conn) -> dict:
    """L2资金流多源交叉验证 (继承 multi_source.py S0/S1/S2 原则)
    
    三源: 北向(hsgt_stock_daily) + 融资(margin_daily) + 主力(stock_fund_flow)
    返回: {score, reliability, detail}
    
    v2修复: 北向estimated_net_buy NULL→net_inflow fallback; 主力60天不足→阈值兜底
    v3修复: 新增hsgt_daily.north(estimator)为最高优先级源, 避免已建管道被绕过
    """
    sources = {}
    
    # ── 源0: hsgt_daily.north (推算引擎产出, 最高质量) ──
    try:
        row = db_conn.execute("""
            SELECT MAX(net_buy) FROM hsgt_daily
            WHERE direction='north' AND trade_date=? AND net_buy IS NOT NULL
        """, (as_of_date,)).fetchone()
        # 3日均值
        rows_3d = db_conn.execute("""
            SELECT trade_date, MAX(net_buy) FROM hsgt_daily
            WHERE direction='north' AND trade_date <= ? AND net_buy IS NOT NULL
            GROUP BY trade_date ORDER BY trade_date DESC LIMIT 3
        """, (as_of_date,)).fetchall()
        if row and row[0] and len(rows_3d) >= 1:
            today = row[0]
            avg3 = sum(r[1] for r in rows_3d if r[1]) / len(rows_3d)
            # 评分: 用绝对值+方向
            if today > 100: hs = 9
            elif today > 50: hs = 7
            elif today > 20: hs = 5
            elif today > 0: hs = 3
            elif today > -30: hs = 1
            else: hs = 0
            sources["估算器"] = {"score": hs, "grade": "VALID",
                "detail": f"推算{as_of_date}净买{today:.1f}亿(3日均{avg3:.1f})→{hs}分"}
        else:
            sources["估算器"] = {"score": 0, "grade": "INVALID", "detail": "推算器无数据"}
    except Exception as e:
        sources["估算器"] = {"score": 0, "grade": "INVALID", "detail": f"推算器异常:{e}"}
    
    # ── 源1: 北向资金 (estimated_net_buy → net_inflow fallback) ──
    try:
        # 优先用estimated_net_buy
        rows = db_conn.execute("""
            SELECT SUM(estimated_net_buy)/100000000.0 FROM hsgt_stock_daily 
            WHERE direction='北向' AND trade_date <= ? AND trade_date >= date(?, '-3 days')
            GROUP BY trade_date
        """, (as_of_date, as_of_date)).fetchall()
        vals = db_conn.execute("""
            SELECT SUM(estimated_net_buy)/100000000.0 FROM hsgt_stock_daily 
            WHERE direction='北向' AND trade_date <= ? AND trade_date >= date(?, '-60 days')
            GROUP BY trade_date
        """, (as_of_date, as_of_date)).fetchall()
        daily = sorted([r[0] or 0 for r in vals])
        all_zero = all(v == 0 for v in daily) if daily else True
        
        # Fallback: estimated_net_buy全NULL时用net_inflow
        if all_zero or not daily or len(daily) < 5:
            rows = db_conn.execute("""
                SELECT SUM(net_inflow)/100000000.0 FROM hsgt_stock_daily 
                WHERE direction='北向' AND trade_date <= ? AND trade_date >= date(?, '-3 days')
                GROUP BY trade_date
            """, (as_of_date, as_of_date)).fetchall()
            vals = db_conn.execute("""
                SELECT SUM(net_inflow)/100000000.0 FROM hsgt_stock_daily 
                WHERE direction='北向' AND trade_date <= ? AND trade_date >= date(?, '-60 days')
                GROUP BY trade_date
            """, (as_of_date, as_of_date)).fetchall()
            daily = sorted([r[0] or 0 for r in vals])
            all_zero = all(v == 0 for v in daily) if daily else True
            source_tag = "北向(net_inflow)"
        else:
            source_tag = "北向"
        
        if daily and len(daily) >= 10 and not all_zero:
            p50 = daily[len(daily)//2]; p90 = daily[min(len(daily)*9//10, len(daily)-1)]
            today = sum(r[0] or 0 for r in rows) / max(len(rows), 1)
            if today >= p90: ns = 9
            elif today >= p50: ns = 5
            elif today > 0: ns = 3
            else: ns = 0
            sources[source_tag] = {"score": ns, "grade": "VALID", "detail": f"{source_tag}3日均{today:.1f}亿(P50={p50:.1f})→{ns}分"}
        else:
            sources[source_tag] = {"score": 0, "grade": "INVALID", "detail": f"{source_tag}全零/数据不足({len(daily)}天)"}
    except Exception as e:
        sources["北向"] = {"score": 0, "grade": "INVALID", "detail": f"北向异常:{e}"}
    
    # ── 源2: 融资余额 ──
    try:
        rows = db_conn.execute("""
            SELECT margin_balance FROM margin_daily 
            WHERE trade_date <= ? AND margin_balance IS NOT NULL
            ORDER BY trade_date DESC LIMIT 5
        """, (as_of_date,)).fetchall()
        if len(rows) >= 2 and rows[0][0] and rows[1][0] and rows[1][0] > 0:
            chg = (rows[0][0] - rows[1][0]) / rows[1][0] * 100
            if chg > 3: ms = 8
            elif chg > 1: ms = 6
            elif chg > 0: ms = 4
            elif chg > -2: ms = 2
            else: ms = 0
            sources["融资"] = {"score": ms, "grade": "VALID", "detail": f"融资环比{chg:+.1f}%→{ms}分"}
        else:
            sources["融资"] = {"score": 0, "grade": "INVALID", "detail": "融资数据不足"}
    except Exception as e:
        sources["融资"] = {"score": 0, "grade": "INVALID", "detail": f"融资异常:{e}"}
    
    # ── 源3: 主力资金 (容错: 60天不足用阈值兜底) ──
    try:
        rows = db_conn.execute("""
            SELECT SUM(main_net_buy)/100000000.0 FROM stock_fund_flow 
            WHERE trade_date <= ? AND trade_date >= date(?, '-3 days')
        """, (as_of_date, as_of_date)).fetchone()
        vals = db_conn.execute("""
            SELECT SUM(main_net_buy)/100000000.0 FROM stock_fund_flow 
            WHERE trade_date <= ? AND trade_date >= date(?, '-60 days')
            GROUP BY trade_date
        """, (as_of_date, as_of_date)).fetchall()
        daily = sorted([r[0] or 0 for r in vals])
        all_zero = all(v == 0 for v in daily) if daily else True
        
        today = (rows[0] or 0) / 3 if rows and rows[0] else 0
        
        if daily and len(daily) >= 10 and not all_zero:
            p50 = daily[len(daily)//2]; p90 = daily[min(len(daily)*9//10, len(daily)-1)]
            if today >= p90: mfs = 8
            elif today >= p50: mfs = 4
            elif today > 0: mfs = 2
            else: mfs = 0
            sources["主力"] = {"score": mfs, "grade": "VALID", "detail": f"主力3日均{today:.1f}亿(P50={p50:.1f})→{mfs}分"}
        elif today != 0:
            # 60天不足→阈值兜底
            if today > 10: mfs = 6
            elif today > 0: mfs = 3
            elif today > -10: mfs = 1
            else: mfs = 0
            sources["主力"] = {"score": mfs, "grade": "SPARSE", "detail": f"主力3日均{today:.1f}亿(数据稀疏)→{mfs}分"}
        else:
            sources["主力"] = {"score": 0, "grade": "INVALID", "detail": "主力全零/无数据"}
    except Exception as e:
        sources["主力"] = {"score": 0, "grade": "INVALID", "detail": f"主力异常:{e}"}
    
    # ── 交叉验证 ──
    valid = [s for s in sources.values() if s["grade"] in ("VALID", "SPARSE")]
    if not valid:
        return {"score": 0, "reliability": "INVALID", 
                "detail": "; ".join(s["detail"] for s in sources.values())}
    
    # 估算器优先: 最高质量源存在时主导
    est_src = sources.get("估算器", {})
    if est_src.get("grade") == "VALID" and est_src.get("score", 0) > 0:
        est_score = est_src["score"]
        other_scores = [s["score"] for s in valid if s is not est_src]
        if other_scores:
            other_avg = sum(other_scores) / len(other_scores)
            # 估算器权重0.7, 其他平均权重0.3
            final = round(est_score * 0.7 + other_avg * 0.3)
        else:
            final = est_score
        rel = "EST_DOMINANT"
        return {"score": min(25, final), "reliability": rel,
                "detail": "; ".join(s["detail"] for s in sources.values())}
    
    scores = [s["score"] for s in valid]
    spread = max(scores) - min(scores) if len(scores) > 1 else 0
    mean = sum(scores) / len(scores)
    
    if len(valid) == 1:
        final, rel = round(mean * 0.8), "SINGLE"  # 0.6→0.8: 单源虽少但可靠
    elif spread <= 2:
        final, rel = round(mean), "S0"
    elif spread <= 5:
        final, rel = round(mean * 0.85), "S1"
    else:
        final, rel = round(mean * 0.5), "S2"
    
    return {"score": min(25, final), "reliability": rel,
            "detail": "; ".join(s["detail"] for s in sources.values())}



def run_trl_backtest(start_date: str, end_date: str) -> dict:
    """TRL龙脉引擎回测: theme_detector_v3 + 名称解析 + 个股生命周期追踪"""
    sys.path.insert(0, str(Path.home() / ".hermes/scripts"))
    
    import importlib.util
    spec = importlib.util.spec_from_file_location("theme_detector_v3", 
        str(Path.home() / ".hermes/scripts/theme_detector_v3.py"))
    td_v3 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(td_v3)
    detect_themes_v3 = td_v3.detect_themes_v3
    get_hk_sectors = td_v3.get_hk_sectors
    get_industry_stocks = td_v3.get_industry_stocks
    
    # L2扫描引擎 (仅在 stock_industry_multilevel 有数据时启用)
    detect_themes_l2 = None
    try:
        db_conn2 = sqlite3.connect(str(DB))
        if db_conn2.execute("SELECT COUNT(*) FROM stock_industry_multilevel WHERE source='cninfo_sw'").fetchone()[0] > 0:
            spec_l2 = importlib.util.spec_from_file_location("theme_detector_v4",
                str(Path.home() / ".hermes/scripts/theme_detector_v4.py"))
            td_v4 = importlib.util.module_from_spec(spec_l2)
            spec_l2.loader.exec_module(td_v4)
            detect_themes_l2 = td_v4.detect_l2
            print("[TRL回测] L2引擎就绪")
        db_conn2.close()
    except Exception as e:
        print(f"[TRL回测] L2引擎跳过: {e}")
    
    days = get_trading_days(start_date, end_date)
    print(f"[TRL回测] {len(days)}天 ({days[0]}→{days[-1]})")
    
    results = []
    all_trades = []
    
    theme_streak = {}  # 主题持续性跟踪
    
    db_conn = sqlite3.connect(str(DB))
    db_conn.row_factory = sqlite3.Row
    try:
        for d in days:
            themes_data = detect_themes_v3(db_conn, as_of_date=d)
            themes = themes_data[0] if isinstance(themes_data, tuple) else themes_data
            
            # L2扫描: 申万二级行业
            if detect_themes_l2:
                try:
                    detect_themes_l2(db_conn, as_of_date=d)
                except Exception:
                    pass
            
            # ── L2多源交叉验证 + 四门禁 ──
            local_flow = compute_l2_multi_source(d, db_conn)
            for t in themes:
                old_l2 = t.get("layers", {}).get("layer2_flow", 0)
                l1 = t.get("layers", {}).get("layer1_price", 0)
                l3 = t.get("layers", {}).get("layer3_event", 0)
                
                # L2注入: 仅当多源有效时替换
                if local_flow["reliability"] != "INVALID":
                    t["layers"]["layer2_flow"] = local_flow["score"]
                    l2 = local_flow["score"]
                else:
                    l2 = old_l2  # 数据无效, 保持原值
                
                t["_flow_reliability"] = local_flow["reliability"]  # 留痕
                
                # 重新计算总分
                vol_est = max(0, t["total_score"] - l1 - old_l2 - l3)
                t["total_score"] = l1 + l2 + l3 + vol_est
                
                active = sum([l1 >= 12, l2 >= 10, l3 >= 12])
                theme_name = t["theme"]
                
                # 门禁判定
                if active == 3:
                    # 三层共振 → 检查四门禁
                    streak = theme_streak.get(theme_name, 0)
                    
                    # 门禁3: 尖峰脉冲检测 (在确认前)
                    if _check_volume_spike(theme_name, d, db_conn):
                        t["category"] = "watch"
                        t["_gate_blocked"] = "volume_spike"
                    # 门禁1: 持续性
                    elif streak < 3:
                        t["category"] = "potential"
                        t["_gate_blocked"] = f"persistence({streak}/3)"
                        theme_streak[theme_name] = streak + 1
                    # 门禁2: 板块宽度
                    elif not _check_sector_breadth(theme_name, d, db_conn):
                        t["category"] = "potential"
                        t["_gate_blocked"] = "breadth<50%"
                    # 门禁4: 扩散检测
                    elif _check_diffusion(t, theme_name):
                        t["category"] = "potential"
                        t["_gate_blocked"] = "diffusion"
                    else:
                        t["category"] = "confirmed"
                        theme_streak[theme_name] = streak + 1
                elif active == 2:
                    t["category"] = "potential"
                    theme_streak[theme_name] = theme_streak.get(theme_name, 0) + 1
                elif active == 1:
                    t["category"] = "watch"
                    theme_streak[theme_name] = 0
                else:
                    t["category"] = "skip"
                    theme_streak[theme_name] = 0
            
            confirmed = [t for t in themes if t["category"] == "confirmed"]
            potential = [t for t in themes if t["category"] == "potential"]
            
            target_themes = confirmed + potential
            day_picks = []
            
            for t in target_themes[:8]:
                theme_name = t["theme"]
                if theme_name.startswith("港股-"):
                    hk_sectors = get_hk_sectors(db_conn)
                    stocks = hk_sectors.get(theme_name, [])
                else:
                    stocks = get_industry_stocks(db_conn, theme_name)
                
                if not stocks or len(stocks) < 3:
                    continue
                
                stock_list = list(stocks)[:50]
                ph = ",".join(f"'{s}'" for s in stock_list)
                rows = db_conn.execute(f"""
                    SELECT symbol, close FROM stock_daily_all 
                    WHERE symbol IN ({ph}) AND trade_date=?
                    ORDER BY close DESC LIMIT 3
                """, (d,)).fetchall()
                
                for rank, row in enumerate(rows):
                    sym = row[0]
                    entry_price = row[1]
                    tier = "leader" if rank == 0 else ("weight" if rank == 1 else "laggard")
                    pool = "a_long" if not theme_name.startswith("港股-") else "hk_long"
                    name = _resolve_name(db_conn, sym)  # ← 修复: JOIN stock_basic
                    
                    try:
                        db_conn.execute("""
                            INSERT INTO leader_history 
                            (symbol, theme_name, recommend_date, pool, tier, score, engine)
                            VALUES (?, ?, ?, ?, ?, ?, 'TRLv3')
                        """, (sym, theme_name, d, pool, tier, 10.0 - rank))
                    except:
                        pass
                    
                    day_picks.append({
                        "symbol": sym, "name": name, "theme": theme_name,
                        "tier": tier, "score": 10.0 - rank,
                        "entry_price": entry_price, "date": d
                    })
            
            db_conn.commit()
            
            # ── 个股生命周期追踪 (信号引擎) ──
            from nous.engine.backtest.signal_engine import (evaluate_buy_signal, evaluate_sell_signal,
                                                     compute_position_size)
            
            for pick in day_picks:
                sym = pick["symbol"]
                
                # 入场信号检查
                buy_sig = evaluate_buy_signal(sym, d, db_conn, pool, "SIDEWAYS")
                if not buy_sig.approved:
                    all_trades.append({
                        "symbol": sym, "name": pick["name"],
                        "pool": "TRL_" + pick["theme"],
                        "theme": pick["theme"], "tier": pick["tier"],
                        "entry_date": d, "entry_price": pick["entry_price"],
                        "error": f"买入信号拒绝: {buy_sig.reason}",
                    })
                    continue
                
                fwd = db_conn.execute("""
                    SELECT trade_date, close FROM stock_daily_all 
                    WHERE symbol=? AND trade_date>? ORDER BY trade_date LIMIT 30
                """, (sym, d)).fetchall()
                
                if not fwd:
                    all_trades.append({
                        "symbol": sym, "name": pick["name"],
                        "pool": "TRL_" + pick["theme"],
                        "theme": pick["theme"], "tier": pick["tier"],
                        "entry_date": d, "entry_price": pick["entry_price"],
                        "error": "no forward data"
                    })
                    continue
                
                # ── 逐日出场信号检查 ──
                highest_since_entry = pick["entry_price"]
                exit_idx = None
                exit_reason = ""
                
                for i, (fwd_date, fwd_close) in enumerate(fwd):
                    if fwd_close > highest_since_entry:
                        highest_since_entry = fwd_close
                    
                    sell_sig = evaluate_sell_signal(
                        sym, pick["entry_price"], d, fwd_date, db_conn,
                        pool, highest_since_entry
                    )
                    
                    if sell_sig.approved:
                        exit_idx = i
                        exit_reason = sell_sig.reason
                        break
                    
                    # 持有期满 T+20
                    if i >= 19 and exit_idx is None:
                        exit_idx = i
                        exit_reason = "持有期满(T+20)"
                        break
                
                if exit_idx is None:
                    exit_idx = min(len(fwd) - 1, 19)
                    exit_reason = "持有期满(T+20)(兜底)"
                
                exit_price = fwd[exit_idx][1]
                exit_date = fwd[exit_idx][0]
                pnl_pct = round((exit_price - pick["entry_price"]) / pick["entry_price"] * 100, 2)
                pos_pct = compute_position_size(sym, d, db_conn, pool)
                
                all_trades.append({
                    "symbol": sym, "name": pick["name"],
                    "pool": "TRL_" + pick["theme"],
                    "theme": pick["theme"], "tier": pick["tier"],
                    "entry_date": d, "entry_price": pick["entry_price"],
                    "exit_date": exit_date, "exit_price": exit_price,
                    "exit_reason": exit_reason, "holding_days": exit_idx + 1,
                    "pnl_pct": pnl_pct,
                    "buy_signal_score": buy_sig.score,
                    "position_pct": pos_pct,
                })
            
            results.append({
                "date": d,
                "confirmed": [t["theme"] for t in confirmed],
                "potential": [t["theme"] for t in potential],
                "top_theme": themes[0]["theme"] if themes else None,
                "top_score": themes[0]["total_score"] if themes else 0,
                "top_category": themes[0]["category"] if themes else "skip",
                "trl_picks": len(day_picks),
                # 新增: 主线维度得分拆解
                "theme_scores": [
                    {"theme": t["theme"], "total_score": t["total_score"],
                     "category": t["category"],
                     "layer1_price": t.get("layers", {}).get("layer1_price", 0),
                     "layer2_flow": t.get("layers", {}).get("layer2_flow", 0),
                     "layer3_event": t.get("layers", {}).get("layer3_event", 0),
                    } for t in (confirmed + potential)[:5]
                ],
                "picks_detail": [{
                    "symbol": p["symbol"], "theme": p["theme"],
                    "tier": p["tier"], "score": p["score"],
                    "name": p["name"]
                } for p in day_picks],
            })
    finally:
        db_conn.close()
    
    days_with_confirmed = sum(1 for r in results if r["confirmed"])
    total_picks = sum(r["trl_picks"] for r in results)
    # 统计TRL信号
    trl_signal_stats = {"buy_rejected": 0, "ma_cross_buy": 0, "atr_stop": 0,
                        "ma_cross_sell": 0, "hard_stop": 0, "time_stop": 0, "expiry": 0}
    for t in all_trades:
        if "error" in t and "买入信号拒绝" in str(t.get("error", "")):
            trl_signal_stats["buy_rejected"] += 1
        elif "error" not in t:
            trl_signal_stats["ma_cross_buy"] += 1
            reason = t.get("exit_reason", "")
            if "ATR" in reason or "移动止盈" in reason:
                trl_signal_stats["atr_stop"] += 1
            elif "MA死叉" in reason:
                trl_signal_stats["ma_cross_sell"] += 1
            elif "硬止损" in reason:
                trl_signal_stats["hard_stop"] += 1
            elif "时间止损" in reason:
                trl_signal_stats["time_stop"] += 1
            elif "持有期满" in reason:
                trl_signal_stats["expiry"] += 1
    
    summary = {
        "mode": "TRL",
        "interval": f"{start_date}_{end_date}",
        "total_days": len(days),
        "days_with_confirmed": days_with_confirmed,
        "days_with_potential": sum(1 for r in results if r["potential"]),
        "total_picks": total_picks,
        "daily": results,
        "trades": all_trades,
        "signal_stats": trl_signal_stats,
    }
    
    out_path = REPORT_DIR / f"trl_backtest_{start_date}_{end_date}.json"
    with open(out_path, 'w') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    
    print(f"[TRL回测] 主线确认{days_with_confirmed}/{len(days)}天, 总推荐{total_picks}只, 有效交易{len([t for t in all_trades if 'error' not in t])}笔")
    print(f"[TRL回测] 报告: {out_path}")
    return summary


# ══════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Walk-Forward回测 v3.6")
    parser.add_argument("--start", type=str, help="起始日期")
    parser.add_argument("--end", type=str, help="截止日期")
    parser.add_argument("--random", type=int, default=0, help="随机区间数")
    parser.add_argument("--days", type=int, default=7, help="每区间交易日数")
    parser.add_argument("--replay", type=str, help="单日回放")
    parser.add_argument("--engine", type=str, default="F3", choices=["F3","TRL","dual"],
                       help="回测引擎: F3(因子分级) / TRL(龙脉) / dual(双轨对比)")
    parser.add_argument("--llm", action="store_true", default=False,
                       help="双轨报告追加LLM解读(仅dual模式)")
    args = parser.parse_args()
    
    if args.replay:
        if args.engine == "TRL":
            print("[TRL单日回放] 功能开发中，请使用 --start/--end")
            sys.exit(0)
        result = run_pipeline(dry_run=False, force=True, replay_date=args.replay)
        if result["status"] == "ok":
            picks = result["all_picks"]
            regime = _get_regime_for_date(args.replay)
            rec = generate_recommendation_report(picks, {
                "regime": regime[0], "rsi14": regime[1].get("rsi14"),
                "daily_change": regime[1].get("daily_change"),
                "is_bull_trap": regime[1].get("is_bull_trap", False)
            }, args.replay)
            lifecycle = track_trade_lifecycle(picks, args.replay)
            rev = generate_review_report(lifecycle, {"regime": regime[0]}, args.replay)
            print(f"\n盘前荐股: {rec}")
            print(f"盘后复盘: {rev}")
    elif args.start and args.end:
        if args.engine == "TRL":
            run_trl_backtest(args.start, args.end)
        elif args.engine == "dual":
            label = "🔴生产参考" if args.start >= "2026-05-01" else "🟡验证"
            print(f"[双轨对比回测] {label}")
            
            # F3轨
            print("\n═══ F3海鹰 ═══")
            f3_result = run_backtest_interval(args.start, args.end)
            
            # TRL轨  
            print("\n═══ TRL龙脉 ═══")
            trl_result = run_trl_backtest(args.start, args.end)
            
            # 质量门禁
            f3_data = {"label": label, "interval": f"{args.start}_{args.end}", 
                      "trades": f3_result.get("trades", []),
                      "signal_stats": f3_result.get("signal_stats", {})}
            failures = validate_backtest_quality([f3_data], [trl_result])
            
            if failures:
                print(f"\n❌ 回测质量门禁未通过:")
                for f in failures:
                    print(f"  - {f}")
            else:
                print(f"\n✅ 回测质量门禁通过")
            
            # 生成双轨合并报告
            dual_path = generate_dual_report(
                [f3_data], [trl_result],
                llm_section="",
                as_of=date.today().isoformat()
            )
            
            # LLM解读 (可选)
            if args.llm:
                print("\n🧠 生成LLM解读...")
                try:
                    from nous.engine.pipelines.backtest_llm_interpret import interpret_dual_results
                    llm_text = interpret_dual_results(f3_data, trl_result)
                    # 重新生成带LLM的报告
                    dual_path = generate_dual_report(
                        [f3_data], [trl_result],
                        llm_section=llm_text,
                        as_of=date.today().isoformat()
                    )
                    print(f"  LLM解读已追加")
                except ImportError:
                    print("  ⚠️ backtest_llm_interpret.py 未找到, 跳过LLM")
                except Exception as e:
                    print(f"  ⚠️ LLM解读失败: {e}")
            
            print(f"\n═══ 双轨对比 ═══")
            print(f"F3: {len(f3_result.get('days',[]))}天, "
                  f"{len([t for t in f3_result.get('trades',[]) if 'error' not in t])}有效交易")
            print(f"TRL: {trl_result['total_days']}天, "
                  f"主线确认{trl_result['days_with_confirmed']}天, "
                  f"{len([t for t in trl_result.get('trades',[]) if 'error' not in t])}有效交易")
            print(f"报告: {dual_path}")
            # 生成HTML报告
            try:
                from scripts.backtest_html_report import generate as gen_html
                f3_trades = f3_data.get("trades", [])
                f3_valid = [t for t in f3_trades if "error" not in t]
                sig_stats = f3_data.get("signal_stats", {})
                html_path = gen_html(
                    f3_valid, trl_result,
                    str(REPORT_DIR / "dual_random" / f"backtest_report_{args.start}_{args.end}.html"),
                    f3_signal_stats=sig_stats,
                    label=f"{args.start}_{args.end}"
                )
                print(f"HTML: {html_path}")
                # HTML→PNG截图
                try:
                    from scripts.md_to_png import report_to_png
                    report_to_png(str(html_path))
                except Exception: pass
            except Exception as e:
                import traceback
                print(f"  ⚠️ HTML生成失败: {e}")
                traceback.print_exc()
        else:
            run_backtest_interval(args.start, args.end)
    elif args.random > 0:
        feasible = get_feasible_range()
        if not feasible[0]:
            print("无可行区间")
            sys.exit(1)
        print(f"可行区间: {feasible[0]} ~ {feasible[1]}")
        
        all_days = get_trading_days(feasible[0], feasible[1])
        print(f"可用交易日: {len(all_days)}天")
        
        f3_intervals = []
        trl_jsons = []
        
        for i in range(args.random):
            max_start = len(all_days) - args.days
            if max_start < 1:
                print("区间不够长")
                break
            idx = random.randint(0, max_start - 1)
            s = all_days[idx]
            e = all_days[idx + args.days - 1]
            print(f"\n{'='*40}")
            print(f"随机区间 {i+1}: {s} ~ {e}")
            print(f"{'='*40}")
            
            f3_result = run_backtest_interval(s, e)
            trl_result = run_trl_backtest(s, e)
            
            label = "🔴生产参考" if s >= "2026-05-01" else f"🟡随机{i+1}"
            f3_intervals.append({"label": label, "interval": f"{s}_{e}", 
                               "trades": f3_result.get("trades", []),
                               "signal_stats": f3_result.get("signal_stats", {})})
            trl_jsons.append(trl_result)
        
        # 质量门禁
        failures = validate_backtest_quality(f3_intervals, trl_jsons)
        if failures:
            print(f"\n❌ 回测质量门禁未通过:")
            for f in failures:
                print(f"  - {f}")
        else:
            print(f"\n✅ 回测质量门禁通过")
        
        # 生成合并报告
        dual_path = generate_dual_report(
            f3_intervals, trl_jsons,
            llm_section="",
            as_of=date.today().isoformat()
        )
        print(f"双轨报告: {dual_path}")
    else:
        parser.print_help()
