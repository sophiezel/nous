"""sim_pnl_tracker — 模拟盘盈亏追踪

每60s从 sim_trades 聚合持仓 → 获取最新价 → 计算浮动盈亏
写入 sim_pnl_snapshot(每标的每slot) + sim_portfolio_snapshot(总净值)
日回撤>5%告警

关键约束:
  - 价格获取: resilient_fetch('sina', ...)
  - DB写入: get_db(write=True) from src.storage
  - 心跳: heartbeat('sim_pnl_tracker')
  - 优雅降级: 实时价不可用时使用上一笔价格(stale标记)
"""

import sys
import time
import re
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, Any
from collections import defaultdict

# ── 路径 ──────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── 自愈框架导入 ──────────────────────────────────
from nous.data.collectors import heartbeat, resilient_fetch, collector_main_loop

# ── DB导入 ─────────────────────────────────────────
from nous.data.storage import get_db, with_retry

# ── 常量 ──────────────────────────────────────────
INITIAL_CAPITAL = 1_000_000  # 初始模拟资本 100万
MAX_DAILY_DRAWDOWN_PCT = 5.0  # 日回撤告警阈值
TRADING_DAYS = 2  # 计算日回撤的天数

DDL_PNL = """
CREATE TABLE IF NOT EXISTS sim_pnl_snapshot (
    symbol       TEXT NOT NULL,
    datetime     TEXT NOT NULL,
    slot         INTEGER NOT NULL DEFAULT 0,
    entry_price  REAL,
    current_price REAL,
    pnl_pct      REAL,
    pnl_amount   REAL,
    PRIMARY KEY (symbol, datetime, slot)
);
CREATE INDEX IF NOT EXISTS idx_pnl_dt ON sim_pnl_snapshot(datetime);
"""

DDL_PORTFOLIO = """
CREATE TABLE IF NOT EXISTS sim_portfolio_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    datetime        TEXT NOT NULL,
    strategy_type   TEXT NOT NULL DEFAULT 'recommend',
    total_cost      REAL DEFAULT 0,
    market_value    REAL DEFAULT 0,
    pnl_pct         REAL,
    position_count  INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_portfolio_dt ON sim_portfolio_snapshot(datetime);
"""

# Sina 行情 API 前缀映射 (与 minute_collector 保持一致)
MARKET_PREFIX = {
    "6": "sh", "5": "sh", "9": "sh",
    "0": "sz", "3": "sz", "2": "sz",
    "8": "bj", "4": "bj",
}

# 日内缓存: 一轮聚合内共享价格查询结果
_price_cache = {}


# ── 辅助函数 ──────────────────────────────────────

def symbol_to_sina(symbol: str) -> Optional[str]:
    """将纯数字代码转为 Sina API 格式"""
    sym = symbol.strip().zfill(6)
    for k, v in MARKET_PREFIX.items():
        if sym.startswith(k):
            return f"{v}{sym}"
    return f"sz{sym}"


def _ensure_tables(conn):
    """确保所有依赖表存在"""
    conn.executescript(DDL_PNL)
    conn.executescript(DDL_PORTFOLIO)
    conn.commit()


# ── 持仓聚合 ──────────────────────────────────────

def aggregate_positions(conn) -> dict:
    """从 sim_trades 聚合当前持仓

    根据所有已执行的交易记录, 按 symbol+slot 计算净持仓:
      - 买入增加持仓
      - 卖出减少持仓
    净持仓 > 0 的才计入活跃持仓

    Returns:
        {
            symbol: {
                "name": str,
                "slots": {
                    slot: {
                        "shares": int,
                        "total_cost": float,  # 总成本(元)
                        "entry_price": float,  # 均价
                    }
                },
                "total_shares": int,
                "total_cost": float,
            },
            ...
        }
    """
    positions = {}  # (symbol, slot) -> {shares, cost}

    trades = conn.execute(
        "SELECT symbol, name, action, slot, shares, price, pnl_amount "
        "FROM sim_trades ORDER BY trade_time ASC"
    ).fetchall()

    names = {}

    for t in trades:
        key = (t["symbol"], t["slot"])
        names[t["symbol"]] = t["name"] or t["symbol"]

        if key not in positions:
            positions[key] = {"shares": 0, "cost": 0.0}

        if t["action"] == "buy":
            positions[key]["shares"] += t["shares"]
            positions[key]["cost"] += t["shares"] * t["price"]
        elif t["action"] == "sell":
            # 卖出按比例减少成本和股数
            sell_shares = min(t["shares"], positions[key]["shares"])
            if positions[key]["shares"] > 0:
                avg_cost = positions[key]["cost"] / positions[key]["shares"]
                positions[key]["shares"] -= sell_shares
                positions[key]["cost"] -= sell_shares * avg_cost
            # 如果全卖光了, 重置为0
            if positions[key]["shares"] <= 0:
                positions[key] = {"shares": 0, "cost": 0.0}

    # 聚合为按 symbol 的结构
    result = {}
    for (sym, slot), pos in positions.items():
        if pos["shares"] <= 0:
            continue

        if sym not in result:
            result[sym] = {
                "name": names.get(sym, sym),
                "slots": {},
                "total_shares": 0,
                "total_cost": 0.0,
            }

        entry_price = round(pos["cost"] / pos["shares"], 4) if pos["shares"] > 0 else 0
        result[sym]["slots"][slot] = {
            "shares": pos["shares"],
            "total_cost": round(pos["cost"], 2),
            "entry_price": entry_price,
        }
        result[sym]["total_shares"] += pos["shares"]
        result[sym]["total_cost"] += pos["cost"]

    return result


def aggregate_by_strategy(conn) -> dict:
    """按 strategy_type 聚合持仓

    Returns:
        {
            "recommend": {
                "total_cost": float,
                "market_value": float,  # 需外部填充
                "position_count": int,
            },
            "quant": ...
        }
    """
    result = defaultdict(lambda: {"total_cost": 0.0, "market_value": 0.0, "position_count": 0})

    trades = conn.execute(
        "SELECT symbol, action, slot, shares, price, strategy_type "
        "FROM sim_trades ORDER BY trade_time ASC"
    ).fetchall()

    positions = {}  # (symbol, strategy_type, slot) -> {shares, cost}

    for t in trades:
        key = (t["symbol"], t["strategy_type"] or "recommend", t["slot"])
        if key not in positions:
            positions[key] = {"shares": 0, "cost": 0.0}

        if t["action"] == "buy":
            positions[key]["shares"] += t["shares"]
            positions[key]["cost"] += t["shares"] * t["price"]
        elif t["action"] == "sell":
            sell_shares = min(t["shares"], positions[key]["shares"])
            if positions[key]["shares"] > 0:
                avg_cost = positions[key]["cost"] / positions[key]["shares"]
                positions[key]["shares"] -= sell_shares
                positions[key]["cost"] -= sell_shares * avg_cost
            if positions[key]["shares"] <= 0:
                positions[key] = {"shares": 0, "cost": 0.0}

    for (sym, strategy, slot), pos in positions.items():
        if pos["shares"] > 0:
            result[strategy]["total_cost"] += pos["cost"]
            result[strategy]["position_count"] += 1

    return dict(result)


# ── 价格获取(自愈) ─────────────────────────────────

def _fetch_prices_batch(symbols: list[str]) -> dict[str, dict]:
    """批量获取最新价, 使用 resilient_fetch 包装

    Args:
        symbols: 纯数字代码列表

    Returns:
        {symbol: {"price": float, "stale": bool}}
    """
    if not symbols:
        return {}

    # 检查缓存
    uncached = [s for s in symbols if s not in _price_cache]
    if uncached:
        _batch_fetch_from_sina(uncached)

    return {s: _price_cache.get(s, {"price": 0, "stale": True}) for s in symbols}


def _batch_fetch_from_sina(symbols: list[str]):
    """从 Sina API 批量获取行情, 写入 _price_cache"""
    if not symbols:
        return

    # 使用 resilient_fetch 包装 Sina 请求
    def _fetch():
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

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            sina_codes = [symbol_to_sina(s) for s in batch]
            sina_codes = [c for c in sina_codes if c is not None]

            if not sina_codes:
                continue

            url = f"http://hq.sinajs.cn/list={','.join(sina_codes)}"
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
                fields = match.group(2).split(",")
                if len(fields) < 32:
                    continue

                pure_symbol = "".join(ch for ch in match.group(1) if ch.isdigit())
                try:
                    price = float(fields[3]) if fields[3] else 0
                    if price > 0:
                        result[pure_symbol] = {"price": price, "stale": False}
                except (ValueError, IndexError):
                    pass

            if i + batch_size < len(symbols):
                time.sleep(0.3)

        return result

    def _fallback():
        """降级: 从 intraday_minute 或 stock_daily 获取"""
        result = {}
        conn = get_db(write=False)
        try:
            for sym in symbols:
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
        finally:
            conn.close()
        if not result:
            raise ValueError(f"No fallback prices for any of {len(symbols)} symbols")
        return result

    prices, status = resilient_fetch("sina", _fetch, fallback_fn=_fallback)

    if prices and isinstance(prices, dict):
        _price_cache.update(prices)

    # 标记未获取到的标的为 stale
    for sym in symbols:
        if sym not in _price_cache:
            # 尝试单独降级
            conn = get_db(write=False)
            try:
                row = conn.execute(
                    "SELECT price FROM intraday_minute WHERE symbol=? ORDER BY datetime DESC LIMIT 1",
                    (sym,),
                ).fetchone()
                if row and row["price"] and row["price"] > 0:
                    _price_cache[sym] = {"price": row["price"], "stale": True}
                    continue
                row2 = conn.execute(
                    "SELECT close FROM stock_daily WHERE symbol=? ORDER BY trade_date DESC LIMIT 1",
                    (sym,),
                ).fetchone()
                if row2 and row2["close"] and row2["close"] > 0:
                    _price_cache[sym] = {"price": row2["close"], "stale": True}
                    continue
            finally:
                conn.close()
            _price_cache[sym] = {"price": 0, "stale": True}


def clear_price_cache():
    """清除价格缓存, 下次调用重新获取"""
    _price_cache.clear()


# ── PnL 计算 & 写入 ──────────────────────────────

def calculate_and_save_pnl() -> int:
    """计算所有持仓的浮动盈亏并写入快照表

    Returns:
        写入的 PnL 快照条数
    """
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    today = date.today()

    print(f"\n{'='*50}")
    print(f"sim_pnl_tracker — {now_str}")
    print(f"{'='*50}")

    conn = get_db(write=True)
    try:
        _ensure_tables(conn)

        # 1. 聚合持仓
        positions = aggregate_positions(conn)
        if not positions:
            print("  [sim_pnl_tracker] 当前无持仓")
            # 仍然写入空组合快照
            _save_portfolio_snapshot(conn, now_str, {}, {})
            return 0

        symbols = list(positions.keys())
        print(f"  [sim_pnl_tracker] 活跃持仓: {len(positions)} 只标的")

        # 2. 获取最新价
        clear_price_cache()
        prices = _fetch_prices_batch(symbols)
        stale_count = sum(1 for v in prices.values() if v.get("stale"))
        fresh_count = sum(1 for v in prices.values() if not v.get("stale"))
        print(f"  [sim_pnl_tracker] 行情: {fresh_count} 实时, {stale_count} 降级")

        # 3. 计算 PnL 并写入
        inserted = 0
        for sym in sorted(positions.keys()):
            pos_info = positions[sym]
            price_info = prices.get(sym, {"price": 0, "stale": True})
            current_price = price_info.get("price", 0)

            if current_price <= 0:
                stale_tag = " [无价]"
            elif price_info.get("stale"):
                stale_tag = " [降级]"
            else:
                stale_tag = ""

            for slot in sorted(pos_info["slots"].keys()):
                slot_info = pos_info["slots"][slot]
                entry_price = slot_info["entry_price"]
                shares = slot_info["shares"]

                if entry_price <= 0 or shares <= 0:
                    continue

                if current_price > 0:
                    pnl_amount = round((current_price - entry_price) * shares, 2)
                    pnl_pct = round((current_price - entry_price) / entry_price * 100, 2)
                else:
                    pnl_amount = 0.0
                    pnl_pct = 0.0

                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO sim_pnl_snapshot "
                        "(symbol, datetime, slot, entry_price, current_price, pnl_pct, pnl_amount) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (sym, now_str, slot, entry_price, current_price, pnl_pct, pnl_amount),
                    )
                    inserted += 1
                except Exception as e:
                    print(f"    ✗ {sym} slot {slot}: 写入失败 {e}", file=sys.stderr)

            # 打印持仓摘要
            total_shares = pos_info["total_shares"]
            avg_cost = round(pos_info["total_cost"] / total_shares, 2) if total_shares > 0 else 0
            total_mv = round(current_price * total_shares, 2) if current_price > 0 else 0
            total_pnl = round(total_mv - pos_info["total_cost"], 2) if current_price > 0 else 0
            total_pnl_pct = round((current_price - avg_cost) / avg_cost * 100, 2) if (current_price > 0 and avg_cost > 0) else 0
            print(f"    {sym} {pos_info['name']}: {total_shares}股 | "
                  f"均价 {avg_cost:.2f} → 现价 {current_price:.2f} | "
                  f"浮动 {total_pnl:+.0f}元 ({total_pnl_pct:+.2f}%){stale_tag}")

        conn.commit()

        # 4. 写入组合快照
        strategy_agg = aggregate_by_strategy(conn)

        # 填充市值
        for sym, pos_info in positions.items():
            price_info = prices.get(sym, {"price": 0})
            current_price = price_info.get("price", 0)
            # 按 strategy_type 分配市值 (简化: 所有标的都算 recommend)
            strategy_agg.setdefault("recommend", {"total_cost": 0.0, "market_value": 0.0, "position_count": 0})
            strategy_agg["recommend"]["market_value"] += current_price * pos_info["total_shares"]

        _save_portfolio_snapshot(conn, now_str, strategy_agg, positions)

        # 5. 检查日回撤
        check_daily_drawdown(conn, now_str, positions, prices)

        now_ts = now.strftime("%H:%M:%S")
        print(f"\n  [sim_pnl_tracker] {now_ts} 写入 {inserted} 条 PnL 快照")
        return inserted

    except Exception as e:
        print(f"  [sim_pnl_tracker] 计算失败: {e}", file=sys.stderr)
        conn.rollback()
        return 0
    finally:
        conn.close()


def _save_portfolio_snapshot(conn, now_str: str, strategy_agg: dict, positions: dict):
    """写入组合快照到 sim_portfolio_snapshot

    Args:
        strategy_agg: {strategy_type: {total_cost, market_value, position_count}}
        positions: aggregate_positions() 的输出
    """
    total_cost = sum(s.get("total_cost", 0) for s in strategy_agg.values())
    total_mv = sum(s.get("market_value", 0) for s in strategy_agg.values())
    total_positions = sum(s.get("position_count", 0) for s in strategy_agg.values())

    # 也支持按策略写入
    for strategy, agg in strategy_agg.items():
        cost = agg.get("total_cost", 0)
        mv = agg.get("market_value", 0)
        count = agg.get("position_count", 0)
        pnl_pct = round((mv - cost) / cost * 100, 2) if cost > 0 else 0.0

        try:
            conn.execute(
                "INSERT INTO sim_portfolio_snapshot "
                "(datetime, strategy_type, total_cost, market_value, pnl_pct, position_count) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now_str, strategy, round(cost, 2), round(mv, 2), pnl_pct, count),
            )
        except Exception as e:
            print(f"  [sim_pnl_tracker] 组合快照写入失败 ({strategy}): {e}", file=sys.stderr)

    # 总组合
    total_pnl_pct = round((total_mv + (0 if total_mv >= total_cost else 0)) / max(total_cost, 1) * 100 - 100, 2) if total_cost > 0 else 0.0

    print(f"  [sim_pnl_tracker] 组合总览: 成本 {total_cost:.0f} | "
          f"市值 {total_mv:.0f} | 盈亏 {total_pnl_pct:+.2f}% | "
          f"持仓 {total_positions} 只")
    conn.commit()


# ── 日回撤告警 ────────────────────────────────────

def check_daily_drawdown(conn, now_str: str, positions: dict, prices: dict) -> bool:
    """检查日回撤, >5% 告警

    日回撤 = (当前市值 - 当日最高市值) / 当日最高市值
    (简化为 当前PnL% - 当日最高PnL%)

    实际实现: 计算今日最高净值 vs 当前净值

    Returns:
        True=触发告警
    """
    today = date.today().isoformat()

    try:
        # 获取今日已写入的 portfolio 快照
        snapshots = conn.execute(
            "SELECT datetime, market_value, total_cost FROM sim_portfolio_snapshot "
            "WHERE strategy_type='recommend' AND datetime >= ? "
            "ORDER BY datetime ASC",
            (f"{today} 00:00:00",),
        ).fetchall()

        if len(snapshots) < 2:
            return False  # 数据不足, 无法计算回撤

        # 计算每个快照的 PnL%
        pnl_values = []
        for s in snapshots:
            cost = s["total_cost"] or 1
            pnl_pct = (s["market_value"] - cost) / cost * 100
            pnl_values.append(pnl_pct)

        current_pnl = pnl_values[-1]
        peak_pnl = max(pnl_values)
        drawdown = current_pnl - peak_pnl  # 回撤为负值

        if drawdown < -MAX_DAILY_DRAWDOWN_PCT:
            alert_msg = (
                f"\n  ⚠⚠⚠ 日回撤告警 ⚠⚠⚠\n"
                f"  当前PnL: {current_pnl:.2f}%\n"
                f"  日内峰值: {peak_pnl:.2f}%\n"
                f"  回撤: {drawdown:.2f}%\n"
                f"  阈值: -{MAX_DAILY_DRAWDOWN_PCT}%\n"
                f"  时间: {now_str}\n"
            )
            print(alert_msg, file=sys.stderr)

            # 写入告警日志(可选)
            _write_alert_log(conn, now_str, drawdown, current_pnl, peak_pnl)

            return True

        print(f"  [sim_pnl_tracker] 日回撤: {drawdown:.2f}% "
              f"(峰值 {peak_pnl:.2f}%, 当前 {current_pnl:.2f}%)")
        return False

    except Exception as e:
        print(f"  [sim_pnl_tracker] 回撤检查失败: {e}", file=sys.stderr)
        return False


def _write_alert_log(conn, now_str: str, drawdown: float, current_pnl: float, peak_pnl: float):
    """写入告警日志到 sim_drawdown_alerts 表"""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sim_drawdown_alerts ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "alert_time TEXT NOT NULL,"
            "drawdown_pct REAL,"
            "current_pnl_pct REAL,"
            "peak_pnl_pct REAL"
            ")"
        )
        conn.execute(
            "INSERT INTO sim_drawdown_alerts (alert_time, drawdown_pct, current_pnl_pct, peak_pnl_pct) "
            "VALUES (?, ?, ?, ?)",
            (now_str, round(drawdown, 2), round(current_pnl, 2), round(peak_pnl, 2)),
        )
        conn.commit()
    except Exception:
        pass


# ── 主采集函数 ────────────────────────────────────

def update_pnl_cycle() -> bool:
    """执行一次 PnL 更新周期

    Returns:
        True=成功, False=失败
    """
    heartbeat("sim_pnl_tracker")

    now = datetime.now()

    # 非交易日跳过(但不报失败)
    if now.weekday() >= 5:
        return True

    try:
        count = calculate_and_save_pnl()
        return count >= 0  # 0 也是成功(无持仓)
    except Exception as e:
        print(f"  [sim_pnl_tracker] 更新失败: {e}", file=sys.stderr)
        return False


# ── 入口 ──────────────────────────────────────────

def main():
    """独立运行入口 — 执行一次 PnL 计算"""
    print(f"\n{'='*50}")
    print(f"sim_pnl_tracker — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    # 确保表存在
    conn = get_db(write=True)
    try:
        _ensure_tables(conn)
    finally:
        conn.close()

    update_pnl_cycle()


def main_loop(interval: int = 60):
    """持续运行模式 — 每60s更新一次盈亏

    使用 collector_main_loop 骨架, 但我们的轮询逻辑需要
    每60秒执行, 所以直接使用 while 循环。

    Args:
        interval: 更新间隔(秒), 默认60
    """
    print(f"sim_pnl_tracker 持续模式启动, 每{interval}s更新一次")

    # 确保表存在
    conn = get_db(write=True)
    try:
        _ensure_tables(conn)
    finally:
        conn.close()

    consecutive_failures = 0

    while True:
        try:
            heartbeat("sim_pnl_tracker")
            now = datetime.now()

            if now.weekday() < 5:  # 交易日
                success = calculate_and_save_pnl()
                if success >= 0:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
            else:
                print(f"  [sim_pnl_tracker] {now.strftime('%Y-%m-%d')} 非交易日, 等待")
                # 非交易日检查间隔加大
                time.sleep(3600)
                continue

            if consecutive_failures >= 10:
                print(f"  [sim_pnl_tracker] FATAL: {consecutive_failures} 次连续失败",
                      file=sys.stderr)
                # 不自杀, 继续尝试

            time.sleep(interval)

        except KeyboardInterrupt:
            print(f"\n  [sim_pnl_tracker] 收到中断, 退出")
            break
        except Exception as e:
            print(f"  [sim_pnl_tracker] 循环异常: {e}", file=sys.stderr)
            consecutive_failures += 1
            time.sleep(interval)


if __name__ == "__main__":
    main()
