# 美团多层反转指标（03690）

## 目标

在 Nous 内为美团港股建立一个**可解释的多层概率信号**，用于观察「趋势延续 vs 反转」共振，而不是给出保证涨跌的点位预测。

CLI：

```bash
nous meituan
nous meituan --json
nous meituan --save
nous meituan --demo   # 合成价格通路验证
```

## 分数约定

- 各层与综合分：`[-100, +100]`
- 正分：偏多头反转 / 上行共振
- 负分：偏空头反转 / 下行共振
- 接近 0：中性或高不确定
- 缺数层 `skip`，权重在其余活跃层上重归一

## 因子层

| 层 | 主要输入 | 方法论镜像 |
|---|---|---|
| technical | RSI/MACD/布林/MA乖离/量比 | 均值回归 + 动量翻转 |
| relative | vs 阿里/京东/腾讯/恒指 N 日超额 | 残差动量 / 相对强弱 |
| capital | 港股通净流入 z、持股占比 | 资金流因子 |
| macro | 非制造 PMI、M2/M1、LPR | 国内流动性与服务业景气 |
| rates_fx | 美债10Y、DXY、USDCNY、VIX | 全球风险偏好与美元流动性 |
| sentiment_policy | `sentiment_cache` | 情绪钟摆（极端恐慌轻度逆向） |
| fundamental | 可选 CSV（GTV/抽成/利润率） | 基本面加速/减速 |
| regime | MA20/MA60 + 波动率 | 趋势 vs 均值回归体制 |

权重见 `config/meituan_reversal.yaml`。

## 数据依赖（本地 `~/nous-data/screener.db`）

- `stock_daily`：`03690` 及同业
- `hsgt_stock_daily`：`03690`
- `index_global_daily`：`TNX/DXY/USDCNY/VIX/HSI/KWEB`
- `macro_lpr` / `macro_pmi` / `macro_m2`
- `sentiment_cache`
- 可选：`config/meituan_fundamentals.csv`（从 example 复制后填真实财报）

## 局限

1. 港股日线在库中窗口有限时，慢线指标会偏短样本。
2. 基本面默认跳过，除非提供 CSV；不做网页财报爬取。
3. 「政策」目前用全市场情绪代理，未接新闻 NLP 事件库。
4. 综合分为线性加权，非黑箱黑盒预测；样本外需自行 `walk-forward` 验证。
5. **不是投资建议。**

## 实现位置

- `src/nous/engine/screening/meituan_reversal.py`
- `config/meituan_reversal.yaml`
- `tests/engine/test_meituan_reversal.py`


## CLI 扩展

```bash
nous meituan --save
nous meituan-alert              # 触及 lean 阈值 exit 2
nous meituan-backtest --hold 5 --max-points 40
```

基本面：`config/meituan_fundamentals.csv`（可从 example 复制后改成真实季报；按 `as_of` 点时）。
回测为事件评估，非完整撮合；阈值默认 YAML 的 lean_long / lean_short。
