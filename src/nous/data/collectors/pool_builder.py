#!/usr/bin/env python3
"""pool_builder — 动态池生成器 (09:25 运行)

从 screen_results + trader state + portfolio state 聚合今日跟踪标的,
清空 realtime_pool 旧数据 → INSERT 新数据,
添加固定指数 + 关联期货。

数据源 (按优先级):
  1. screen_results 当日 Top 50 (score>0)  → pool_source='recommend', strategy_type from screening
  2. portfolio/state.yaml 实盘持仓          → pool_source='portfolio'
  3. trader/state.yaml 模拟盘候选+持仓      → pool_source='trader'
  4. 固定指数: sh000001/sh000300/sz399006/sh000905/sh000688/HSTECH
  5. 关联期货: IF00/IC00/IH00/IM00
  6. 中概互联: KWEB (yfinance)

增强功能 (v2):
  - strategy_type 由 screen_results 字段或推断规则写入 realtime_pool
  - "至少 1 只" 保障: 每象限(A长/A短/H长/H短)若为空, 降级取 Top 1
  - recommendation_history 同步: 新入池 INSERT, 出池 UPDATE

自愈: heartbeat('pool_builder')

单独运行:
    python -m src.collectors.pool_builder
"""

import sys
import os
import json
import yaml
from datetime import date, datetime
from pathlib import Path

# ── 确保可以从 src 导入 ─────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from nous.data.collectors import heartbeat
from nous.data.storage import get_db

# ── 路径配置 ─────────────────────────────────────────
PORTFOLIO_STATE = Path.home() / "wiki/finance/portfolio/state.yaml"
TRADER_STATE = Path.home() / "code/stock-advisor/trader/state.yaml"
TODAY_REPORT_DIR = os.path.join(os.path.dirname(PROJECT_ROOT), "reports", "daily", date.today().strftime("%Y-%m-%d"))

# 固定指数: 前6个为A股指数(Sina可查), HSTECH/KWEB需yfinance
FIXED_INDICES = [
    ("sh000001", "上证指数"),
    ("sh000300", "沪深300"),
    ("sz399006", "创业板指"),
    ("sh000905", "中证500"),
    ("sh000688", "科创50"),
    ("HSTECH", "恒生科技"),
    ("KWEB", "中概互联ETF"),
]

# 关联期货 (Sina可查)
FUTURES_SYMBOLS = [
    ("IF00", "沪深300期货"),
    ("IC00", "中证500期货"),
    ("IH00", "上证50期货"),
    ("IM00", "中证1000期货"),
]

# realtime_pool 表 DDL (若不存在则自动创建)
DDL_POOL = """
CREATE TABLE IF NOT EXISTS realtime_pool (
    symbol      TEXT NOT NULL,
    pool_source TEXT NOT NULL DEFAULT 'screen',
    weight      REAL DEFAULT 1.0,
    strategy_type TEXT DEFAULT NULL,
    added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    active      INTEGER DEFAULT 1,
    PRIMARY KEY (symbol, pool_source)
);
CREATE INDEX IF NOT EXISTS idx_pool_active ON realtime_pool(active);
CREATE INDEX IF NOT EXISTS idx_pool_symbol ON realtime_pool(symbol);
"""

# recommendation_history 表 DDL (若不存在则自动创建)
DDL_RECOMMEND_HISTORY = """
CREATE TABLE IF NOT EXISTS recommendation_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol            TEXT NOT NULL,
    name              TEXT,
    market            TEXT,
    strategy_type     TEXT,
    entry_date        TEXT NOT NULL,
    exit_date         TEXT,
    status            TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','closed','stopped_out','time_exit','take_profit')),
    recommendation_date TEXT,
    source_report     TEXT,
    score             REAL,
    pnl               REAL,
    pnl_pct           REAL,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rec_history_symbol ON recommendation_history(symbol);
CREATE INDEX IF NOT EXISTS idx_rec_history_status ON recommendation_history(status);
"""


# ══════════════════════════════════════════════════════
# 数据源读取
# ══════════════════════════════════════════════════════

def _infer_strategy_type_from_row(row: dict) -> str:
    """从 screen_results 行推断 strategy_type.
    
    优先使用表中 strategy_type 字段 (若存在),
    否则根据评分指标推断:
      - long_term: PE<20 + ROE>10%
      - short_term: volume_ratio>2 + RSI 40-70
      - 若两者都满足, long_term 优先
      - 默认: 'long_term'
    """
    # 检查表中是否已有 strategy_type 列
    col_names = row.keys() if hasattr(row, 'keys') else {}
    if 'strategy_type' in col_names and row['strategy_type']:
        return str(row['strategy_type'])

    pe = row.get('pe')
    roe = row.get('roe')
    volume_ratio = row.get('volume_ratio')
    rsi = row.get('rsi')

    is_long = False
    is_short = False

    if pe is not None and roe is not None:
        try:
            if float(pe) < 20 and float(roe) > 10:
                is_long = True
        except (ValueError, TypeError):
            pass

    if volume_ratio is not None and rsi is not None:
        try:
            if float(volume_ratio) > 2 and 40 <= float(rsi) <= 70:
                is_short = True
        except (ValueError, TypeError):
            pass

    if is_long:
        return 'long_term'
    if is_short:
        return 'short_term'
    return 'long_term'  # 默认


def _infer_market(symbol: str) -> str:
    """根据代码前缀推断市场: 'A' = A股, 'H' = 港股"""
    if symbol.startswith(('HK', 'hk', 'H', '0')):
        # 以 H 开头的代码视为港股
        if symbol.startswith(('HK', 'hk')):
            return 'H'
        # 0 开头且长度为 5 (如 00700) 为港股
        if symbol.startswith('0') and len(symbol) == 5:
            return 'H'
    # A股: 6位数, 以 0/3/6 开头
    return 'A'


def _read_screen_recommends(limit: int = 50, relaxed: bool = False) -> list[dict]:
    """从 screen_results 读取当日评分>0的Top N推荐
    
    Args:
        limit: 最大返回条数
        relaxed: 是否使用宽松条件 (用于象限保障)
    
    Returns:
        含 symbol, name, score, strategy_type, pe, roe, volume_ratio, rsi, market 的列表
    """
    conn = get_db(write=False)
    try:
        latest = conn.execute("SELECT MAX(screen_date) as dt FROM screen_results").fetchone()
        if not latest or not latest["dt"]:
            print("  [pool_builder] screen_results 无数据", file=sys.stderr)
            return []

        if relaxed:
            rows = conn.execute(
                """SELECT s.symbol, COALESCE(b.name, s.symbol) as name, s.score,
                          s.pe, s.roe, s.volume_ratio, s.rsi, s.market
                   FROM screen_results s
                   LEFT JOIN stock_basic b ON s.symbol = b.symbol
                   WHERE s.screen_date = ? AND s.score >= 5
                   ORDER BY s.score DESC LIMIT ?""",
                (latest["dt"], limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT s.symbol, COALESCE(b.name, s.symbol) as name, s.score,
                          s.pe, s.roe, s.volume_ratio, s.rsi, s.market
                   FROM screen_results s
                   LEFT JOIN stock_basic b ON s.symbol = b.symbol
                   WHERE s.screen_date = ? AND s.score > 0
                   ORDER BY s.score DESC LIMIT ?""",
                (latest["dt"], limit),
            ).fetchall()

        label = "宽松" if relaxed else ""
        print(f"  [pool_builder] screen_results({label})({latest['dt']}): Top {len(rows)} 只")
        result = []
        for r in rows:
            strategy_type = _infer_strategy_type_from_row(dict(r))
            market = _infer_market(r["symbol"])
            result.append({
                "symbol": r["symbol"],
                "name": r["name"],
                "score": r["score"],
                "strategy_type": strategy_type,
                "market": market,
                "pe": r["pe"],
                "roe": r["roe"],
                "volume_ratio": r["volume_ratio"],
                "rsi": r["rsi"],
            })
        return result
    finally:
        conn.close()


def _read_portfolio_state() -> list[dict]:
    """从 portfolio/state.yaml 读取实盘持仓"""
    symbols = []
    if not PORTFOLIO_STATE.exists():
        print(f"  [pool_builder] portfolio.yaml 不存在: {PORTFOLIO_STATE}", file=sys.stderr)
        return []

    try:
        state = yaml.safe_load(PORTFOLIO_STATE.read_text(encoding="utf-8"))
        # 遍历所有账户的 holdings
        accounts = state.get("accounts", {})
        for acct_name, acct_data in accounts.items():
            for holding in acct_data.get("holdings", []):
                sym = str(holding.get("symbol", "")).strip()
                if sym:
                    symbols.append(sym)
        # 基金持仓 (funds)
        funds = state.get("funds", {})
        for holding in funds.get("holdings", []):
            sym = str(holding.get("symbol", "")).strip()
            if sym:
                symbols.append(sym)
    except Exception as e:
        print(f"  [pool_builder] 解析 portfolio.yaml 失败: {e}", file=sys.stderr)

    result = list(dict.fromkeys(symbols))  # 去重保序
    print(f"  [pool_builder] portfolio: {len(result)} 只")
    return [{"symbol": s, "name": "", "score": 10} for s in result]


def _read_trader_state() -> list[dict]:
    """从 trader/state.yaml 读取模拟盘候选 + 持仓"""
    symbols = []
    if not TRADER_STATE.exists():
        print(f"  [pool_builder] trader/state.yaml 不存在: {TRADER_STATE}", file=sys.stderr)
        return []

    try:
        state = yaml.safe_load(TRADER_STATE.read_text(encoding="utf-8"))
        # 从 positions 字段读取持仓标的
        positions = state.get("positions", {})
        if isinstance(positions, dict):
            for sym, pos_info in positions.items():
                if sym:
                    symbols.append(str(sym))
        elif isinstance(positions, list):
            for p in positions:
                sym = str(p.get("symbol", "")).strip()
                if sym:
                    symbols.append(sym)

        # 从 orders -> pending 中读取候选
        orders = state.get("orders", {})
        pending = orders.get("pending", {})
        if isinstance(pending, dict):
            for sym in pending:
                if sym:
                    symbols.append(str(sym))
    except Exception as e:
        print(f"  [pool_builder] 解析 trader/state.yaml 失败: {e}", file=sys.stderr)

    result = list(dict.fromkeys(symbols))
    print(f"  [pool_builder] trader: {len(result)} 只")
    return [{"symbol": s, "name": "", "score": 8} for s in result]


# ══════════════════════════════════════════════════════
# 象限保障
# ══════════════════════════════════════════════════════

def _build_quadrant_key(symbol: str, strategy_type: str) -> str:
    """生成象限键: A长, A短, H长, H短"""
    market = _infer_market(symbol)
    # 将 long_term/short_term 映射为 长/短
    st = "长" if strategy_type == "long_term" else "短" if strategy_type == "short_term" else strategy_type
    return f"{market}{st}"


def _ensure_min_one_per_quadrant(all_candidates: dict, screen_items: list[dict]) -> dict:
    """"至少 1 只" 保障: 检查每个象限, 若空则降级取 1 只。
    
    修改 all_candidates 并返回。
    """
    # 统计当前各象限入选情况
    quadrant_counts = {"A长": 0, "A短": 0, "H长": 0, "H短": 0}
    for sym, (src, weight, st) in all_candidates.items():
        if src != "recommend":
            continue
        qk = _build_quadrant_key(sym, st)
        if qk in quadrant_counts:
            quadrant_counts[qk] += 1

    empty_quadrants = [qk for qk, cnt in quadrant_counts.items() if cnt == 0]
    if not empty_quadrants:
        return all_candidates

    print(f"\n  [pool_builder] 象限保障: 以下象限为空 {empty_quadrants}")
    
    # 对每个空象限, 用宽松条件补充
    for qk in empty_quadrants:
        market_target = qk[0]  # 'A' 或 'H'
        strategy_target = "long_term" if "长" in qk else "short_term"
        
        # 找候选: 从 screen_items 中找符合条件但未入选的
        candidates = []
        for item in screen_items:
            sym = item["symbol"].strip().zfill(6)
            # 已入选则跳过
            if sym in all_candidates:
                continue
            # 匹配市场
            item_market = _infer_market(sym)
            if item_market != market_target:
                continue
            # 匹配策略类型
            if item.get("strategy_type") != strategy_target:
                continue
            # 宽松条件
            if strategy_target == "long_term":
                # 放宽: PE<25, ROE>5%, score>=5
                pe = item.get("pe")
                roe = item.get("roe")
                score = item.get("score", 0)
                if pe is not None and roe is not None and score >= 5:
                    try:
                        if float(pe) < 25 and float(roe) > 5:
                            candidates.append(item)
                    except (ValueError, TypeError):
                        pass
            else:  # short_term
                # 放宽: volume_ratio>1.5, 不要求dragon_tiger, score>=5
                vr = item.get("volume_ratio")
                score = item.get("score", 0)
                if vr is not None and score >= 5:
                    try:
                        if float(vr) > 1.5:
                            candidates.append(item)
                    except (ValueError, TypeError):
                        pass

        if candidates:
            # 按评分降序取 Top 1
            candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
            pick = candidates[0]
            sym = pick["symbol"].strip().zfill(6)
            st = pick.get("strategy_type", strategy_target)
            all_candidates[sym] = ("recommend", pick.get("score", 5) / 10.0, st)
            print(f"    ✓ {qk} 补充: {sym} ({pick['name']}) 评分={pick['score']}")
        else:
            # 仍找不到, 标记 NO_PICK (不强制塞入垃圾)
            print(f"    ✗ {qk} 仍空, 标记 NO_PICK (不强制入池)")

    return all_candidates


# ══════════════════════════════════════════════════════
# recommendation_history 同步
# ══════════════════════════════════════════════════════

def _sync_recommendation_history(
    conn,
    today: date,
    new_recommends: list[dict],
    all_candidates: dict,
):
    """同步 recommendation_history:
    
    1. 新入池 (screen_results → recommend): INSERT history
    2. 出池 (昨日 active recommend 今日不在): UPDATE status='closed'
    """
    today_str = today.strftime("%Y-%m-%d")
    
    # 目前候选池中的 recommend 标的
    current_recommend_symbols = set()
    current_recommend_info = {}
    for sym, (src, weight, st) in all_candidates.items():
        if src == "recommend":
            current_recommend_symbols.add(sym)
            current_recommend_info[sym] = st

    # 获取昨日活跃的 recommend 历史
    yesterday_active = conn.execute(
        """SELECT rh.symbol, rh.name, rh.market, rh.strategy_type, rh.score
           FROM recommendation_history rh
           WHERE rh.status='active'
           AND rh.symbol IN (
               SELECT rp.symbol FROM realtime_pool rp
               WHERE rp.pool_source='recommend' AND rp.active=0
               AND EXISTS (
                   SELECT 1 FROM realtime_pool rp2
                   WHERE rp2.symbol = rp.symbol AND rp2.active = 0
                   GROUP BY rp2.symbol
               )
           )"""
    ).fetchall()
    
    # 由于 active 已被清 0, 更好的方式: 对比新候选 vs 所有 status='active' 的历史
    # 使用所有 status='active' 的记录
    all_active_histories = conn.execute(
        "SELECT * FROM recommendation_history WHERE status='active'"
    ).fetchall()
    
    active_historical_symbols = {row["symbol"] for row in all_active_histories}

    # 1. INSERT 新入池标的
    inserted_count = 0
    for sym in current_recommend_symbols:
        if sym not in active_historical_symbols:
            # 新标的, 从 new_recommends 找详情
            info = None
            for item in new_recommends:
                item_sym = item["symbol"].strip().zfill(6)
                if item_sym == sym:
                    info = item
                    break
            
            if info is None:
                continue

            # 推断 market
            market = _infer_market(sym)
            name = info.get("name", sym)
            strategy_type = current_recommend_info.get(sym, info.get("strategy_type", "long_term"))
            score = info.get("score", 0)

            # 查找今日报告路径
            report_path = _find_today_report(sym, today_str)

            conn.execute(
                """INSERT INTO recommendation_history
                   (symbol, name, market, strategy_type, entry_date, status, recommendation_date, source_report, score)
                   VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
                (sym, name, market, strategy_type, today_str, today_str, report_path, score),
            )
            inserted_count += 1

    # 2. UPDATE 出池标的
    removed_count = 0
    for row in all_active_histories:
        sym = row["symbol"]
        if sym not in current_recommend_symbols:
            conn.execute(
                "UPDATE recommendation_history SET exit_date=?, status='closed' WHERE symbol=? AND status='active'",
                (today_str, sym),
            )
            removed_count += 1

    if inserted_count:
        print(f"  [pool_builder] recommendation_history: +{inserted_count} 新入池")
    if removed_count:
        print(f"  [pool_builder] recommendation_history: -{removed_count} 出池 (closed)")


def _find_today_report(symbol: str, today_str: str) -> str:
    """查找今日分析报告路径"""
    report_dir = os.path.join(
        os.path.dirname(PROJECT_ROOT), "reports", "daily", today_str
    )
    if os.path.isdir(report_dir):
        for fname in os.listdir(report_dir):
            if symbol in fname and fname.endswith(".md"):
                return os.path.join(report_dir, fname)
    return ""


# ══════════════════════════════════════════════════════
# 池生成主逻辑
# ══════════════════════════════════════════════════════

def build_realtime_pool() -> int:
    """生成并写入 realtime_pool 表, 返回写入条数"""
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    print("=" * 60)
    print(f" pool_builder — {today}")
    print("=" * 60)

    # 使用三元组: symbol -> (pool_source, weight, strategy_type)
    all_candidates = {}  # symbol -> (pool_source, weight, strategy_type)

    # 1. screen_results 当日推荐 (score>0, Top 50)
    screen_items = _read_screen_recommends(50)
    for item in screen_items:
        sym = item["symbol"].strip().zfill(6)
        if sym not in all_candidates:
            st = item.get("strategy_type", "long_term")
            all_candidates[sym] = ("recommend", item.get("score", 5) / 10.0, st)

    # 2. 实盘持仓 (portfolio) - 无 strategy_type
    for item in _read_portfolio_state():
        sym = item["symbol"].strip()
        if not sym.startswith(("HK", "hk")):
            sym = sym.zfill(6)
        if sym not in all_candidates:
            all_candidates[sym] = ("portfolio", 1.5, None)

    # 3. 模拟盘 (trader) - 无 strategy_type
    for item in _read_trader_state():
        sym = item["symbol"].strip()
        if not sym.startswith(("HK", "hk")):
            sym = sym.zfill(6)
        if sym not in all_candidates:
            all_candidates[sym] = ("trader", 1.2, None)

    # 4. 固定指数 - 无 strategy_type
    for sym, name in FIXED_INDICES:
        if sym not in all_candidates:
            all_candidates[sym] = ("index", 2.0, None)

    # 5. 关联期货 - 无 strategy_type
    for sym, name in FUTURES_SYMBOLS:
        if sym not in all_candidates:
            all_candidates[sym] = ("futures", 1.5, None)

    # "至少 1 只" 象限保障 (仅针对 recommend 源)
    all_candidates = _ensure_min_one_per_quadrant(all_candidates, screen_items)

    if not all_candidates:
        print("  [pool_builder] ⚠ 无候选标的", file=sys.stderr)
        return 0

    print(f"\n  聚合完成: {len(all_candidates)} 只")
    sources = {}
    for src, _, _ in all_candidates.values():
        sources[src] = sources.get(src, 0) + 1
    for src, cnt in sorted(sources.items()):
        print(f"    {src}: {cnt} 只")

    # 写入数据库
    conn = get_db(write=True)
    try:
        # 确保 DDL 已执行
        conn.executescript(DDL_POOL)
        conn.executescript(DDL_RECOMMEND_HISTORY)

        # 获取旧活跃 recommend 标的 (用于 recommendation_history 同步)
        # 同步需要在清空前执行, 但因为我们用 status='active' 判断, 在清空后也没问题
        # 实际上先同步再清空更方便
        _sync_recommendation_history(conn, today, screen_items, all_candidates)

        # 清空今日旧数据 (标记active=0)
        conn.execute("UPDATE realtime_pool SET active = 0")
        # 清空所有非活跃旧数据
        conn.execute("DELETE FROM realtime_pool WHERE active = 0")

        inserted = 0
        for sym, (source, weight, strategy_type) in sorted(all_candidates.items()):
            conn.execute(
                "INSERT OR REPLACE INTO realtime_pool (symbol, pool_source, weight, strategy_type, added_at, active) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, 1)",
                (sym, source, weight, strategy_type),
            )
            inserted += 1

        conn.commit()
        print(f"\n  写入完成: {inserted} 条")
        return inserted
    finally:
        conn.close()


# ══════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════

def main():
    """独立运行入口 — 生成池后立即退出"""
    count = build_realtime_pool()
    # 写入心跳
    heartbeat("pool_builder")
    print(f"\n  pool_builder 完成, 写入 {count} 条")


if __name__ == "__main__":
    main()
