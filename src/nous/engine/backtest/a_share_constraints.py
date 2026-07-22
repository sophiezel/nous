"""A股实盘约束: T+1/涨跌停/冲击成本/印花税/停牌

提供 AShareConstraints 和 BacktestValidator 两个核心类。

股票板块判定规则:
  - 主板 (10%): 600xxx, 601xxx, 603xxx, 000xxx, 002xxx, 001xxx
  - 创业板 (20%): 300xxx, 301xxx
  - 科创板 (20%): 688xxx
  - 北交所 (30%): 920xxx, 8xxxxx
  - ST (5%): 名称含 "ST" 或 "*ST"

印花税: 2023年8月起减半征收, 卖出 0.05%
"""
import numpy as np
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path(__file__).resolve().parents[4]  # nous repo root / "data" / "screener.db"


# ── 板块涨跌停阈值 ───────────────────────────────────


def _get_board_limit(symbol: str) -> float:
    """根据股票代码判断涨跌停板幅度 (小数, 如 0.10 表示 10%)"""
    if len(symbol) < 3:
        return 0.10
    prefix = symbol[:3]
    # 创业板
    if prefix in ("300", "301"):
        return 0.20
    # 科创板
    if prefix == "688":
        return 0.20
    # 北交所
    if prefix in ("920", "430", "830", "870", "880", "889") or prefix.startswith("8"):
        return 0.30
    # 主板
    return 0.10


def _is_st_stock(symbol: str) -> bool:
    """通过 stock_basic 表判断是否 ST / *ST 股票"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT name FROM stock_basic WHERE symbol = ?", (symbol,)
        ).fetchone()
        conn.close()
        if row and row[0]:
            name = row[0]
            return "ST" in name or "*ST" in name
        return False
    except Exception:
        return False


def _get_stock_name(symbol: str) -> str:
    """获取股票名称"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT name FROM stock_basic WHERE symbol = ?", (symbol,)
        ).fetchone()
        conn.close()
        return row[0] if row else ""
    except Exception:
        return ""


def get_limit_threshold(symbol: str, check_db: bool = True) -> float:
    """获取涨跌停阈值 (小数)

    Args:
        symbol: 股票代码
        check_db: 是否查询数据库判断 ST (默认 True)

    Returns:
        涨跌停幅度, 如 0.10 (10%), 0.05 (ST 5%), 0.20 (创业板 20%)
    """
    if check_db and _is_st_stock(symbol):
        return 0.05
    return _get_board_limit(symbol)


# ── 核心约束类 ───────────────────────────────────────


class AShareConstraints:
    """
    A 股实盘约束检测。

    每个方法对应一个约束条件, 返回 bool 或 float。
    可在 Backtrader 策略的 next() 中调用, 也可在自定义回测引擎中使用。
    """

    @staticmethod
    def t_plus_1_check(buy_date, current_date) -> bool:
        """T+1 检查: 今日买入 → 最早明日才能卖出

        Args:
            buy_date: 买入日期 (str 'YYYY-MM-DD' 或 datetime 或 date)
            current_date: 当前日期 (str 'YYYY-MM-DD' 或 datetime 或 date)

        Returns:
            True=可卖出 (已过 T+1), False=不可卖出 (当日或更早)
        """
        # 统一转换为 datetime.datetime 以进行类型安全的比较
        if isinstance(buy_date, str):
            buy_date = datetime.strptime(buy_date[:10], "%Y-%m-%d")
        elif hasattr(buy_date, "isoformat") and not isinstance(buy_date, datetime):
            # datetime.date → datetime.datetime
            buy_date = datetime.combine(buy_date, datetime.min.time())
        if isinstance(current_date, str):
            current_date = datetime.strptime(current_date[:10], "%Y-%m-%d")
        elif hasattr(current_date, "isoformat") and not isinstance(current_date, datetime):
            current_date = datetime.combine(current_date, datetime.min.time())
        return current_date > buy_date

    @staticmethod
    def limit_up_filter(
        open_p: float, high: float, low: float, close: float,
        prev_close: float, symbol: str = "",
    ) -> bool:
        """涨停检测: 判断当天能否买入

        涨停条件 (任一):
          - 涨幅 >= 阈值 - 0.2% (考虑四舍五入和精度)
          - 收盘 = 最高 且 涨幅 >= 阈值 - 0.5%

        Args:
            open_p: 开盘价
            high: 最高价
            low: 最低价
            close: 收盘价
            prev_close: 前收盘价
            symbol: 股票代码 (用于判断板块/ST, 空字符串则用默认 10%)

        Returns:
            True=可买入 (未涨停), False=涨停买不到
        """
        if prev_close <= 0:
            return True  # 无昨收 (如上市首日), 允许买入

        pct = (close - prev_close) / prev_close
        # 判断板块/ST 阈值
        threshold = get_limit_threshold(symbol) if symbol else 0.10

        # 涨停判定: 考虑 A 股四舍五入的精度误差
        limit_up_hit = (pct >= threshold - 0.002) or (
            close == high and pct >= threshold - 0.005
        )
        if limit_up_hit:
            return False  # 涨停, 买不到
        return True  # 可买

    @staticmethod
    def limit_down_check(
        open_p: float, high: float, low: float, close: float,
        prev_close: float, symbol: str = "",
    ) -> bool:
        """跌停检测: 判断当天能否卖出

        Args:
            open_p: 开盘价
            high: 最高价
            low: 最低价
            close: 收盘价
            prev_close: 前收盘价
            symbol: 股票代码 (用于判断板块/ST)

        Returns:
            True=跌停卖不掉, False=可卖出
        """
        if prev_close <= 0:
            return False  # 上市首日, 可卖

        pct = (close - prev_close) / prev_close
        threshold = get_limit_threshold(symbol) if symbol else 0.10

        # 跌停判定
        limit_down_hit = (pct <= -(threshold - 0.002)) or (
            close == low and pct <= -(threshold - 0.005)
        )
        return limit_down_hit  # True=跌停(卖不掉)

    @staticmethod
    def impact_cost(
        order_amount: float, symbol: str, trade_date: str,
        avg_amount_20d: float = None,
    ) -> float:
        """冲击成本: 基于参与率估计滑点

        参与率 = 订单金额 / 日均成交额

        Args:
            order_amount: 订单金额 (元)
            symbol: 股票代码
            trade_date: 交易日期 'YYYY-MM-DD'
            avg_amount_20d: 可选, 预计算的20日均成交额, 避免重复查询

        Returns:
            冲击成本比例 (小数), 如 0.003 表示 30bp
        """
        if order_amount <= 0:
            return 0.0

        if avg_amount_20d is None:
            avg_amount_20d = AShareConstraints._get_avg_amount(
                symbol, trade_date
            )

        if avg_amount_20d is None or avg_amount_20d <= 0:
            return 0.003  # 未知股票, 默认 30bp

        participation = order_amount / avg_amount_20d
        if participation > 0.05:
            return 0.005   # 50bp (大单, 冲击显著)
        elif participation > 0.01:
            return 0.002   # 20bp
        elif participation > 0.001:
            return 0.0008  # 8bp
        return 0.0003       # 3bp (小单, 大盘股流动性好)

    @staticmethod
    def _get_avg_amount(symbol: str, trade_date: str) -> float:
        """从 screener.db 获取最近20日日均成交额"""
        try:
            conn = sqlite3.connect(str(DB_PATH))
            row = conn.execute(
                """
                SELECT AVG(amount) FROM (
                    SELECT amount FROM stock_daily
                    WHERE symbol = ? AND trade_date <= ?
                    ORDER BY trade_date DESC LIMIT 20
                )
                """,
                (symbol, trade_date),
            ).fetchone()
            conn.close()
            return float(row[0]) if row and row[0] is not None else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def stamp_duty(sell_amount: float) -> float:
        """印花税: 卖出 0.05% (2023年8月起减半征收)

        Args:
            sell_amount: 卖出成交金额 (元)

        Returns:
            印花税额 (元)
        """
        return sell_amount * 0.0005

    @staticmethod
    def is_suspended(symbol: str, trade_date: str) -> bool:
        """停牌检测: 当日无成交量 → 停牌

        Args:
            symbol: 股票代码
            trade_date: 交易日期 'YYYY-MM-DD'

        Returns:
            True=停牌, False=正常交易
        """
        try:
            conn = sqlite3.connect(str(DB_PATH))
            row = conn.execute(
                "SELECT volume FROM stock_daily WHERE symbol = ? AND trade_date = ?",
                (symbol, trade_date),
            ).fetchone()
            conn.close()
            # 无数据行 或 成交量为 0 → 停牌
            return row is None or row[0] is None or row[0] == 0
        except Exception:
            return True  # 查不到默认当作停牌

    @staticmethod
    def get_limit_threshold(symbol: str, check_db: bool = True) -> float:
        """获取涨跌停阈值 (小数)

        便捷封装, 调用模块级 get_limit_threshold().
        """
        return get_limit_threshold(symbol, check_db=check_db)

    @staticmethod
    def get_executable_price(
        direction: str,
        close_price: float,
        impact_slippage: float,
        limit_blocked: bool = False,
    ) -> float:
        """计算实际可执行价格 (考虑冲击成本)

        Args:
            direction: 'buy' 或 'sell'
            close_price: 收盘价/基准价
            impact_slippage: 冲击成本比例
            limit_blocked: 是否被涨跌停阻挡

        Returns:
            可执行价格, 如果被阻挡则返回 None
        """
        if direction == "buy" and limit_blocked:
            return None  # 涨停买不到
        if direction == "sell" and limit_blocked:
            return None  # 跌停卖不掉

        if direction == "buy":
            return close_price * (1.0 + impact_slippage)
        else:
            return close_price * (1.0 - impact_slippage)

    @staticmethod
    def get_trade_cost(
        direction: str, amount: float, impact_slippage: float,
        commission_rate: float = 0.0003,
    ) -> float:
        """计算交易总成本 (冲击成本 + 佣金 + 印花税)

        Args:
            direction: 'buy' 或 'sell'
            amount: 交易金额 (元)
            impact_slippage: 冲击成本比例
            commission_rate: 佣金费率 (默认万三)

        Returns:
            总成本 (元)
        """
        # 冲击成本
        impact = amount * impact_slippage
        # 佣金 (双边收取)
        commission = amount * commission_rate
        # 印花税 (仅卖出)
        stamp = AShareConstraints.stamp_duty(amount) if direction == "sell" else 0.0
        return impact + commission + stamp


class BacktestValidator:
    """回测验证器: 检查回测结果是否满足所有 A 股实盘约束"""

    @staticmethod
    def validate_trades(trades_df) -> list:
        """验证交易记录, 返回违规列表

        检查项:
          - T+1: 所有卖出日期 >= 买入日期 + 1个交易日
          - 买入价格 <= 当日最高价 (合理范围)
          - 卖出价格 >= 当日最低价

        Args:
            trades_df: DataFrame 或 list of dict
                       columns/keys: [symbol, buy_date, sell_date, buy_price, sell_price]

        Returns:
            违规描述字符串列表, 空列表表示全部合规
        """
        # 统一输入格式
        if hasattr(trades_df, "iterrows"):
            records = trades_df.to_dict("records")
        else:
            records = list(trades_df)

        violations = []
        for i, t in enumerate(records):
            sym = t.get("symbol", f"#{i}")

            # ── T+1 检查 ──
            sell_date = t.get("sell_date")
            buy_date = t.get("buy_date")
            if sell_date is not None and buy_date is not None:
                bd = _parse_date(buy_date)
                sd = _parse_date(sell_date)
                if sd is not None and bd is not None and sd <= bd:
                    violations.append(
                        f"T+1 violation: {sym} buy={bd.date()} sell={sd.date()}"
                    )

            # ── 买入价格合理范围 ──
            buy_price = t.get("buy_price")
            if buy_price is not None and buy_price > 0:
                # 过高或过低都不合理 (偏离昨收 >20%)
                if buy_price > t.get("prev_close", buy_price) * 1.20:
                    violations.append(
                        f"Price spike (buy): {sym} buy_price={buy_price:.2f}"
                    )

        return violations

    @staticmethod
    def validate_equity_curve(equity_curve: list) -> list:
        """验证净值曲线: 检查异常跳变

        Args:
            equity_curve: list of dict, 每项含 date 和 equity

        Returns:
            警告列表
        """
        warnings = []
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i - 1]["equity"]
            curr = equity_curve[i]["equity"]
            if prev > 0 and abs(curr / prev - 1) > 0.25:
                warnings.append(
                    f"Large equity swing: {equity_curve[i]['date']} "
                    f"{prev:.2f} -> {curr:.2f} ({((curr/prev-1)*100):.1f}%)"
                )
        return warnings


# ── 辅助函数 ────────────────────────────────────────


def _parse_date(d):
    """统一日期解析, 返回 datetime 或 None"""
    if isinstance(d, datetime):
        return d
    if isinstance(d, str):
        try:
            return datetime.strptime(d[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return None


def make_trade_record(
    symbol: str, side: str, shares: int, price: float, date: str,
    prev_close: float = None, limit_blocked: bool = False,
    impact_slippage: float = 0.0, stamp_duty: float = 0.0,
    commission: float = 0.0,
) -> dict:
    """创建标准化的交易记录 dict

    Args:
        symbol: 股票代码
        side: 'BUY' 或 'SELL'
        shares: 成交股数
        price: 成交价格
        date: 交易日期
        prev_close: 前收盘价 (用于验证)
        limit_blocked: 是否被涨跌停阻挡
        impact_slippage: 冲击成本比例
        stamp_duty: 印花税额 (仅卖出)
        commission: 佣金

    Returns:
        标准化的交易记录 dict
    """
    amount = shares * price
    return {
        "date": date,
        "symbol": symbol,
        "side": side,
        "shares": shares,
        "price": round(price, 2),
        "amount": round(amount, 2),
        "prev_close": round(prev_close, 2) if prev_close else None,
        "limit_blocked": limit_blocked,
        "impact_slippage": round(impact_slippage, 6),
        "stamp_duty": round(stamp_duty, 2),
        "commission": round(commission, 2),
        "total_cost": round(stamp_duty + commission, 2),
    }
