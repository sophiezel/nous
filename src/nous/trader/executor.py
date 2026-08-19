"""模拟盘交易系统 — 交易执行器

核心决策引擎：
- 读取荐股报告 → 生成候选清单
- 集合竞价预判（09:24）
- 买入：市价/限价，风控检查，金字塔建仓
- 卖出：止损/止盈/移动止盈/时间止损
- 佣金+印花税+过户费模拟
- 跌停/停牌特殊处理
- 除权除息调整
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Callable
import sys
from pathlib import Path
import traceback

from .account import Account, RiskRules
from .portfolio import Portfolio, Position
from .order import Order, OrderBook, OrderSide, OrderType, OrderStatus, OrderReason
from .risk import RiskEngine, CheckResult, full_pre_trade_check, estimate_slippage
from .state_mgr import StateManager
from nous.core.paths import repo_root

# 卖出纪律（可选模块，加载失败静默跳过）
try:
    from .sell_discipline import check_sell_signals, resolve_sell_signals
    _HAS_SELL_DISCIPLINE = True
except ImportError:
    _HAS_SELL_DISCIPLINE = False


# ============================================================
# 交易费用
# ============================================================

class CommissionCalc:
    """交易费用计算器"""

    # A股费率
    A_COMMISSION_RATE = Decimal("0.0003")  # 佣金 0.03%
    A_MIN_COMMISSION = Decimal("5.00")  # 最低佣金 5元
    A_STAMP_TAX_RATE = Decimal("0.0005")  # 印花税 0.05%（仅卖出）
    A_TRANSFER_RATE = Decimal("0.00002")  # 过户费 0.002%

    # 港股费率
    HK_COMMISSION_RATE = Decimal("0.001")  # 佣金 0.1%
    HK_MIN_COMMISSION = Decimal("100.00")  # 最低佣金 100港元
    HK_STAMP_TAX_RATE = Decimal("0.001")  # 印花税 0.1%（买卖双向）
    HK_STAMP_MIN = Decimal("1.00")  # 最低印花税 1港元
    HK_TRADING_FEE = Decimal("0.00005")  # 交易费 0.005%（港交所）
    HK_LEVY_RATE = Decimal("0.000027")  # 交易征费 0.0027%（SFC）
    HK_SYSTEM_FEE = Decimal("0.50")  # 交易系统使用费 0.5港元/笔
    HK_SETTLEMENT_RATE = Decimal("0.00002")  # 结算费 0.002%
    HK_SETTLEMENT_MIN = Decimal("2.00")  # 最低结算费 2港元
    HK_SETTLEMENT_MAX = Decimal("100.00")  # 最高结算费 100港元

    # 港元/人民币汇率（每日更新）
    _hkd_cny_rate: Decimal = Decimal("0.92")  # 默认 ~0.92

    @classmethod
    def set_hkd_cny_rate(cls, rate: Decimal):
        """设置港元人民币汇率（每日盘前更新）"""
        cls._hkd_cny_rate = rate

    @classmethod
    def get_hkd_cny_rate(cls) -> Decimal:
        """获取港元人民币汇率"""
        return cls._hkd_cny_rate

    @classmethod
    def buy_cost(cls, price: Decimal, shares: int, market: str = "A") -> tuple[Decimal, Decimal, Decimal]:
        """计算买入费用 → (佣金, 印花税, 其他费用) 单位：人民币"""
        amount = price * shares

        if market == "A":
            commission = max(amount * cls.A_COMMISSION_RATE, cls.A_MIN_COMMISSION)
            stamp = Decimal("0")  # A股买入不收印花税
            other = amount * cls.A_TRANSFER_RATE  # 过户费
        else:  # HK
            commission = max(amount * cls.HK_COMMISSION_RATE, cls.HK_MIN_COMMISSION)
            stamp = max(amount * cls.HK_STAMP_TAX_RATE, cls.HK_STAMP_MIN)
            # 其他费用：交易费 + 交易征费 + 系统使用费 + 结算费
            trading_fee = amount * cls.HK_TRADING_FEE
            levy = amount * cls.HK_LEVY_RATE
            settlement = max(min(amount * cls.HK_SETTLEMENT_RATE, cls.HK_SETTLEMENT_MAX), cls.HK_SETTLEMENT_MIN)
            other = trading_fee + levy + cls.HK_SYSTEM_FEE + settlement

        return (
            commission.quantize(Decimal("0.01")),
            stamp.quantize(Decimal("0.01")),
            other.quantize(Decimal("0.01")),
        )

    @classmethod
    def sell_cost(cls, price: Decimal, shares: int, market: str = "A") -> tuple[Decimal, Decimal, Decimal]:
        """计算卖出费用 → (佣金, 印花税, 其他费用) 单位：人民币"""
        amount = price * shares

        if market == "A":
            commission = max(amount * cls.A_COMMISSION_RATE, cls.A_MIN_COMMISSION)
            stamp = amount * cls.A_STAMP_TAX_RATE  # 卖出收印花税
            other = amount * cls.A_TRANSFER_RATE  # 过户费
        else:  # HK
            commission = max(amount * cls.HK_COMMISSION_RATE, cls.HK_MIN_COMMISSION)
            stamp = max(amount * cls.HK_STAMP_TAX_RATE, cls.HK_STAMP_MIN)
            trading_fee = amount * cls.HK_TRADING_FEE
            levy = amount * cls.HK_LEVY_RATE
            settlement = max(min(amount * cls.HK_SETTLEMENT_RATE, cls.HK_SETTLEMENT_MAX), cls.HK_SETTLEMENT_MIN)
            other = trading_fee + levy + cls.HK_SYSTEM_FEE + settlement

        return (
            commission.quantize(Decimal("0.01")),
            stamp.quantize(Decimal("0.01")),
            other.quantize(Decimal("0.01")),
        )


# ============================================================
# 候选股
# ============================================================

@dataclass
class Candidate:
    """推荐候选股"""
    symbol: str
    name: str
    market: str  # A / HK
    strategy: str  # short_term / mid_term / long_term
    sector: str
    theme: str
    score: float  # 荐股报告评分
    buy_low: Decimal = Decimal("0")  # 买入区间下限
    buy_high: Decimal = Decimal("0")  # 买入区间上限
    recommendation_id: str = ""  # 荐股报告日期
    atr: Decimal = Decimal("0")
    daily_amount: float = 0  # 日成交额（用于滑点估算）
    # 模型选股信息 (W5)
    model_score_norm: Optional[float] = None  # 模型预测分 (0-10)
    top_factors: Optional[list[str]] = None  # 贡献最大的因子
    regime: str = ""  # 当前市场状态


# ============================================================
# 决策结果
# ============================================================

@dataclass
class ExecResult:
    """执行结果"""
    action: str  # "buy", "sell", "skip", "wait", "error"
    order_id: str = ""
    symbol: str = ""
    name: str = ""
    price: Decimal = Decimal("0")
    shares: int = 0
    reason: str = ""
    pnl: Decimal = Decimal("0")  # 卖出盈亏
    pnl_pct: Decimal = Decimal("0")
    details: str = ""


# ============================================================
# 执行器
# ============================================================

class Executor:
    """模拟盘交易执行器"""

    def __init__(self, state: StateManager, risk: RiskEngine):
        self.state = state
        self.risk = risk

        # 盘中统计
        self.today_results: list[ExecResult] = []
        self._pending_candidates: list[Candidate] = []

        # 模型上下文缓存 (W5: 盘前一次性加载, 盘中复用)
        self._model_context: Optional[dict] = None
        self._regime_cache: Optional[dict] = None

    # ---- 集合竞价 ----

    def call_auction_assess(
        self,
        candidates: list[Candidate],
        virtual_prices: dict[str, Decimal],
    ) -> list[ExecResult]:
        """09:24 集合竞价预判

        virtual_prices: {symbol: 虚拟开盘价}
        返回：建议挂限价单的候选
        """
        results = []
        for c in candidates:
            vp = virtual_prices.get(c.symbol)
            if not vp or vp == 0:
                continue

            if c.buy_low > 0 and vp >= c.buy_low and vp <= c.buy_high:
                # 虚拟价在买入区间内 → 挂限价单参与集合竞价
                result = self._execute_buy(c, vp, OrderReason.RECOMMENDATION, is_call_auction=True)
                results.append(result)
            elif vp < c.buy_low:
                # 低于买入区间 → 记录等待
                results.append(ExecResult(
                    action="wait", symbol=c.symbol, name=c.name,
                    reason=f"虚拟开盘价 {vp} 低于买入区间 [{c.buy_low}, {c.buy_high}]"
                ))
            else:
                results.append(ExecResult(
                    action="skip", symbol=c.symbol, name=c.name,
                    reason=f"虚拟开盘价 {vp} 高于买入区间 [{c.buy_low}, {c.buy_high}]"
                ))

        return results

    def call_auction_hk(
        self,
        candidates: list[Candidate],
        virtual_prices: dict[str, Decimal],
    ) -> list[ExecResult]:
        """港股集合竞价预判

        港股竞价时段:
        - 早市: 09:00-09:30 (09:20-09:30 不可撤单，09:28-09:30 随机对盘)
        - 午市: 12:00-13:00 (12:45-13:00 不可撤单)

        virtual_prices: {symbol: 港股虚拟开盘价 (Sina API IOPV)}
        返回：建议挂限价单的候选
        """
        results = []
        now = datetime.now().time()
        is_morning = now < dtime(12, 0)

        for c in candidates:
            if c.market != "HK":
                continue

            vp = virtual_prices.get(c.symbol)
            if not vp or vp == 0:
                continue

            # 港股T+0: 竞价买入无锁仓期
            buy_low = c.buy_low if c.buy_low > 0 else vp * Decimal("0.98")  # 无买入区间时默认-2%
            buy_high = c.buy_high if c.buy_high > 0 else vp * Decimal("1.03")  # 无买入区间时默认+3%

            if vp >= buy_low and vp <= buy_high:
                result = self._execute_buy(c, vp, OrderReason.RECOMMENDATION, is_call_auction=True)
                results.append(result)
            elif vp < buy_low:
                results.append(ExecResult(
                    action="wait", symbol=c.symbol, name=c.name,
                    reason=f"HK虚拟开盘价 {vp} 低于买入区间 [{buy_low}, {buy_high}]"
                ))
            else:
                results.append(ExecResult(
                    action="skip", symbol=c.symbol, name=c.name,
                    reason=f"HK虚拟开盘价 {vp} 高于买入区间 [{buy_low}, {buy_high}]"
                ))

        return results

    # ---- 模型选股入口 (W5) ----

    def execute_model_buys(
        self,
        prices: dict[str, Decimal],
        index_changes: dict[str, Decimal],
        top_n: int = 10,
        max_buys: int = 3,
        dt_str: str = "",
    ) -> list[ExecResult]:
        """执行模型选股买入 (替代荐股报告候选)

        流程:
        1. 调用模型预测获取 TOP N
        2. 过滤: 排除已持仓、停牌、涨停的
        3. 选择 TOP {max_buys} 买入
        4. 每笔调用 _execute_buy (复用 ATR/风控)

        Args:
            prices: {symbol: 当前价格}
            index_changes: 指数涨跌幅
            top_n: 模型候选池大小
            max_buys: 实际买入数量
            dt_str: 日期字符串

        Returns:
            list[ExecResult]
        """
        ctx = self._get_model_context()
        if not ctx:
            return [ExecResult(
                action="skip", reason="模型预测不可用, 降级到原有选股逻辑"
            )]

        recs = ctx["recommendations"]
        regime = ctx["regime"]
        model_scores = ctx["model_scores"]

        if not recs:
            return [ExecResult(
                action="skip", reason="模型预测为空"
            )]

        results = []
        bought = 0

        for r in recs:
            if bought >= max_buys:
                break
            sym = r["symbol"]

            # 过滤: 已持仓
            if self.state.portfolio.has(sym):
                continue

            # 过滤: 无价格
            price = prices.get(sym)
            if not price or price == 0:
                continue

            # 构建候选 (使用模型评分)
            c = Candidate(
                symbol=sym,
                name=r.get("name", ""),
                market="A",
                strategy="short_term",
                sector="",
                theme="",
                score=float(r.get("model_score_norm", 5)),
                model_score_norm=float(r.get("model_score_norm", 5)),
                top_factors=[],  # 当前预测未输出因子贡献
                regime=regime.get("regime", "SIDEWAYS") if regime else "",
            )
            # 价格在买入区间外, 设置合理区间 (价格 ± 0.5%)
            c.buy_low = (price * Decimal("0.995")).quantize(Decimal("0.01"))
            c.buy_high = (price * Decimal("1.005")).quantize(Decimal("0.01"))

            result = self._execute_buy(c, price, OrderReason.RECOMMENDATION, index_changes=index_changes)
            results.append(result)
            if result.action == "buy":
                bought += 1

        if not results:
            results.append(ExecResult(
                action="skip", reason="模型选股: 无适合买入标的"
            ))

        return results

    def _get_model_context(self) -> Optional[dict]:
        """获取模型预测上下文 (带缓存, 盘前加载一次)

        如果模型不可用, 返回 None (降级到原有逻辑)。

        Returns:
            dict with keys:
              - recommendations: list[dict] 模型推荐列表
              - regime: dict 当前市场状态
              - model_scores: dict[str, float] {symbol: score_norm}
        """
        if self._model_context is not None:
            return self._model_context

        try:
            sys.path.insert(0, str(repo_root()))
            from src.qlib_research.predict import get_model_recommendations
            from src.qlib_research.market_regime import predict_current_regime

            # 获取模型推荐
            recs = get_model_recommendations(top_n=20)

            # 获取市场状态
            regime = None
            try:
                regime = predict_current_regime()
            except Exception as e:
                print(f"  [executor] 市场状态预测失败: {e}", file=sys.stderr)
                traceback.print_exc()

            # 构建 symbol→score 索引
            model_scores = {}
            for r in recs:
                sym = r.get("symbol", "")
                score = r.get("model_score_norm")
                if sym and score is not None:
                    model_scores[sym] = float(score)

            self._model_context = {
                "recommendations": recs,
                "regime": regime,
                "model_scores": model_scores,
            }
            print(f"  [executor] 模型预测已加载: {len(recs)} 只候选, 市场状态={regime.get('regime','N/A') if regime else 'N/A'}", file=sys.stderr)
            return self._model_context
        except ImportError as e:
            print(f"  [executor] 模型模块未安装, 降级到原有逻辑: {e}", file=sys.stderr)
            self._model_context = {}  # 标记为已尝试但不可用
            return None
        except Exception as e:
            print(f"  [executor] 模型预测失败, 降级到原有逻辑: {e}", file=sys.stderr)
            traceback.print_exc()
            self._model_context = {}
            return None

    # ---- 买入执行 ----

    def execute_open_buys(
        self,
        candidates: list[Candidate],
        prices: dict[str, Decimal],  # {symbol: 买一价}
        index_changes: dict[str, Decimal],
        dt_str: str = "",
    ) -> list[ExecResult]:
        """执行首批买入（09:32 首次买入 / 盘中补买）

        prices: 当前买一价（或最新价降级）
        """
        results = []
        dt = dt_str or datetime.now().isoformat()[:10]

        for c in candidates:
            price = prices.get(c.symbol)
            if not price or price == 0:
                results.append(ExecResult(
                    action="skip", symbol=c.symbol, name=c.name,
                    reason="无实时价格"
                ))
                continue

            # 价格在买入区间外 → 跳过
            if c.buy_low > 0 and price > c.buy_high:
                results.append(ExecResult(
                    action="skip", symbol=c.symbol, name=c.name,
                    reason=f"价格 {price} 高于买入区间上限 {c.buy_high}"
                ))
                continue

            result = self._execute_buy(c, price, OrderReason.RECOMMENDATION, index_changes=index_changes)
            results.append(result)

        return results

    def execute_pyramid_add(
        self,
        prices: dict[str, Decimal],
        index_changes: dict[str, Decimal],
    ) -> list[ExecResult]:
        """执行金字塔加仓（盘中调用）

        对已有持仓且未完成全部建仓阶段的标的，检查加仓条件。
        """
        results = []
        for pos in self.state.portfolio.positions.values():
            max_stages = 2 if pos.strategy == "short_term" else 3
            if pos.pyramid_stage >= max_stages:
                continue

            price = prices.get(pos.symbol)
            if not price or price == 0:
                continue

            # 加仓条件：
            # 短线第2批：价格未跌破首笔成本-2%
            # 中线第2批：未触发止损且量能持续（简化为价格未破首笔-3%）
            # 长线后续：趋势延续
            if pos.strategy == "short_term" and pos.pyramid_stage == 1:
                min_price = pos.entry_price * Decimal("0.98")
                if price < min_price:
                    results.append(ExecResult(
                        action="skip", symbol=pos.symbol, name=pos.name,
                        reason=f"金字塔加仓条件不满足：{price} < {min_price}（成本-2%）"
                    ))
                    continue

            plan = self.risk.get_pyramid_plan(
                pos.shares + 100,  # 占位
                pos.strategy,
            )

            # 构建临时 Candidate
            c = Candidate(
                symbol=pos.symbol, name=pos.name, market=pos.market,
                strategy=pos.strategy, sector=pos.sector, theme=pos.theme,
                score=0, recommendation_id=pos.recommendation_id,
            )

            result = self._execute_buy(
                c, price, OrderReason.PYRAMID_ADD,
                pyramid_stage=pos.pyramid_stage + 1,
                index_changes=index_changes,
            )
            results.append(result)

        return results

    def _execute_buy(
        self,
        candidate: Candidate,
        price: Decimal,
        reason: OrderReason,
        is_call_auction: bool = False,
        pyramid_stage: int = 1,
        index_changes: dict[str, Decimal] = None,
    ) -> ExecResult:
        """执行一笔买入（核心方法）"""
        if index_changes is None:
            index_changes = {"sh000001": Decimal("0")}

        # 1. 风控检查
        check = full_pre_trade_check(
            risk=self.risk,
            symbol=candidate.symbol,
            market=candidate.market,
            strategy=candidate.strategy,
            sector=candidate.sector,
            theme=candidate.theme,
            price=price,
            atr=candidate.atr,
            portfolio=self.state.portfolio,
            account=self.state.account,
            index_changes=index_changes,
        )

        if not check.passed:
            return ExecResult(
                action="skip", symbol=candidate.symbol, name=candidate.name,
                reason=f"风控拦截: {check.reason}"
            )

        # 2. 确定仓位（ATR 动态计算）
        shares = check.suggested_shares
        
        # Soul L4+L5: 琼斯趋势过滤 + 仓位上限
        try:
            _screener_src = str(repo_root() / "src")
            if _screener_src not in sys.path:
                sys.path.insert(0, _screener_src)
            from soul_engine import jones_trend_filter, calc_position_weight, assign_channel
            
            trend = jones_trend_filter(candidate.symbol)
            if not trend["passed"]:
                return ExecResult(
                    action="skip", symbol=candidate.symbol, name=candidate.name,
                    reason=f"Soul L4: 琼斯趋势过滤未通过 - {trend.get('reason','价格<MA200')}"
                )
            
            # 仓位上限 = min(ATR计算, Soul计算)
            soul_weight = calc_position_weight(
                conviction=trend.get("trend_strength", 50),
                entry_price=float(price),
                stop_price=float(price) * 0.95,
                portfolio_value=float(self.state.account.total_asset(
                    self.state.portfolio.total_market_value)),
                channel=assign_channel(candidate.symbol, {}),
            )
            max_soul_shares = int(soul_weight * float(self.state.account.total_asset(
                self.state.portfolio.total_market_value)) / float(price))
            if max_soul_shares > 0:
                shares = min(shares, max_soul_shares)
        except ImportError:
            pass  # soul_engine 不可用时不阻塞
        if pyramid_stage > 1:
            # 金字塔加仓：按阶段比例
            plan = self.risk.get_pyramid_plan(shares * 3, candidate.strategy)
            if pyramid_stage - 1 < len(plan.stages):
                shares = plan.stages[pyramid_stage - 1]
            else:
                shares = 0

        if shares <= 0:
            return ExecResult(
                action="skip", symbol=candidate.symbol, name=candidate.name,
                reason="计算仓位为0"
            )

        # 3. 计算滑点
        slippage_pct = estimate_slippage(candidate.daily_amount, price)
        slippage_cost = price * shares * slippage_pct

        # 4. 计算费用
        commission, stamp_tax, transfer = CommissionCalc.buy_cost(price, shares, candidate.market)
        total_fee = commission + stamp_tax + transfer + slippage_cost
        total_cost = price * shares + total_fee

        # 5. 资金检查
        if not self.state.account.can_buy(total_cost):
            return ExecResult(
                action="skip", symbol=candidate.symbol, name=candidate.name,
                reason=f"资金不足：需要 {total_cost}，可用 {self.state.account.available_cash}"
            )

        # 6. 冻结资金→成交
        self.state.account.reserve_cash(total_cost)

        order = Order(
            symbol=candidate.symbol,
            name=candidate.name,
            market=candidate.market,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT if is_call_auction else OrderType.MARKET,
            price=price,
            shares=shares,
            reason=reason,
            strategy=candidate.strategy,
            sector=candidate.sector,
            theme=candidate.theme,
            recommendation_id=candidate.recommendation_id,
        )
        order.fill(price, commission=commission, stamp_tax=stamp_tax, slippage=slippage_cost)

        # 扣减现金
        self.state.account.commit_buy(total_cost)

        # 记录冷却期
        self.risk.record_buy(candidate.sector)

        # 记录订单
        self.state.orders.submit(order)
        self.state.orders.fill(order.order_id, price,
                               commission=commission, stamp_tax=stamp_tax,
                               slippage=slippage_cost)

        # 7. 更新持仓
        today = date.today().isoformat()
        lock_date = (date.today() + timedelta(days=1)).isoformat()
        existing = self.state.portfolio.get(candidate.symbol)
        if existing:
            # 加仓：更新均价和总成本
            old_cost = existing.total_cost
            old_shares = existing.shares
            existing.shares += shares
            existing.total_cost = old_cost + total_cost
            existing.entry_price = (existing.total_cost / existing.shares).quantize(Decimal("0.01"))
            existing.pyramid_stage = pyramid_stage
            # 加仓不改变 T+1 锁定状态（原持仓可能已解锁）
            if existing.market == "A" and existing.is_locked_today:
                existing.locked_until = max(existing.locked_until, lock_date)
        else:
            pos = Position(
                symbol=candidate.symbol,
                name=candidate.name,
                market=candidate.market,
                strategy=candidate.strategy,
                sector=candidate.sector,
                theme=candidate.theme,
                entry_price=price,
                entry_date=today,
                shares=shares,
                total_cost=total_cost,
                current_price=price,
                is_locked_today=(candidate.market == "A"),
                locked_until=lock_date if candidate.market == "A" else "",
                recommendation_id=candidate.recommendation_id,
                pyramid_stage=pyramid_stage,
            )
            # 设置初始止损止盈价
            sl_pct = self.state.risk_rules.stop_loss_pct[pos.strategy]
            tp_pct = self.state.risk_rules.take_profit_pct[pos.strategy]
            pos.stop_loss_price = (price * (Decimal("1") + sl_pct)).quantize(Decimal("0.01"))
            pos.take_profit_price = (price * (Decimal("1") + tp_pct)).quantize(Decimal("0.01"))
            self.state.portfolio.add(pos)

        self.state.save()

        # W5: 模型交易日志
        try:
            from .model_trade_log import ModelTradeLogger
            model_score = candidate.model_score_norm or 0
            top_factors = candidate.top_factors or []
            regime = candidate.regime or ""
            # 如果候选本身没有模型分, 尝试从缓存获取
            if not candidate.model_score_norm and self._model_context:
                symbol_score = self._model_context.get("model_scores", {}).get(candidate.symbol)
                if symbol_score is not None:
                    model_score = symbol_score
                if not regime and self._model_context.get("regime"):
                    regime = self._model_context["regime"].get("regime", "")
            ModelTradeLogger.log_buy(
                symbol=candidate.symbol,
                buy_price=float(price),
                model_score=model_score,
                regime=regime,
                top_factors=top_factors,
                amount=float(total_cost),
                strategy=candidate.strategy,
                sector=candidate.sector,
                name=candidate.name,
            )
        except Exception as e:
            print(f"  [executor] 模型交易日志记录失败: {e}", file=sys.stderr)

        return ExecResult(
            action="buy",
            order_id=order.order_id,
            symbol=candidate.symbol,
            name=candidate.name,
            price=price,
            shares=shares,
            reason=f"{reason.value} (阶段{pyramid_stage})",
            details=f"总成本={total_cost} 滑点={slippage_cost.quantize(Decimal('0.01'))}",
        )

    # ---- 卖出执行 ----

    def execute_exits(
        self,
        prices: dict[str, Decimal],
    ) -> list[ExecResult]:
        """扫描所有持仓，执行止损/止盈/移动止盈/时间止损

        prices: {symbol: 最新价}
        """
        results = []

        for symbol in list(self.state.portfolio.positions.keys()):
            pos = self.state.portfolio.positions.get(symbol)
            if not pos:
                continue
            price = prices.get(symbol)
            if not price or price == 0:
                continue

            # 更新价格
            pos.update_price(price)

            # 检查是否可卖出
            sell_check = self.risk.can_sell_position(pos, "exit_signal")
            if not sell_check.passed:
                continue

            # 评估退出信号
            signals = self.risk.evaluate_exit_signals(pos)
            
            # 卖出纪律增强（可选模块）
            sell_disc_signals = []
            if _HAS_SELL_DISCIPLINE:
                try:
                    market_data = {'current_price': float(price)}
                    disc_signals = check_sell_signals(pos, market_data)
                    if disc_signals:
                        resolved = resolve_sell_signals(disc_signals)
                        if resolved:
                            print(f"  [sell_discipline] {pos.symbol} {pos.name}: {resolved.reason} ({resolved.urgency})", file=sys.stderr)
                            sell_disc_signals.append(resolved.reason)
                except Exception as e:
                    print(f"  [sell_discipline] check failed for {pos.symbol}: {e}", file=sys.stderr)
            
            if not signals and not sell_disc_signals:
                continue
            
            # 合并信号：取最高优先级
            if sell_disc_signals:
                signals = list(signals) + sell_disc_signals if signals else sell_disc_signals

            # 取最高优先级信号
            signal = signals[0]

            # 执行卖出
            result = self._execute_sell(pos, price, signal)
            results.append(result)

        self.state.save()
        return results

    def _execute_sell(
        self,
        pos: Position,
        price: Decimal,
        signal: str,
    ) -> ExecResult:
        """执行一笔卖出"""

        reason_map = {
            "stop_loss": OrderReason.STOP_LOSS,
            "take_profit": OrderReason.TAKE_PROFIT,
            "trailing_stop": OrderReason.TRAILING_STOP,
            "time_stop": OrderReason.TIME_STOP,
        }
        reason = reason_map.get(signal, OrderReason.MANUAL)

        # 计算费用
        commission, stamp_tax, transfer = CommissionCalc.sell_cost(
            price, pos.shares, pos.market
        )
        total_fee = commission + stamp_tax + transfer
        proceeds = price * pos.shares - total_fee

        # 计算盈亏
        pnl = proceeds - pos.total_cost
        pnl_pct = (pnl / pos.total_cost).quantize(Decimal("0.0001")) if pos.total_cost > 0 else Decimal("0")

        # 创建卖单
        order = Order(
            symbol=pos.symbol,
            name=pos.name,
            market=pos.market,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            price=price,
            shares=pos.shares,
            reason=reason,
            strategy=pos.strategy,
        )
        order.fill(price, commission=commission, stamp_tax=stamp_tax)

        # 记录订单
        self.state.orders.submit(order)
        self.state.orders.fill(order.order_id, price,
                               commission=commission, stamp_tax=stamp_tax)

        # 回笼现金
        self.state.account.receive_sell(proceeds, pnl)

        # 记录冷却
        self.risk.record_sell()

        # 移除持仓
        self.state.portfolio.remove(pos.symbol)

        # W5: 模型交易日志 (卖出)
        try:
            from .model_trade_log import ModelTradeLogger, read_logs_for_symbol
            # 从日志读取该股票的买入记录, 关联盈亏
            buy_records = read_logs_for_symbol(pos.symbol, months=2)
            buy_record = None
            for r in reversed(buy_records):
                if r.get("event") == "buy":
                    buy_record = r
                    break
            ModelTradeLogger.log_sell(
                symbol=pos.symbol,
                sell_price=float(price),
                buy_record=buy_record,
                reason=signal,
            )
        except Exception as e:
            print(f"  [executor] 卖出日志记录失败: {e}", file=sys.stderr)

        return ExecResult(
            action="sell",
            order_id=order.order_id,
            symbol=pos.symbol,
            name=pos.name,
            price=price,
            shares=pos.shares,
            reason=f"{reason.value} (信号={signal})",
            pnl=pnl,
            pnl_pct=pnl_pct,
            details=f"盈亏={pnl} ({pnl_pct:.2%}) 手续费={total_fee}",
        )

    # ---- 每日维护 ----

    def daily_maintenance(self):
        """每日维护：持仓天数+1，检查除权除息"""
        self.state.portfolio.increment_all_days()
        self._reset_daily_tracking()

    def _reset_daily_tracking(self):
        """重置当日追踪"""
        self.today_results.clear()
        # RiskEngine 内部会跨日自动重置

    # ---- 港股收盘更新 ----

    def update_hk_close(self, prices: dict[str, Decimal]):
        """港股收盘更新价格"""
        for symbol, pos in self.state.portfolio.positions.items():
            if pos.market == "HK" and symbol in prices:
                pos.update_price(prices[symbol])
        self.state.save()


# ============================================================
# 荐股报告解析（简易版）
# ============================================================

def parse_recommendations(report_path: str) -> list[Candidate]:
    """从荐股报告 Markdown 解析候选清单

    支持两种格式：
    1. 新格式（表格型）：### #1 跨境通（002640） + 下方表格 | 板块 | ... | 周期 | ...
    2. 旧格式（内联型）：### 1. 跨境通（002640）— 板块 【策略】

    返回 Candidate 列表（需外部补充 price/atr/daily_amount）
    """
    candidates = []
    try:
        with open(report_path) as f:
            content = f.read()
    except FileNotFoundError:
        return candidates

    import re

    # 统一匹配函数：解析一个板块内的候选股
    def _parse_section(section_text: str, market: str, symbol_len: int) -> list[Candidate]:
        result = []
        # 匹配标题行：### #1 名称（代码） 或 ### 1. 名称（代码）— 板块 【策略】
        heading_re = re.compile(
            r'###\s+(?:#)?\d+\.?\s*(.+?)\s*[（(](\d{' + str(symbol_len) + r'})[）)]'
        )
        for m in heading_re.finditer(section_text):
            name, symbol = m.group(1), m.group(2)
            heading_end = m.end()

            # 尝试从标题后缀提取板块和策略（旧格式）
            suffix_match = re.match(
                r'\s*[—\-]\s*(.+?)\s*【(.+?)】',
                section_text[heading_end:heading_end + 80]
            )
            if suffix_match:
                sector, strategy_raw = suffix_match.group(1), suffix_match.group(2)
                strategy = map_strategy(strategy_raw)
            else:
                # 新格式：从下方表格提取
                sector = ""
                strategy = "short_term"
                # 取标题后2000字符查找表格行
                table_block = section_text[heading_end:heading_end + 2000]
                sector_m = re.search(r'\|\s*板块\s*\|\s*(.+?)\s*\|', table_block)
                if sector_m:
                    sector = sector_m.group(1).strip()
                cycle_m = re.search(r'\|\s*周期\s*\|\s*(\S+)', table_block)
                if cycle_m:
                    strategy = map_strategy(cycle_m.group(1))

            # 提取评分
            score = 7.0
            score_block = section_text[heading_end:heading_end + 2000]
            score_m = re.search(r'\|\s*评分\s*\|\s*★\s*([\d.]+)', score_block)
            if score_m:
                score = float(score_m.group(1))

            result.append(Candidate(
                symbol=symbol, name=name.strip(), market=market,
                strategy=strategy, sector=sector,
                theme=infer_theme(sector),
                score=score,
                recommendation_id=extract_date(report_path),
            ))
        return result

    # 匹配 A股推荐
    a_section = re.search(r'## 二、A股推荐.*?(?=## 三、港股推荐)', content, re.DOTALL)
    if a_section:
        candidates.extend(_parse_section(a_section.group(0), "A", 6))

    # 匹配港股推荐
    hk_section = re.search(r'## 三、港股推荐.*?(?=## 四、风险提示)', content, re.DOTALL)
    if hk_section:
        candidates.extend(_parse_section(hk_section.group(0), "HK", 5))

    return candidates


def map_strategy(raw: str) -> str:
    """映射策略名称"""
    raw = raw.strip()
    # "短中线"中"短"和"线"不连续，不能依赖 substring 匹配
    if "短" in raw and "线" in raw:
        return "short_term"
    elif "中" in raw and "线" in raw:
        return "mid_term"
    elif "长" in raw and "线" in raw:
        return "long_term"
    return "short_term"


def infer_theme(sector: str) -> str:
    """从板块反推主线"""
    theme_map = {
        "PCB/电子": "AI产业链",
        "PCB": "AI产业链",
        "电子": "AI产业链",
        "互联网": "AI产业链",
        "短视频": "AI产业链",
        "医药": "医药健康",
        "体外诊断": "医药健康",
        "IVD": "医药健康",
        "创新药": "医药健康",
        "证券": "金融周期",
        "电气设备": "新能源",
        "工业自动化": "新能源",
        "新能源车": "新能源",
        "电动工具": "制造周期",
        "机械": "制造周期",
        "制造": "制造周期",
    }
    for key, theme in theme_map.items():
        if key in sector:
            return theme
    return sector


def extract_date(path: str) -> str:
    """从路径提取日期"""
    import re
    match = re.search(r'(\d{4}-\d{2}-\d{2})', path)
    return match.group(1) if match else ""
