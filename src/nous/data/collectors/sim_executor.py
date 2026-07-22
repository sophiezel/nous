"""sim_executor — 模拟盘交易执行器 (SQLite-only)

检测推荐池变更 → 生成买卖计划 → 每15s检查slot时间 → 执行 → 写入sim_trades

池变更检测:
  对比昨日realtime_pool(source='recommend') vs 今日realtime_pool(source='recommend')

入池标的: slot 1-3 各买入动态仓位 (CAPITAL_PER_SLOT/price 取整到手)
出池标的: slot 1-3 各卖出同等仓位

多维卖出触发:
  - 池移除: 出池标的全额卖出
  - 止损: 当前价 <= 持仓均价 * 0.92 → 全仓卖出
  - 时间退出: 持仓 > 5个交易日且盈亏 < 3% → 全仓卖出
  - 止盈: 盈亏 >= 15% 且 RSI > 70 → 卖出50%

量化模型: strategy_type='quant', slot 1 一次性买入1手(100股)

关键约束:
  - 价格获取: resilient_fetch('sina', ...)
  - DB写入: get_db(write=True) from src.storage
  - 心跳: heartbeat('sim_executor')
  - 优雅降级: 实时价不可用时使用上一笔价格(stale标记)
  - 全SQLite持久化: 无JSON文件, 计划存入sim_trade_plans表
"""

import sys
import time
import re
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

# ── 路径 ──────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── 自愈框架导入 ──────────────────────────────────
from nous.data.collectors import heartbeat, resilient_fetch, collector_main_loop

# ── DB导入 ─────────────────────────────────────────
from nous.data.storage import get_db, with_retry

# ── 常量 ──────────────────────────────────────────
LOT_SIZE = 100          # 每手股数
CAPITAL_PER_SLOT = 50000  # 每slot可用资金 (总资金100万, 5%)
QUANT_SHARES = 100      # 量化策略 1 手
SLOT_TIMES = {1: "10:00", 2: "11:00", 3: "14:00"}
IS_TRADING_DAY_CACHE = {}  # 日内缓存

# Sina 行情 API 前缀映射 (从 minute_collector 复用)
MARKET_PREFIX = {
    "6": "sh", "5": "sh", "9": "sh",
    "0": "sz", "3": "sz", "2": "sz",
    "8": "bj", "4": "bj",
}

DDL = """
CREATE TABLE IF NOT EXISTS sim_trade_plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    name            TEXT,
    action          TEXT NOT NULL CHECK(action IN ('buy','sell')),
    slot            INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 3),
    shares          INTEGER NOT NULL DEFAULT 0,
    strategy_type   TEXT NOT NULL DEFAULT 'recommend',
    sell_reason     TEXT DEFAULT NULL,
    executed        INTEGER NOT NULL DEFAULT 0,
    executed_at     TEXT,
    price           REAL,
    amount          REAL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_plans_executed ON sim_trade_plans(executed);
CREATE INDEX IF NOT EXISTS idx_plans_slot ON sim_trade_plans(slot);
CREATE INDEX IF NOT EXISTS idx_plans_symbol ON sim_trade_plans(symbol, action);
"""


# ── 辅助函数 ──────────────────────────────────────

def symbol_to_sina(symbol: str) -> Optional[str]:
    """将纯数字代码转为 Sina API 格式"""
    sym = symbol.strip().zfill(6)
    for k, v in MARKET_PREFIX.items():
        if sym.startswith(k):
            return f"{v}{sym}"
    return f"sz{sym}"


def is_trading_day() -> bool:
    """判断今天是否为交易日(简单: 非周末)"""
    today = date.today()
    if today in IS_TRADING_DAY_CACHE:
        return IS_TRADING_DAY_CACHE[today]
    weekday = today.weekday()
    result = weekday < 5
    IS_TRADING_DAY_CACHE[today] = result
    return result


def _get_name(conn, symbol: str) -> str:
    """从 stock_basic 获取股票名称"""
    try:
        row = conn.execute(
            "SELECT name FROM stock_basic WHERE symbol=?", (symbol,)
        ).fetchone()
        if row and row["name"]:
            return row["name"]
    except Exception:
        pass
    return symbol


def _ensure_tables(conn):
    """确保所有依赖表及字段存在"""
    conn.executescript(DDL)
    # 迁移: 为 sim_trade_plans 添加 sell_reason 列 (如果不存在)
    try:
        conn.execute("ALTER TABLE sim_trade_plans ADD COLUMN sell_reason TEXT DEFAULT NULL")
    except Exception:
        pass
    # 迁移: 为 sim_trades 添加 strategy_type 列 (如果不存在)
    try:
        conn.execute("ALTER TABLE sim_trades ADD COLUMN strategy_type TEXT DEFAULT 'recommend'")
    except Exception:
        pass
    # 迁移: 为 sim_trades 添加 trade_date 列 (如果不存在)
    try:
        conn.execute("ALTER TABLE sim_trades ADD COLUMN trade_date TEXT DEFAULT (date('now'))")
    except Exception:
        pass
    # 迁移: 创建 UNIQUE 索引 (如果不存在)
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_sim_trades_unique "
            "ON sim_trades(symbol, slot, action, trade_date)"
        )
    except Exception:
        pass
    conn.commit()


def _compute_dynamic_shares(price: float) -> int:
    """动态计算每slot买入股数

    每slot投入 <= CAPITAL_PER_SLOT (5% 总资金)
    取整到手数 (100的倍数)
    上限 100 手 (10000股)
    """
    if price <= 0:
        return LOT_SIZE  # 兜底 1 手
    max_lots = int(CAPITAL_PER_SLOT / (price * LOT_SIZE))
    lots = min(max_lots, 100)
    if lots < 1:
        lots = 1
    return lots * LOT_SIZE


# ── 价格获取(自愈) ─────────────────────────────────

def fetch_sina_price(symbol: str) -> tuple[Optional[float], bool]:
    """通过 resilient_fetch 获取单个标的最新价

    Returns:
        (price, is_stale)
        is_stale=True 表示价格来自降级/缓存
    """
    sina_code = symbol_to_sina(symbol)
    if not sina_code:
        return None, False

    def _fetch():
        try:
            import requests as o
        except ImportError:
            try:
                from curl_cffi import requests as c
                o = c
            except ImportError:
                raise ImportError("No requests library available")

        url = f"http://hq.sinajs.cn/list={sina_code}"
        resp = o.get(url, timeout=10)
        resp.encoding = "gbk"
        text = resp.text.strip()
        if not text or "=" not in text:
            raise ValueError(f"Empty response for {sina_code}")

        match = re.search(r'var hq_str_\w+="(.+)"', text)
        if not match:
            raise ValueError(f"Cannot parse response for {sina_code}")

        fields = match.group(1).split(",")
        if len(fields) < 32:
            raise ValueError(f"Too few fields for {sina_code}: {len(fields)}")

        price = float(fields[3]) if fields[3] else 0
        return price

    def _fallback():
        """降级: 从 intraday_minute 取上一笔价格"""
        conn = get_db(write=False)
        try:
            row = conn.execute(
                "SELECT price FROM intraday_minute WHERE symbol=? ORDER BY datetime DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            if row and row["price"] and row["price"] > 0:
                return row["price"]
            # 再降级: 从 stock_daily 取最近收盘价
            row = conn.execute(
                "SELECT close FROM stock_daily WHERE symbol=? ORDER BY trade_date DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            if row and row["close"] and row["close"] > 0:
                return row["close"]
        except Exception:
            pass
        finally:
            conn.close()
        raise ValueError(f"No fallback price for {symbol}")

    result, status = resilient_fetch("sina", _fetch, fallback_fn=_fallback)
    if status.get("success") and result is not None and result > 0:
        is_stale = status.get("fallback_used", False) or status.get("retries", 0) > 0
        return result, is_stale
    return None, False


def fetch_batch_prices(symbols: list[str]) -> dict[str, dict]:
    """批量获取价格，返回 {symbol: {price, stale}}

    分批请求 Sina API (每批最多200个)
    """
    if not symbols:
        return {}

    import requests as o
    try:
        from curl_cffi import requests as c
        o.get = lambda url, **kw: c.get(
            url, impersonate="chrome131", timeout=15,
            **{k: v for k, v in kw.items() if k != "proxies"}
        )
    except ImportError:
        pass

    batch_size = 200
    result = {}
    fallback_needed = []

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        sina_codes = [symbol_to_sina(s) for s in batch]
        sina_codes = [c for c in sina_codes if c is not None]

        if not sina_codes:
            continue

        url = f"http://hq.sinajs.cn/list={','.join(sina_codes)}"

        try:
            resp = o.get(url, timeout=10)
            resp.encoding = "gbk"
            text = resp.text.strip()

            for line in text.splitlines():
                line = line.strip()
                if not line or "=" not in line:
                    continue
                match = re.match(r'var hq_str_(\w+)="(.+)"', line)
                if not match:
                    continue
                sina_code = match.group(1)
                fields = match.group(2).split(",")
                if len(fields) < 32:
                    continue

                pure_symbol = "".join(ch for ch in sina_code if ch.isdigit())
                try:
                    price = float(fields[3]) if fields[3] else 0
                    if price > 0:
                        result[pure_symbol] = {"price": price, "stale": False}
                    else:
                        fallback_needed.append(pure_symbol)
                except (ValueError, IndexError):
                    fallback_needed.append(pure_symbol)

        except Exception as e:
            print(f"  [sim_executor] Sina batch fetch error: {e}", file=sys.stderr)
            fallback_needed.extend(
                "".join(ch for ch in c if ch.isdigit())
                for c in sina_codes if c
            )

        if i + batch_size < len(symbols):
            time.sleep(0.3)

    # 对失败/缺失的标的使用降级价格
    if fallback_needed:
        conn = get_db(write=False)
        try:
            for sym in fallback_needed:
                if sym in result:
                    continue
                row = conn.execute(
                    "SELECT price FROM intraday_minute WHERE symbol=? ORDER BY datetime DESC LIMIT 1",
                    (sym,),
                ).fetchone()
                if row and row["price"] and row["price"] > 0:
                    result[sym] = {"price": row["price"], "stale": True}
                else:
                    row2 = conn.execute(
                        "SELECT close FROM stock_daily WHERE symbol=? ORDER BY trade_date DESC LIMIT 1",
                        (sym,),
                    ).fetchone()
                    if row2 and row2["close"] and row2["close"] > 0:
                        result[sym] = {"price": row2["close"], "stale": True}
        except Exception:
            pass
        finally:
            conn.close()

    return result


# ── 池变更检测 ────────────────────────────────────

def detect_pool_changes() -> dict:
    """检测推荐池变更: 对比昨日 vs 今日 realtime_pool 中 source='recommend' 的标的

    Returns:
        {
            "new_entries": [{"symbol": "...", "name": "..."}, ...],
            "removed": [{"symbol": "...", "name": "..."}, ...],
            "today_symbols": [...],
            "yesterday_symbols": [...],
        }
    """
    conn = get_db(write=False)
    try:
        # 获取今日推荐池标的 (active=1 且 pool_source='recommend')
        today_rows = conn.execute(
            "SELECT symbol FROM realtime_pool WHERE active=1 AND pool_source='recommend'"
        ).fetchall()
        today_symbols = {r["symbol"] for r in today_rows}

        # 获取昨日推荐池标的
        yesterday_rows = conn.execute(
            "SELECT symbol FROM realtime_pool WHERE pool_source='recommend' "
            "AND (active=0 OR "
            "added_at < date('now') || ' 09:30:00') "
            "GROUP BY symbol"
        ).fetchall()
        yesterday_symbols = {r["symbol"] for r in yesterday_rows}

        # 如果 yesterday 是空的(首次运行), 用 today 作为基准(不触发买卖)
        if not yesterday_symbols:
            print("  [sim_executor] 首次运行: 无昨日池对比, 今日池作为基准")
            return {
                "new_entries": [],
                "removed": [],
                "today_symbols": sorted(today_symbols),
                "yesterday_symbols": set(),
            }

        # 新入池: 今日有但昨日无
        new_entries = []
        for sym in sorted(today_symbols - yesterday_symbols):
            name = _get_name(conn, sym)
            new_entries.append({"symbol": sym, "name": name})

        # 出池: 昨日有但今日无
        removed = []
        for sym in sorted(yesterday_symbols - today_symbols):
            name = _get_name(conn, sym)
            removed.append({"symbol": sym, "name": name})

        print(f"  [sim_executor] 池变更检测: 今日推荐 {len(today_symbols)}, "
              f"昨日推荐 {len(yesterday_symbols)}")
        print(f"  [sim_executor] 新入池 {len(new_entries)}, 出池 {len(removed)}")

        return {
            "new_entries": new_entries,
            "removed": removed,
            "today_symbols": sorted(today_symbols),
            "yesterday_symbols": sorted(yesterday_symbols),
        }

    except Exception as e:
        print(f"  [sim_executor] 池变更检测失败: {e}", file=sys.stderr)
        return {"new_entries": [], "removed": [], "today_symbols": [], "yesterday_symbols": []}
    finally:
        conn.close()


# ── 持仓聚合 ──────────────────────────────────────

def _aggregate_positions(conn) -> dict:
    """从 sim_trades 聚合当前活跃持仓

    Returns:
        {
            symbol: {
                "name": str,
                "shares": int,
                "avg_price": float,
                "total_cost": float,
                "strategy_type": str,
                "earliest_buy_date": str (YYYY-MM-DD),
            },
            ...
        }
    """
    positions = {}

    trades = conn.execute(
        "SELECT symbol, name, action, shares, price, trade_time, strategy_type "
        "FROM sim_trades ORDER BY trade_time ASC"
    ).fetchall()

    for t in trades:
        sym = t["symbol"]
        if sym not in positions:
            positions[sym] = {
                "name": t["name"] or sym,
                "shares": 0,
                "total_cost": 0.0,
                "strategy_type": t["strategy_type"] or "recommend",
                "earliest_buy_date": None,
            }

        pos = positions[sym]
        if t["action"] == "buy":
            pos["shares"] += t["shares"]
            pos["total_cost"] += t["price"] * t["shares"]
            if pos["earliest_buy_date"] is None:
                pos["earliest_buy_date"] = t["trade_time"][:10]
        elif t["action"] == "sell":
            pos["shares"] -= t["shares"]
            # proportionally reduce cost
            if pos["shares"] + t["shares"] > 0:
                sell_ratio = t["shares"] / (pos["shares"] + t["shares"])
                pos["total_cost"] -= pos["total_cost"] * sell_ratio

    # 过滤净持仓 > 0 的, 计算均价
    result = {}
    for sym, pos in positions.items():
        if pos["shares"] <= 0:
            continue
        pos["avg_price"] = pos["total_cost"] / pos["shares"] if pos["shares"] > 0 else 0
        result[sym] = pos

    return result


def _get_rsi(conn, symbol: str) -> Optional[float]:
    """从 screen_results 或 stock_daily 获取最新 RSI"""
    try:
        row = conn.execute(
            "SELECT rsi FROM screen_results WHERE symbol=? "
            "ORDER BY screen_date DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if row and row["rsi"] is not None:
            return row["rsi"]
    except Exception:
        pass
    return None


def _count_holding_days(entry_date_str: str) -> int:
    """粗略计算持仓天数 (自然日, 简化)"""
    if not entry_date_str:
        return 0
    try:
        entry = datetime.strptime(entry_date_str, "%Y-%m-%d").date()
        delta = (date.today() - entry).days
        return delta
    except (ValueError, TypeError):
        return 0


# ── 计划管理 ──────────────────────────────────────

def _load_plans() -> list[dict]:
    """从 sim_trade_plans 表加载未执行的交易计划"""
    conn = get_db(write=False)
    try:
        rows = conn.execute(
            "SELECT * FROM sim_trade_plans WHERE executed=0 ORDER BY slot, id"
        ).fetchall()
        plans = []
        for r in rows:
            plans.append({
                "id": r["id"],
                "symbol": r["symbol"],
                "name": r["name"] or r["symbol"],
                "action": r["action"],
                "slot": r["slot"],
                "shares": r["shares"],
                "strategy_type": r["strategy_type"],
                "sell_reason": r.get("sell_reason"),
                "executed": r["executed"],
                "executed_at": r["executed_at"],
                "price": r["price"],
                "amount": r["amount"],
                "created_at": r["created_at"],
            })
        return plans
    except Exception as e:
        print(f"  [sim_executor] 加载计划失败: {e}", file=sys.stderr)
        return []
    finally:
        conn.close()


def _save_plans_to_db(plans: list[dict], conn):
    """批量插入新计划到 sim_trade_plans 表"""
    for plan in plans:
        conn.execute(
            "INSERT INTO sim_trade_plans "
            "(symbol, name, action, slot, shares, strategy_type, sell_reason, executed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (
                plan["symbol"],
                plan.get("name", plan["symbol"]),
                plan["action"],
                plan["slot"],
                plan["shares"],
                plan.get("strategy_type", "recommend"),
                plan.get("sell_reason"),
            ),
        )


def generate_plans(dry_run: bool = False) -> list[dict]:
    """检测池变更并生成交易计划

    入池标的: slot 1-3 各买入动态仓位 (CAPITAL_PER_SLOT/price 取整到手)
    出池标的: slot 1-3 各卖出同等仓位
    多维卖出触发: 止损/时间退出/止盈

    Args:
        dry_run: True=只打印不保存

    Returns:
        生成的计划列表
    """
    changes = detect_pool_changes()
    new_plans = []

    # ─── 1. 池变更计划 ────────────────────────────

    # 新入池 → 3笔买入计划
    for item in changes["new_entries"]:
        sym, name = item["symbol"], item["name"]
        # 获取价格以计算动态仓位
        price, _ = fetch_sina_price(sym)
        if price is None or price <= 0:
            price = 10.0  # 兜底价, 用10元估算
        shares = _compute_dynamic_shares(price)
        for slot in [1, 2, 3]:
            plan = {
                "symbol": sym,
                "name": name,
                "action": "buy",
                "slot": slot,
                "shares": shares,
                "strategy_type": "recommend",
                "sell_reason": None,
            }
            new_plans.append(plan)
        print(f"  [sim_executor] 计划: 买入 {sym} {name} (3 slot, 各{shares}股, 动态仓位)")

    # 出池 → 3笔卖出计划
    for item in changes["removed"]:
        sym, name = item["symbol"], item["name"]
        # 查询实际持仓
        conn = get_db(write=False)
        try:
            pos_row = conn.execute(
                "SELECT SUM(CASE WHEN action='buy' THEN shares ELSE -shares END) as net_shares "
                "FROM sim_trades WHERE symbol=?",
                (sym,),
            ).fetchone()
            net_shares = pos_row["net_shares"] if pos_row and pos_row["net_shares"] else 0
        except Exception:
            net_shares = 0
        finally:
            conn.close()

        if net_shares <= 0:
            net_shares = 30000  # 兜底 3万 股 (约1手 * 3slot = 300, 实际取决于价格)

        per_slot = max(net_shares // 3, LOT_SIZE) if net_shares >= 3 * LOT_SIZE else LOT_SIZE
        for slot in [1, 2, 3]:
            plan = {
                "symbol": sym,
                "name": name,
                "action": "sell",
                "slot": slot,
                "shares": per_slot,
                "strategy_type": "recommend",
                "sell_reason": "pool_removed",
            }
            new_plans.append(plan)
        print(f"  [sim_executor] 计划: 卖出 {sym} {name} (3 slot, 各{per_slot}股, 池移除)")

    # ─── 2. 多维卖出触发 ──────────────────────────
    conn = get_db(write=False)
    try:
        positions = _aggregate_positions(conn)
        if positions:
            syms = list(positions.keys())
            prices = fetch_batch_prices(syms)

            for sym, pos in positions.items():
                price_info = prices.get(sym, {})
                current_price = price_info.get("price")
                if not current_price or current_price <= 0:
                    continue

                avg_price = pos["avg_price"]
                if avg_price <= 0:
                    continue

                pnl_pct = (current_price - avg_price) / avg_price * 100
                total_shares = pos["shares"]

                # 检查是否已有该标的的卖出计划
                existing_sells = {p["symbol"] for p in new_plans if p["action"] == "sell"}
                if sym in existing_sells:
                    continue

                # 检查是否已有未执行的卖出计划
                existing_plans = _load_plans()
                has_pending_sell = any(
                    p["symbol"] == sym and p["action"] == "sell" for p in existing_plans
                )
                if has_pending_sell:
                    continue

                # ── 止损: 当前价 <= 持仓均价 * 0.92 ──
                if current_price <= avg_price * 0.92:
                    per_slot = max(total_shares // 3, LOT_SIZE) if total_shares >= 3 * LOT_SIZE else total_shares
                    for slot in [1, 2, 3]:
                        plan = {
                            "symbol": sym,
                            "name": pos["name"],
                            "action": "sell",
                            "slot": slot,
                            "shares": per_slot,
                            "strategy_type": pos["strategy_type"],
                            "sell_reason": "stop_loss",
                        }
                        new_plans.append(plan)
                    print(f"  [sim_executor] 止损计划: 卖出 {sym} {pos['name']} "
                          f"(现价{current_price:.2f} 均价{avg_price:.2f} PnL{pnl_pct:.1f}%)")
                    continue

                # ── 时间退出: 持仓 > 5日 且 pnl_pct < 3% ──
                holding_days = _count_holding_days(pos.get("earliest_buy_date", ""))
                if holding_days >= 5 and pnl_pct < 3.0:
                    per_slot = max(total_shares // 3, LOT_SIZE) if total_shares >= 3 * LOT_SIZE else total_shares
                    for slot in [1, 2, 3]:
                        plan = {
                            "symbol": sym,
                            "name": pos["name"],
                            "action": "sell",
                            "slot": slot,
                            "shares": per_slot,
                            "strategy_type": pos["strategy_type"],
                            "sell_reason": "time_exit",
                        }
                        new_plans.append(plan)
                    print(f"  [sim_executor] 时间退出: 卖出 {sym} {pos['name']} "
                          f"(持仓{holding_days}日 PnL{pnl_pct:.1f}%)")
                    continue

                # ── 止盈: pnl_pct >= 15% 且 RSI > 70 ──
                if pnl_pct >= 15.0:
                    rsi = _get_rsi(conn, sym)
                    if rsi is not None and rsi > 70:
                        half_shares = total_shares // 2
                        # 卖出50%, 均匀分配到3个slot
                        half_per_slot = max(half_shares // 3, LOT_SIZE) if half_shares >= 3 * LOT_SIZE else half_shares
                        for slot in [1, 2, 3]:
                            plan = {
                                "symbol": sym,
                                "name": pos["name"],
                                "action": "sell",
                                "slot": slot,
                                "shares": half_per_slot,
                                "strategy_type": pos["strategy_type"],
                                "sell_reason": "take_profit",
                            }
                            new_plans.append(plan)
                        print(f"  [sim_executor] 止盈计划: 卖出50% {sym} {pos['name']} "
                              f"(PnL{pnl_pct:.1f}% RSI{rsi:.1f})")
                        continue

                # ── 鳄鱼派信号退出 ──
                try:
                    from nous.engine.signals.crocodile_signals import evaluate_crocodile_signals
                    croc = evaluate_crocodile_signals(conn, date.today().isoformat())
                    croc_signals = croc.get('signals', {})

                    # 火车头低开退出
                    loco = croc_signals.get('locomotive', {})
                    if loco.get('status') == '低开预警':
                        per_slot = max(total_shares // 3, LOT_SIZE) if total_shares >= 3 * LOT_SIZE else total_shares
                        for slot in [1, 2, 3]:
                            plan = {
                                "symbol": sym, "name": pos["name"], "action": "sell",
                                "slot": slot, "shares": per_slot,
                                "strategy_type": pos["strategy_type"],
                                "sell_reason": "crocodile_loco_exit",
                            }
                            new_plans.append(plan)
                        print(f"  [sim_executor] 鳄鱼派火车头退出: 卖出 {sym} {pos['name']}")
                        continue

                    # 主线退潮退出
                    mainline = croc_signals.get('mainline', {})
                    if mainline.get('stage') == '退潮期':
                        per_slot = max(total_shares // 3, LOT_SIZE) if total_shares >= 3 * LOT_SIZE else total_shares
                        for slot in [1, 2, 3]:
                            plan = {
                                "symbol": sym, "name": pos["name"], "action": "sell",
                                "slot": slot, "shares": per_slot,
                                "strategy_type": pos["strategy_type"],
                                "sell_reason": "crocodile_mainline_exit",
                            }
                            new_plans.append(plan)
                        print(f"  [sim_executor] 鳄鱼派主线退潮退出: 卖出 {sym} {pos['name']}")
                        continue

                    # 拥挤度减仓
                    crowd = croc_signals.get('crowding', {})
                    if crowd.get('pct', 0) > 85 and pnl_pct > 5:
                        half_shares = total_shares // 2
                        half_per_slot = max(half_shares // 3, LOT_SIZE) if half_shares >= 3 * LOT_SIZE else half_shares
                        for slot in [1, 2, 3]:
                            plan = {
                                "symbol": sym, "name": pos["name"], "action": "sell",
                                "slot": slot, "shares": half_per_slot,
                                "strategy_type": pos["strategy_type"],
                                "sell_reason": "crocodile_crowding_reduce",
                            }
                            new_plans.append(plan)
                        print(f"  [sim_executor] 鳄鱼派拥挤度减仓: 卖出50% {sym} {pos['name']}")
                        continue

                    # 分歧期止盈(卖一半)
                    if mainline.get('stage') == '分歧期' and pnl_pct > 10:
                        half_shares = total_shares // 2
                        half_per_slot = max(half_shares // 3, LOT_SIZE) if half_shares >= 3 * LOT_SIZE else half_shares
                        for slot in [1, 2, 3]:
                            plan = {
                                "symbol": sym, "name": pos["name"], "action": "sell",
                                "slot": slot, "shares": half_per_slot,
                                "strategy_type": pos["strategy_type"],
                                "sell_reason": "crocodile_diverge_profit",
                            }
                            new_plans.append(plan)
                        print(f"  [sim_executor] 鳄鱼派分歧期止盈: 卖出50% {sym} {pos['name']}")
                        continue

                except Exception as e:
                    # 信号引擎失败不影响原有逻辑
                    pass

    except Exception as e:
        print(f"  [sim_executor] 卖出触发检测失败: {e}", file=sys.stderr)
    finally:
        conn.close()

    if dry_run:
        print(f"\n  [sim_executor] 干运行: 生成 {len(new_plans)} 条计划(未保存)")
        return new_plans

    if not new_plans:
        print("  [sim_executor] 无新计划生成")
        return []

    # 保存到数据库
    conn_w = get_db(write=True)
    try:
        _ensure_tables(conn_w)
        _save_plans_to_db(new_plans, conn_w)
        conn_w.commit()
        print(f"  [sim_executor] 保存 {len(new_plans)} 条新计划到 sim_trade_plans")
    except Exception as e:
        conn_w.rollback()
        print(f"  [sim_executor] 保存计划失败: {e}", file=sys.stderr)
    finally:
        conn_w.close()

    return new_plans


# ── Slot 执行 ─────────────────────────────────────

def _slot_time_passed(slot: int) -> bool:
    """判断指定 slot 的触发时间是否已到

    Slot 时间:
        1 -> 10:00
        2 -> 11:00
        3 -> 14:00
    """
    now = datetime.now()
    slot_str = SLOT_TIMES.get(slot)
    if not slot_str:
        return False

    hour, minute = map(int, slot_str.split(":"))
    slot_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now >= slot_dt


def _execute_plan(conn, plan: dict) -> dict:
    """执行一条交易计划, 在事务中写入 sim_trades + 更新 sim_trade_plans

    Returns:
        {"success": bool, "trade": dict|None, "error": str|None}
    """
    symbol = plan["symbol"]
    name = plan.get("name", symbol)
    action = plan["action"]
    slot = plan["slot"]
    shares = plan["shares"]
    strategy_type = plan.get("strategy_type", "recommend")
    sell_reason = plan.get("sell_reason")
    plan_id = plan.get("id")

    # 获取价格
    price, is_stale = fetch_sina_price(symbol)
    if price is None or price <= 0:
        return {"success": False, "error": f"无法获取 {symbol} 价格", "stale": False}

    trade_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    amount = round(price * shares, 2)
    stale_tag = " (stale)" if is_stale else ""

    try:
        conn.execute("BEGIN")

        # ── idempotent INSERT: UNIQUE 约束防重 ──
        cursor = conn.execute(
            "INSERT OR IGNORE INTO sim_trades "
            "(symbol, name, action, trade_time, slot, shares, price, amount, strategy_type, trade_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, date('now'))",
            (symbol, name, action, trade_time, slot, shares, price, amount, strategy_type),
        )

        if cursor.rowcount == 0:
            conn.execute("ROLLBACK")
            return {"success": False, "error": f"重复交易: {symbol} slot={slot} {action} 今日已执行"}

        # ── 更新 sim_trade_plans ──
        if plan_id:
            conn.execute(
                "UPDATE sim_trade_plans SET executed=1, executed_at=?, price=?, amount=? WHERE id=?",
                (trade_time, price, amount, plan_id),
            )

        # ── 卖出时更新 recommendation_history ──
        if action == "sell":
            # 计算持仓均价和盈亏
            entry_row = conn.execute(
                "SELECT SUM(CASE WHEN action='buy' THEN price*shares ELSE 0 END) as total_cost, "
                "SUM(CASE WHEN action='buy' THEN shares ELSE 0 END) as total_bought "
                "FROM sim_trades WHERE symbol=?",
                (symbol,),
            ).fetchone()
            entry_avg = 0
            pnl_pct_val = 0
            pnl_amount_val = 0
            if entry_row and entry_row["total_bought"] and entry_row["total_bought"] > 0:
                entry_avg = entry_row["total_cost"] / entry_row["total_bought"]
                pnl_pct_val = (price - entry_avg) / entry_avg * 100 if entry_avg > 0 else 0
                pnl_amount_val = (price - entry_avg) * shares

            status_map = {
                "stop_loss": "stopped_out",
                "time_exit": "time_exit",
                "take_profit": "take_profit",
                "pool_removed": "closed",
            }
            rh_status = status_map.get(sell_reason, "closed")

            try:
                conn.execute(
                    "UPDATE recommendation_history SET "
                    "exit_date=date('now'), "
                    "exit_avg_price=?, "
                    "pnl=?, "
                    "pnl_pct=?, "
                    "status=? "
                    "WHERE symbol=? AND status='active'",
                    (price, round(pnl_amount_val, 2), round(pnl_pct_val, 2), rh_status, symbol),
                )
            except Exception as rh_err:
                # recommendation_history 更新失败不阻塞交易
                print(f"  [sim_executor] recommendation_history 更新跳过: {rh_err}")

        conn.execute("COMMIT")

        print(f"    ✓ {'买入' if action == 'buy' else '卖出'} {symbol} {name} "
              f"slot={slot}: {shares}股 @ {price:.2f}{stale_tag}")

        return {
            "success": True,
            "trade": {
                "id": cursor.lastrowid,
                "symbol": symbol,
                "name": name,
                "action": action,
                "slot": slot,
                "shares": shares,
                "price": price,
                "amount": amount,
                "strategy_type": strategy_type,
                "stale": is_stale,
            },
        }

    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        return {"success": False, "error": str(e)}


def execute_pending_plans() -> list[dict]:
    """检查并执行所有到期的未执行计划

    Returns:
        执行结果列表
    """
    plans = _load_plans()
    pending = [p for p in plans if not p.get("executed")]

    if not pending:
        return []

    # 筛选当前时间可执行的 slot
    executable = []
    for p in pending:
        slot = p["slot"]
        if _slot_time_passed(slot):
            executable.append(p)

    if not executable:
        return []

    print(f"  [sim_executor] 待执行计划: {len(pending)} 条, 当前可执行: {len(executable)} 条")

    conn = get_db(write=True)
    results = []
    try:
        _ensure_tables(conn)

        for plan in executable:
            result = _execute_plan(conn, plan)
            results.append(result)
    except Exception as e:
        print(f"  [sim_executor] 执行批次失败: {e}", file=sys.stderr)
    finally:
        conn.close()

    return results


# ── 量化策略支持 ──────────────────────────────────

def generate_quant_plans(symbols: list[dict], dry_run: bool = False) -> list[dict]:
    """为量化模型标的生成交易计划

    量化模型: strategy_type='quant', slot 1 (10:00), 买入 1 手

    Args:
        symbols: [{"symbol": "...", "name": "..."}, ...]

    Returns:
        生成的计划列表
    """
    new_plans = []

    for item in symbols:
        sym, name = item["symbol"], item.get("name", "")
        plan = {
            "symbol": sym,
            "name": name,
            "action": "buy",
            "slot": 1,  # 一次性 10:00
            "shares": QUANT_SHARES,  # 1 手 = 100 股
            "strategy_type": "quant",
            "sell_reason": None,
        }
        new_plans.append(plan)
        print(f"  [sim_executor] [量化] 计划: 买入 {sym} {name} slot=1, {QUANT_SHARES}股")

    if dry_run:
        return new_plans

    if not new_plans:
        return []

    conn_w = get_db(write=True)
    try:
        _ensure_tables(conn_w)
        _save_plans_to_db(new_plans, conn_w)
        conn_w.commit()
        print(f"  [sim_executor] [量化] 保存 {len(new_plans)} 条量化计划到 sim_trade_plans")
    except Exception as e:
        conn_w.rollback()
        print(f"  [sim_executor] [量化] 保存计划失败: {e}", file=sys.stderr)
    finally:
        conn_w.close()

    return new_plans


# ── 主采集函数 ────────────────────────────────────

def execute_cycle() -> bool:
    """执行一次完整周期: 生成计划 + 执行到期计划

    兜底逻辑: 如果无待执行计划且当前 < 14:00, 触发计划生成

    Returns:
        True=至少一个计划执行成功, False=无操作或全部失败
    """
    if not is_trading_day():
        return True  # 非交易日不报告失败

    now = datetime.now()
    now_ts = now.strftime("%H:%M:%S")
    t = now.hour * 100 + now.minute

    heartbeat("sim_executor")

    # 检查当前是否在交易时段 (09:15 - 15:00)
    if t < 915 or t > 1500:
        return True

    # ── 兜底计划生成: 无待执行计划且尚未收盘 ──
    pending = _load_plans()
    if not pending and t < 1400:
        print(f"  [sim_executor] {now_ts} 无待执行计划, 执行兜底计划生成 ...")
        generate_plans()

    executed = execute_pending_plans()
    if executed:
        success_count = sum(1 for r in executed if r.get("success"))
        fail_count = sum(1 for r in executed if not r.get("success"))
        print(f"  [sim_executor] {now_ts} 执行 {len(executed)} 笔: "
              f"{success_count} 成功, {fail_count} 失败")
        return success_count > 0

    return True


# ── 首次运行初始化 ────────────────────────────────

def initialize_today_plans():
    """每日首次运行: 检测池变更并生成当日计划

    在 09:15-09:29 之间调用, 为当天生成交易计划
    """
    now = datetime.now()
    today = date.today()
    t = now.hour * 100 + now.minute

    if not is_trading_day():
        print(f"  [sim_executor] {today} 非交易日, 跳过计划生成")
        return

    # 检查今天是否已经生成过计划
    plans = _load_plans()
    if plans:
        print(f"  [sim_executor] 今日已有 {len(plans)} 条待执行计划, 跳过重新生成")
        return

    print(f"\n{'='*50}")
    print(f"sim_executor — {today} 计划生成")
    print(f"{'='*50}")

    # 生成推荐策略计划
    generate_plans()

    # 检查是否有量化信号
    _check_quant_signals()

    print(f"  [sim_executor] 计划生成完成")


def _check_quant_signals():
    """检查量化信号表, 为有信号的标的生成量化交易计划"""
    try:
        conn = get_db(write=False)
        try:
            # 从 quant_signals 表获取今日信号
            signals = conn.execute(
                "SELECT symbol, signal_type, confidence FROM quant_signals "
                "WHERE signal_type='entry' AND confidence > 0.7 "
                "AND date(signal_date) = date('now')"
            ).fetchall()

            if signals:
                symbols_to_buy = []
                for s in signals:
                    name = _get_name(conn, s["symbol"])
                    symbols_to_buy.append({"symbol": s["symbol"], "name": name})
                    print(f"  [sim_executor] [量化信号] {s['symbol']} {name} "
                          f"(confidence={s['confidence']:.2f})")

                if symbols_to_buy:
                    generate_quant_plans(symbols_to_buy)
            else:
                print("  [sim_executor] [量化] 今日无量化信号")

        except Exception:
            print("  [sim_executor] [量化] quant_signals 表不可用或无数据")
        finally:
            conn.close()
    except Exception as e:
        print(f"  [sim_executor] [量化] 检查失败: {e}")


# ── 入口 ──────────────────────────────────────────

def main():
    """独立运行入口 — 执行一次完整周期"""
    print(f"\n{'='*50}")
    print(f"sim_executor — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    # 确保基础设施
    conn = get_db(write=True)
    try:
        _ensure_tables(conn)
    finally:
        conn.close()

    t = datetime.now().hour * 100 + datetime.now().minute

    if 915 <= t <= 929:
        # 计划生成时段
        initialize_today_plans()
    elif 930 <= t <= 1500:
        # 执行时段
        execute_cycle()
    else:
        print(f"  [sim_executor] 当前 {datetime.now().strftime('%H:%M')} 非交易时段")


def main_loop():
    """持续运行模式 — 每15s检查一次

    09:15-09:29 生成计划
    09:30-15:00 执行计划
    """
    print("sim_executor 持续模式启动, 每15s检查一次")

    # 确保基础设施
    conn = get_db(write=True)
    try:
        _ensure_tables(conn)
    finally:
        conn.close()

    has_initialized = False

    try:
        while True:
            heartbeat("sim_executor")
            now = datetime.now()

            if not is_trading_day():
                time.sleep(300)
                continue

            t = now.hour * 100 + now.minute

            if t < 915:
                # 盘前等待
                time.sleep(60)
                continue
            elif t > 1500:
                # 盘后, 等明天
                print(f"  [sim_executor] {now.strftime('%H:%M')} 已收盘, 等待下一个交易日")
                time.sleep(3600)
                continue
            elif 915 <= t <= 929 and not has_initialized:
                initialize_today_plans()
                has_initialized = True
                time.sleep(15)
                continue
            elif t >= 930:
                execute_cycle()
                time.sleep(15)
                continue
            else:
                time.sleep(15)
    except Exception as e:
        print(f"  [sim_executor] FATAL: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
