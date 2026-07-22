# 回测系统重构方案 — Purged Walk-Forward + Point-in-Time + CPCV

> 对标: Zipline Reloaded + López de Prado CPCV + Qlib  
> 现状: 事件驱动 ✅ | 幸存偏差 ✅ | Purge ❌ | Embargo ❌ | PIT ❌ | CPCV ❌ | PBO ❌

---

## 一、总架构

```
nous/engine/backtest/
├── engine.py              # 事件驱动主循环 (现有，需增强)
├── data_handler.py        # [新] Point-in-Time数据访问层
├── walk_forward.py        # [重写] Purged Walk-Forward CV
├── cross_validator.py     # [新] Combinatorial Purged CV
├── embargo.py             # [新] Embargo管理
├── universe.py            # [新] 幸存偏差自由股票池 (从survivorship.py升级)
├── validator.py           # [新] 统计验证 (Deflated SR, PBO, CSCV)
├── metrics.py             # 绩效指标 (现有，需增加OOS-only模式)
└── signal_engine.py       # 信号引擎 (现有)
```

**数据流:**

```
PointInTimeData(date=2024-03-15)
  ├── stock_daily WHERE trade_date <= '2024-03-15'
  ├── stock_fundamental WHERE snapshot_date <= '2024-03-15'
  ├── hsgt_market_daily WHERE trade_date <= '2024-03-15'
  └── index_daily WHERE trade_date <= '2024-03-15'

PurgedWalkForward(n_splits=5, embargo_days=21)
  Fold 0: train=[2020..2022Q3] embargo=[21d] test=[2022Q4..2023Q2]
  Fold 1: train=[2020..2023Q1] embargo=[21d] test=[2023Q2..2023Q4]
  ...

CombinatorialPurgedCV(n_splits=6, n_paths=10)
  → 生成10条不同回测路径
  → 每条路径有不同的train/test组合
  → 得到Sharpe分布而非单点
```

---

## 二、Phase 1: Point-in-Time 数据层（基础）

**目标:** 任何查询自动截断到 `as_of_date`，消除最基本的数据泄漏。

### 2.1 DataHandler

```python
class PointInTimeDataHandler:
    """所有数据访问的入口。时间门禁强制执行。"""
    
    def __init__(self, as_of_date: str):
        self.as_of = as_of_date
    
    def get_daily(self, symbol: str, days: int = 120) -> pd.DataFrame:
        """获取 symbol 在 as_of_date 之前（含）的日线"""
        return query(f"""
            SELECT * FROM stock_daily 
            WHERE symbol=? AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT ?
        """, symbol, self.as_of, days)
    
    def get_fundamentals(self, symbol: str) -> dict:
        """获取 as_of_date 之前最新的基本面数据"""
        return query(f"""
            SELECT * FROM stock_fundamental 
            WHERE symbol=? AND snapshot_date <= ?
            ORDER BY snapshot_date DESC LIMIT 1
        """, symbol, self.as_of)
    
    def get_universe(self, market: str = "a") -> list[str]:
        """获取 as_of_date 时存在的股票"""
        return query(f"""
            SELECT DISTINCT symbol FROM stock_daily 
            WHERE trade_date = (
                SELECT MAX(trade_date) FROM stock_daily 
                WHERE trade_date <= ?
            ) AND symbol IN (
                SELECT symbol FROM stock_basic WHERE market=?
            )
        """, self.as_of, market)
```

### 2.2 基本面时间门禁

```python
# 当前（错误）:
SELECT pe, pb, roe FROM stock_fundamental WHERE symbol=?  -- 无日期！

# 修复后:
SELECT pe, pb, roe FROM stock_fundamental 
WHERE symbol=? AND snapshot_date <= ?  -- 只能用当时已披露的
ORDER BY snapshot_date DESC LIMIT 1
```

### 2.3 实施步骤

1. 创建 `data_handler.py`，实现 `PointInTimeDataHandler`
2. 修改 `engine.py` 所有DB查询走 DataHandler
3. 修改 `coarse_filter.py` 和 `trl_recommender.py` 的时间查询
4. 测试：用 `as_of_date='2022-06-30'` 查询2022年Q2年报，应查不到（还没披露）

---

## 三、Phase 2: Purged Walk-Forward（核心）

**目标:** 消除训练/测试重叠，实现真正的OOS评估。

### 3.1 WalkForwardSplitter

```python
@dataclass
class Fold:
    train_start: str
    train_end: str      # 训练数据截止日
    embargo_end: str    # train_end + embargo_days 之后的第一个交易日
    test_start: str     # 测试开始
    test_end: str       # 测试结束

class PurgedWalkForward:
    """López de Prado 风格的 Purged Walk-Forward"""
    
    def __init__(self, n_splits: int = 5, embargo_days: int = 21):
        self.n_splits = n_splits
        self.embargo_days = embargo_days
    
    def split(self, start: str, end: str) -> list[Fold]:
        """生成N折，每折训练集递增，测试集前移。
        
        关键: train_end 到 test_start 之间必须 purge 掉标签重叠的样本。
        Purge逻辑: 如果某个训练样本的标签窗口(test_label_horizon天)
        与测试集第一天重叠，则剔除该训练样本。
        """
        trading_days = self._get_trading_days(start, end)
        fold_size = len(trading_days) // (self.n_splits + 1)
        
        folds = []
        for i in range(self.n_splits):
            train_end_idx = (i + 1) * fold_size
            test_start_idx = train_end_idx + self._embargo_idx(trading_days, train_end_idx)
            test_end_idx = min(test_start_idx + fold_size, len(trading_days))
            
            folds.append(Fold(
                train_start=trading_days[0],
                train_end=trading_days[train_end_idx],
                embargo_end=trading_days[max(0, test_start_idx - 1)],
                test_start=trading_days[test_start_idx],
                test_end=trading_days[test_end_idx - 1],
            ))
        return folds
    
    def purge_train_labels(self, df, train_end, test_start, label_horizon=5):
        """移除训练集中标签窗口与测试集重叠的样本"""
        purge_date = self._subtract_trading_days(test_start, label_horizon)
        return df[df['trade_date'] <= purge_date]
```

### 3.2 集成到回测主循环

```python
class WalkForwardBacktest:
    """Purged Walk-Forward 驱动的回测"""
    
    def run(self, config: BacktestConfig) -> WalkForwardResult:
        splitter = PurgedWalkForward(n_splits=5, embargo_days=21)
        folds = splitter.split(config.start_date, config.end_date)
        
        oos_results = []
        for fold in folds:
            # 1. 训练模型（只用fold.train_start到fold.train_end的数据）
            model = train_model(
                DataHandler(fold.train_end),  # ← 时间门禁
                market=config.market,
            )
            
            # 2. 回测（逐日推进，用DataHandler保证无前视）
            bt = BacktestEngine(config, model, fold.test_start, fold.test_end)
            result = bt.run()
            
            # 3. 只记录OOS
            oos_results.append(result)
        
        # 4. 聚合所有折叠的OOS指标
        return aggregate_oos(oos_results)
```

### 3.3 实施步骤

1. 创建 `walk_forward.py`，实现 `PurgedWalkForward` 和 `WalkForwardBacktest`
2. 实现 `_get_trading_days` 和 `_subtract_trading_days` 工具函数
3. 实现 `purge_train_labels` 标签清洗
4. 集成到 CLI：`nous backtest run --wf --folds 5 --embargo 21`

---

## 四、Phase 3: 统计验证（防过拟合）

**目标:** 区分"真正有效的策略"和"多试几次碰巧好看的曲线"。

### 4.1 Deflated Sharpe Ratio

```python
def deflated_sharpe_ratio(
    observed_sr: float,
    n_trials: int,           # 总共试了多少策略
    sr_std: float,           # 零假设下Sharpe的标准差
    skew: float,             # 收益率偏度
    kurtosis: float,         # 收益率峰度
) -> float:
    """DSR: 考虑多重测试后的真实显著性。
    
    DSR < 0.05 → 策略显著（不是碰运气）
    DSR > 0.10 → 可能过拟合
    """
    from scipy.stats import norm
    
    expected_max = sr_std * (
        (1 - np.euler_gamma) * norm.ppf(1 - 1/n_trials) 
        + np.euler_gamma * norm.ppf(1 - 1/(n_trials * np.e))
    )
    return 1 - norm.cdf((observed_sr - expected_max) / sr_std)
```

### 4.2 PBO (Probability of Backtest Overfitting)

```python
def compute_pbo(cpcv_results: list[BacktestResult]) -> float:
    """López de Prado 的 CSCV 方法。
    
    对所有CPCV路径，比较in-sample排名 vs out-of-sample排名。
    如果最优IS策略在OOS中表现很差 → 过拟合。
    
    PBO < 0.10 → 低过拟合风险
    PBO > 0.20 → 高过拟合风险
    """
    # 生成 Combinatorial CV 路径
    paths = generate_cpcv_paths(n_splits=6, n_paths=10)
    
    # 每条路径计算 IS SR 排名 vs OOS SR 排名
    is_ranks = []
    oos_ranks = []
    for path in paths:
        is_sr = path.in_sample_sharpe()
        oos_sr = path.out_of_sample_sharpe()
        is_ranks.append(rank(is_sr, all_is_sr))
        oos_ranks.append(rank(oos_sr, all_oos_sr))
    
    # 最优IS策略的OOS表现落在下半部分的频率
    best_is_idx = np.argmax([p.in_sample_sharpe() for p in paths])
    best_oos_rank = oos_ranks[best_is_idx]
    
    # 对数几率拟合
    from scipy.stats import norm
    logits = norm.logit([r / len(paths) for r in oos_ranks])
    return np.mean([1 if l <= logits[best_is_idx] else 0 for l in logits])
```

### 4.3 实施步骤

1. 创建 `validator.py`，实现 `deflated_sharpe_ratio` 和 `compute_pbo`
2. 在 `WalkForwardResult` 中嵌入DSR和PBO
3. CLI输出自动包含：`nous backtest run --validate`

---

## 五、Phase 4: CPCV（可选，进阶）

### 5.1 概念

标准Walk-Forward只有1条路径。CPCV生成N条路径，每条使用不同的fold组合作为IS/OOS。

```
Fold:   [1] [2] [3] [4] [5] [6]
Path0:   IS  IS  OOS OOS IS  IS
Path1:   OOS IS  IS  IS  OOS OOS
Path2:   IS  OOS OOS IS  IS  OOS
...
```

这给出一个Sharpe分布，而非单点估计。分布的肥尾程度反映过拟合风险。

### 5.2 实施步骤

1. 创建 `cross_validator.py`
2. 实现 `generate_cpcv_paths(n_splits, n_paths)`
3. 集成到 `WalkForwardBacktest` 的 `--cpcv` 标志

---

## 六、Phase 5: 模型滚动重训

**目标:** 每个Walk-Forward折叠都重新训练模型。

```python
def train_model_for_fold(data_handler: PointInTimeDataHandler, fold: Fold):
    """为单个折叠训练模型"""
    # 1. 计算因子（只用 ≤ fold.train_end 的数据）
    factors = compute_factors(data_handler, fold.train_start, fold.train_end)
    
    # 2. 生成标签（未来N日收益，但purge掉与测试集重叠的）
    labels = generate_labels(data_handler, fold.train_end, label_horizon=5)
    labels = purge_train_labels(labels, fold.train_end, fold.test_start)
    
    # 3. 训练LGB模型
    model = lgb.train(params, lgb.Dataset(factors, label=labels))
    
    return model
```

---

## 七、实施总路线

```
Week 1: Phase 1 PIT数据层 (2天)
  Day 1: data_handler.py + 修改engine.py所有查询
  Day 2: 基本面时间门禁 + 测试验证

Week 2: Phase 2 Purged Walk-Forward (3天)  
  Day 3: PurgedWalkForward + Fold dataclass
  Day 4: purge_train_labels + embargo
  Day 5: WalkForwardBacktest 集成 + CLI

Week 3: Phase 3 统计验证 (2天)
  Day 6: Deflated SR + PBO
  Day 7: CLI集成 + 端到端测试

Week 4: Phase 4+5 CPCV + 模型重训 (3天)
  Day 8: CPCV多路径
  Day 9: 模型滚动重训
  Day 10: 全链路联调
```

**总工期: 10天，4周**

---

## 八、CLI 接口

```bash
# 基本回测
nous backtest run --start 2020-01-01 --end 2025-12-31

# Walk-Forward
nous backtest run --wf --folds 5 --embargo 21

# 带统计验证
nous backtest run --wf --folds 5 --validate

# CPCV (更严格)
nous backtest run --wf --cpcv --paths 10 --validate

# 查看上次结果
nous backtest report
```

---

## 九、成功标准

| 指标 | 当前 | 目标 |
|------|------|------|
| 数据泄漏 | PE无日期过滤 | 全部PIT |
| Purge | 无 | 训练/测试间标签0重叠 |
| Embargo | 无 | 至少21天 |
| Walk-Forward | 分离未集成 | 主循环驱动 |
| OOS评估 | 混入IS数据 | 只看OOS折叠 |
| DSR | 无 | <0.05 |
| PBO | 无 | <0.10 |
| 模型重训 | 静态评分 | 每折重训 |
