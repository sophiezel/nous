"""模型交易日志: 每笔交易记录模型分数、市场状态、因子贡献

日志格式: JSONL (逐行追加, 不易损坏)
存储位置: ~/wiki/finance/reports/model_trades/trades_YYYYMM.jsonl
"""
import json
import sys
from pathlib import Path
from datetime import date, datetime
from typing import Optional


LOG_DIR = Path.home() / "wiki" / "finance" / "reports" / "model_trades"


def _ensure_log_dir() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR


def _log_path() -> Path:
    _ensure_log_dir()
    return LOG_DIR / f"trades_{date.today().strftime('%Y%m')}.jsonl"


def _append_log(record: dict):
    """追加一条记录到当月日志文件"""
    path = _log_path()
    record["_logged_at"] = datetime.now().isoformat()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


class ModelTradeLogger:
    """模型选股交易日志记录器"""

    @staticmethod
    def log_buy(
        symbol: str,
        buy_price: float,
        model_score: float,
        regime: str,
        top_factors: list,
        amount: float,
        strategy: str = "short_term",
        sector: str = "",
        name: str = "",
    ):
        """记录一笔买入"""
        record = {
            "event": "buy",
            "symbol": symbol,
            "name": name,
            "buy_date": date.today().isoformat(),
            "buy_price": round(buy_price, 4),
            "model_score": round(model_score, 3),
            "regime": regime,
            "top_factors": (top_factors or [])[:5],
            "amount": round(amount, 2),
            "strategy": strategy,
            "sector": sector,
            "status": "holding",
        }
        _append_log(record)

    @staticmethod
    def log_sell(
        symbol: str,
        sell_price: float,
        buy_record: Optional[dict] = None,
        pnl_pct: Optional[float] = None,
        reason: str = "",
    ):
        """记录一笔卖出, 关联买入记录或直接记录盈亏

        Args:
            symbol: 股票代码
            sell_price: 卖出价格
            buy_record: 原始买入记录 (从日志读取)
            pnl_pct: 盈亏比例 (直接传入)
            reason: 卖出原因 (stop_loss/take_profit/manual)
        """
        record = {
            "event": "sell",
            "symbol": symbol,
            "sell_date": date.today().isoformat(),
            "sell_price": round(sell_price, 4),
            "reason": reason,
            "status": "closed",
        }

        if buy_record:
            # 从买入记录继承元数据
            record["buy_date"] = buy_record.get("buy_date", "")
            record["buy_price"] = buy_record.get("buy_price", 0)
            record["model_score"] = buy_record.get("model_score")
            record["regime"] = buy_record.get("regime")
            record["strategy"] = buy_record.get("strategy", "")
            record["sector"] = buy_record.get("sector", "")
            record["amount"] = buy_record.get("amount", 0)
            bp = float(buy_record.get("buy_price", 0) or 0)
            if bp > 0:
                record["pnl_pct"] = round((sell_price - bp) / bp * 100, 2)
            elif pnl_pct is not None:
                record["pnl_pct"] = round(pnl_pct, 2)
        else:
            record["pnl_pct"] = round(pnl_pct, 2) if pnl_pct is not None else 0

        _append_log(record)

    @staticmethod
    def update_stop(status: str, symbol: str, sl_price: float, tp_price: float):
        """记录止损/止盈触发"""
        _append_log({
            "event": "stop_update",
            "symbol": symbol,
            "date": date.today().isoformat(),
            "status": status,
            "stop_loss_price": round(sl_price, 4),
            "take_profit_price": round(tp_price, 4),
        })

    @staticmethod
    def get_summary(days: int = 30) -> dict:
        """最近 N 天交易汇总"""
        records = _load_recent_logs(days)
        if not records:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0,
                "avg_pnl_pct": 0,
                "avg_win_pct": 0,
                "avg_loss_pct": 0,
                "profit_factor": 0,
                "total_pnl_pct": 0,
            }

        buys = {r["symbol"]: r for r in records if r.get("event") == "buy"}
        sells = [r for r in records if r.get("event") == "sell"]

        # 计算胜率
        wins = [s for s in sells if s.get("pnl_pct", 0) > 0]
        losses = [s for s in sells if s.get("pnl_pct", 0) <= 0]
        total_closed = len(sells)

        win_rate = len(wins) / total_closed if total_closed > 0 else 0
        avg_pnl = sum(s.get("pnl_pct", 0) for s in sells) / total_closed if total_closed > 0 else 0
        avg_win = sum(s.get("pnl_pct", 0) for s in wins) / len(wins) if wins else 0
        avg_loss = sum(s.get("pnl_pct", 0) for s in losses) / len(losses) if losses else 0

        total_win = sum(s.get("pnl_pct", 0) for s in wins)
        total_loss = abs(sum(s.get("pnl_pct", 0) for s in losses))
        profit_factor = total_win / total_loss if total_loss > 0 else float("inf")

        return {
            "period_days": days,
            "total_buys": len(buys),
            "total_closed": total_closed,
            "still_holding": len(buys) - total_closed,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate * 100, 2),
            "avg_pnl_pct": round(avg_pnl, 2),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "total_pnl_pct": round(sum(s.get("pnl_pct", 0) for s in sells), 2),
        }

    @staticmethod
    def get_recent_buys(days: int = 7) -> list[dict]:
        """最近 N 天的买入记录"""
        records = _load_recent_logs(days)
        buys = [r for r in records if r.get("event") == "buy"]
        # 去重保留最新
        seen = {}
        for b in buys:
            seen[b["symbol"]] = b
        return list(seen.values())

    @staticmethod
    def get_buy_record(symbol: str) -> Optional[dict]:
        """查询某股票最近一条买入记录"""
        records = _load_recent_logs(90)
        for r in reversed(records):
            if r.get("event") == "buy" and r.get("symbol") == symbol:
                return r
        return None


def _load_recent_logs(days: int = 30) -> list[dict]:
    """从当月和上月日志加载最近 N 天记录"""
    records = []
    today = date.today()

    # 尝试当前月和上个月
    for offset in [0, -1]:
        if offset == 0:
            ym = today.strftime("%Y%m")
        else:
            m = today.month - 1
            y = today.year
            if m == 0:
                m = 12
                y -= 1
            ym = f"{y}{m:02d}"

        path = LOG_DIR / f"trades_{ym}.jsonl"
        if path.exists():
            try:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                records.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            except OSError:
                continue

    # 按时间过滤
    cutoff = date.today()
    try:
        from datetime import timedelta
        cutoff = date.today() - timedelta(days=days)
    except Exception:
        pass

    filtered = []
    for r in records:
        d = r.get("buy_date") or r.get("sell_date") or ""
        if d:
            try:
                rd = date.fromisoformat(d)
                if rd >= cutoff:
                    filtered.append(r)
            except (ValueError, TypeError):
                filtered.append(r)
        else:
            filtered.append(r)

    return filtered


def read_logs_for_symbol(symbol: str, months: int = 3) -> list[dict]:
    """读取某只股票的所有日志记录"""
    records = []
    today = date.today()
    for m_offset in range(months):
        m = today.month - m_offset
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        ym = f"{y}{m:02d}"
        path = LOG_DIR / f"trades_{ym}.jsonl"
        if path.exists():
            try:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                r = json.loads(line)
                                if r.get("symbol") == symbol:
                                    records.append(r)
                            except json.JSONDecodeError:
                                continue
            except OSError:
                continue
    records.sort(key=lambda x: x.get("buy_date", "") or x.get("sell_date", ""))
    return records


if __name__ == "__main__":
    # CLI 测试
    import argparse
    parser = argparse.ArgumentParser(description="模型交易日志工具")
    parser.add_argument("--summary", action="store_true", help="打印交易汇总")
    parser.add_argument("--days", type=int, default=30, help="汇总天数")
    args = parser.parse_args()

    if args.summary:
        summary = ModelTradeLogger.get_summary(days=args.days)
        print(f"\n{'='*60}")
        print(f"  模型交易日志汇总 (近{args.days}天)")
        print(f"{'='*60}")
        print(f"  总买入:     {summary['total_buys']}")
        print(f"  已平仓:     {summary['total_closed']}")
        print(f"  持仓中:     {summary['still_holding']}")
        print(f"  胜率:       {summary['win_rate']:.1f}%")
        print(f"  平均盈亏:   {summary['avg_pnl_pct']:+.2f}%")
        print(f"  平均盈利:   {summary['avg_win_pct']:+.2f}%")
        print(f"  平均亏损:   {summary['avg_loss_pct']:+.2f}%")
        print(f"  盈亏比:     {summary['profit_factor']:.2f}")
        print(f"  总盈亏:     {summary['total_pnl_pct']:+.2f}%")
        print(f"{'='*60}\n")
    else:
        # 简单测试: 写入一条测试记录
        ModelTradeLogger.log_buy(
            symbol="000001", buy_price=12.5, model_score=8.2,
            regime="SIDEWAYS", top_factors=["K3_std_60d", "K4_vwap"],
            amount=10000, strategy="short_term",
        )
        print("测试日志已写入:", _log_path())
